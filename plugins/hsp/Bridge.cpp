#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <objidl.h>
#include <gdiplus.h>
#include <algorithm>
#include <memory>
#include <vector>
#include "hsp3plugin.h"
#include "Wire.hpp"

namespace {
HSPEXINFO* api=nullptr;
HSPCTX* context=nullptr;
void (*original_message)(HSPCTX*)=nullptr;
std::unique_ptr<gameqa::Core> core;
std::wstring directory,executable;
std::string executable_utf8;
DWORD process_id=0;
ULONG_PTR gdiplus_token=0;
bool ticking=false,mouse_sending=false,updates_started=false;
double last_status=0,last_guard=0;
HWND pressed_button=nullptr;
POINT button_point={0,0};
struct Patch { ULONG_PTR* slot;ULONG_PTR original,replacement; };
std::vector<Patch> patches;
using AsyncKey=SHORT (WINAPI*)(int);
using Cursor=BOOL (WINAPI*)(LPPOINT);
using ActiveWindow=HWND (WINAPI*)();
AsyncKey original_key=nullptr;
Cursor original_cursor=nullptr;
ActiveWindow original_active=nullptr;

std::wstring wide(const std::string& s) {
    if(s.empty()) return L"";
    int size=MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,s.data(),static_cast<int>(s.size()),nullptr,0);
    if(!size) throw std::runtime_error("invalid_utf8");
    std::wstring w(size,L'\0');MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,s.data(),static_cast<int>(s.size()),&w[0],size);return w;
}
std::string utf8(const std::wstring& s) {
    if(s.empty()) return "";
    int size=WideCharToMultiByte(CP_UTF8,WC_ERR_INVALID_CHARS,s.data(),static_cast<int>(s.size()),nullptr,0,nullptr,nullptr);
    if(!size) throw std::runtime_error("invalid_path");
    std::string b(size,'\0');WideCharToMultiByte(CP_UTF8,WC_ERR_INVALID_CHARS,s.data(),static_cast<int>(s.size()),&b[0],size,nullptr,nullptr);return b;
}
std::wstring environment(const wchar_t* key) {
    DWORD n=GetEnvironmentVariableW(key,nullptr,0);
    if(!n || n>32768) return L"";
    std::wstring value(n,L'\0');GetEnvironmentVariableW(key,&value[0],n);value.resize(n-1);return value;
}
double now() {
    FILETIME f;GetSystemTimeAsFileTime(&f);ULARGE_INTEGER t;t.LowPart=f.dwLowDateTime;t.HighPart=f.dwHighDateTime;
    return static_cast<double>(t.QuadPart)/10000000.0-11644473600.0;
}
std::wstring path(const std::string& name) { return directory+L"/"+wide(name); }
gameqa::Fields read(const std::string& name) {
    HANDLE file=CreateFileW(path(name+".ini").c_str(),GENERIC_READ,FILE_SHARE_READ|FILE_SHARE_WRITE|FILE_SHARE_DELETE,nullptr,OPEN_EXISTING,0,nullptr);
    if(file==INVALID_HANDLE_VALUE) {
        if(GetLastError()==ERROR_FILE_NOT_FOUND) return {};
        throw std::runtime_error("control_read_failed");
    }
    LARGE_INTEGER size;
    if(!GetFileSizeEx(file,&size) || size.QuadPart<0 || size.QuadPart>65536) { CloseHandle(file);throw std::runtime_error("control_file_size"); }
    std::string text(static_cast<size_t>(size.QuadPart),'\0');DWORD count=0;
    BOOL ok=ReadFile(file,text.empty()?nullptr:&text[0],static_cast<DWORD>(text.size()),&count,nullptr);CloseHandle(file);
    if(!ok || count!=text.size()) throw std::runtime_error("control_read_incomplete");
    return gameqa::ini(text);
}
void write(const std::string& name,const std::string& bytes,bool state=false) {
    const auto target=path(name),temporary=target+L".part";
    HANDLE f=INVALID_HANDLE_VALUE;
    for(int i=0;i<8;++i) {
        f=CreateFileW((state?target:temporary).c_str(),GENERIC_WRITE,state?0:FILE_SHARE_READ,nullptr,CREATE_ALWAYS,0,nullptr);
        if(f!=INVALID_HANDLE_VALUE) break;
        Sleep(1);
    }
    if(f==INVALID_HANDLE_VALUE) throw std::runtime_error("control_write_open");
    DWORD count=0;BOOL ok=WriteFile(f,bytes.data(),static_cast<DWORD>(bytes.size()),&count,nullptr);CloseHandle(f);
    if(!ok || count!=bytes.size()) throw std::runtime_error("control_write_incomplete");
    if(!state) {
        for(int i=0;i<8;++i) {
            if(MoveFileExW(temporary.c_str(),target.c_str(),MOVEFILE_REPLACE_EXISTING|MOVEFILE_WRITE_THROUGH)) return;
            Sleep(1);
        }
        throw std::runtime_error("control_publish_failed");
    }
}
BMSCR* screen() { return api?static_cast<BMSCR*>(api->HspFunc_getbmscr(0)):nullptr; }
bool dimensions(HWND& hwnd,int& width,int& height) {
    auto* s=screen();RECT r;
    if(!s || !s->hwnd || !GetClientRect(s->hwnd,&r)) return false;
    hwnd=s->hwnd;width=r.right;height=r.bottom;
    return width>0 && height>0 && static_cast<long long>(width)*height<=16777216;
}
std::string status() {
    HWND hwnd=nullptr;int width=0,height=0;bool ready=dimensions(hwnd,width,height);
    std::ostringstream o;
    o<<"{\"protocol\":1,\"session_id\":"<<gameqa::quote(core->session)<<",\"pid\":"<<process_id
     <<",\"exe_path\":"<<gameqa::quote(executable_utf8)<<",\"engine\":\"hsp3\",\"updated_at\":"<<std::setprecision(17)<<now()
     <<",\"frame\":"<<core->frame<<",\"paused\":"<<(core->paused?"true":"false")
     <<",\"held\":["<<(core->held.empty()?"":gameqa::quote(core->held))<<"],\"input_ready\":"<<(ready?"true":"false")
     <<",\"screen\":["<<width<<','<<height<<"],\"command_id\":"<<(core->active?gameqa::quote(core->command.id):"null")
     <<",\"error\":"<<(core->error.empty()?"null":gameqa::quote(core->error))
     <<",\"capabilities\":[\"move\",\"confirm\",\"cancel\",\"wait\",\"press\",\"release\",\"click\"]}";
    return o.str();
}
void publish_status(bool force=false) {
    double t=now();if(force || t-last_status>=.1) { write("status.json",status(),true);last_status=t; }
}
void receipt() {
    auto& r=core->receipt;if(!r.ready) return;
    std::ostringstream o;
    o<<"{\"protocol\":1,\"session_id\":"<<gameqa::quote(core->session)<<",\"id\":"<<gameqa::quote(r.request.id)
     <<",\"ok\":"<<(r.ok?"true":"false")<<",\"consumed\":"<<(r.consumed?"true":"false")
     <<",\"frame\":"<<r.frame<<",\"capture_frame\":"<<r.frame<<",\"paused\":"<<(core->paused?"true":"false")
     <<",\"error\":"<<(r.error.empty()?"null":gameqa::quote(r.error))<<",\"screenshot\":"<<(r.screenshot.empty()?"null":gameqa::quote(r.screenshot))
     <<",\"state\":"<<status()<<'}';
    publish_status(true);write("results/"+r.request.id+".json",o.str());core->acknowledge();
}
double lease() {
    auto f=read("lease");return gameqa::get(f,"session_id")==core->session?gameqa::number(f,"expires_at"):0;
}
std::string generation() {
    auto f=read("pause");return gameqa::get(f,"session_id")==core->session?gameqa::get(f,"id"):core->pause;
}
void cancel_mouse() {
    if(!pressed_button) return;
    HWND target=pressed_button;pressed_button=nullptr;
    if(IsWindow(target)) SendMessageW(target,WM_CANCELMODE,0,0);
    if(GetCapture()==target) ReleaseCapture();
}
void native_mouse() {
    if(core->paused) { cancel_mouse();return; }
    const bool down=core->held=="mouse0";
    if(!down && pressed_button) {
        HWND target=pressed_button;pressed_button=nullptr;
        if(IsWindow(target)) SendMessageW(target,WM_LBUTTONUP,0,MAKELPARAM(button_point.x,button_point.y));
        return;
    }
    if(!down || pressed_button || !core->active || core->command.action!="click") return;
    auto* s=screen();if(!s || !s->hwnd) return;
    POINT p={core->command.x,core->command.y};
    HWND target=ChildWindowFromPointEx(s->hwnd,p,CWP_SKIPINVISIBLE|CWP_SKIPDISABLED|CWP_SKIPTRANSPARENT);
    DWORD owner=0;GetWindowThreadProcessId(target,&owner);
    wchar_t name[32]={0};GetClassNameW(target,name,32);
    if(owner!=process_id || _wcsicmp(name,L"Button")) return;
    MapWindowPoints(s->hwnd,target,&p,1);pressed_button=target;button_point=p;
    SendMessageW(target,WM_MOUSEMOVE,0,MAKELPARAM(p.x,p.y));
    SendMessageW(target,WM_LBUTTONDOWN,MK_LBUTTON,MAKELPARAM(p.x,p.y));
    if(SendMessageW(target,BM_GETSTATE,0,0)&BST_PUSHED) core->observe("mouse0");
}
void guard() {
    if(!core || ticking || !updates_started) return;
    double t=now();if(t-last_guard<.01) return;last_guard=t;
    try {
        const auto p=generation();
        if(p!=core->pause) { core->pause=p;core->fail("manual_pause"); }
        if(!core->paused && lease()<t) core->fail("lease_expired");
        if(core->paused) cancel_mouse();
        receipt();publish_status();
    } catch(const std::exception&) { core->fail("bridge_io");cancel_mouse(); }
}
int key_for(const std::string& token) {
    if(token=="right") return VK_RIGHT;if(token=="left") return VK_LEFT;
    if(token=="up") return VK_UP;if(token=="down") return VK_DOWN;
    if(token=="ok") return VK_RETURN;if(token=="cancel") return VK_ESCAPE;
    if(token=="shift") return VK_SHIFT;if(token=="pageup") return VK_PRIOR;
    if(token=="pagedown") return VK_NEXT;if(token=="mouse0") return VK_LBUTTON;return 0;
}
void mouse() {
    if(!core || !core->active || core->command.action!="click" || mouse_sending) return;
    auto* s=screen();if(!s || !s->hwnd) return;
    mouse_sending=true;
    SendMessageW(s->hwnd,WM_MOUSEMOVE,core->held=="mouse0"?MK_LBUTTON:0,MAKELPARAM(core->command.x,core->command.y));
    mouse_sending=false;
}
SHORT WINAPI virtual_key(int key) {
    guard();
    if(!core || core->paused) return original_key?original_key(key):0;
    mouse();
    if(!core->held.empty() && key==key_for(core->held)) { core->observe(core->held);return static_cast<SHORT>(0x8000); }
    return 0;
}
BOOL WINAPI virtual_cursor(LPPOINT point) {
    guard();
    if(!core || core->paused || !core->active || core->command.action!="click") return original_cursor?original_cursor(point):FALSE;
    auto* s=screen();if(!point || !s || !s->hwnd) return FALSE;
    mouse();point->x=core->command.x;point->y=core->command.y;return ClientToScreen(s->hwnd,point);
}
HWND WINAPI virtual_active() {
    guard();
    if(core && !core->paused) {
        auto* s=screen();
        if(s && s->hwnd) return s->hwnd;
    }
    return original_active?original_active():nullptr;
}
bool replace_import(const char* target,ULONG_PTR replacement,ULONG_PTR& original) {
    auto* base=reinterpret_cast<BYTE*>(GetModuleHandleW(nullptr));
    auto* dos=reinterpret_cast<IMAGE_DOS_HEADER*>(base);
    auto* nt=reinterpret_cast<IMAGE_NT_HEADERS*>(base+dos->e_lfanew);
    auto d=nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT];if(!d.VirtualAddress) return false;
    auto* entry=reinterpret_cast<IMAGE_IMPORT_DESCRIPTOR*>(base+d.VirtualAddress);
    for(;entry->Name;++entry) {
        if(_stricmp(reinterpret_cast<char*>(base+entry->Name),"user32.dll")) continue;
        if(!entry->OriginalFirstThunk) return false;
        auto* names=reinterpret_cast<IMAGE_THUNK_DATA*>(base+entry->OriginalFirstThunk);
        auto* slots=reinterpret_cast<IMAGE_THUNK_DATA*>(base+entry->FirstThunk);
        for(;names->u1.AddressOfData;++names,++slots) {
            if(IMAGE_SNAP_BY_ORDINAL(names->u1.Ordinal)) continue;
            auto* name=reinterpret_cast<IMAGE_IMPORT_BY_NAME*>(base+names->u1.AddressOfData);
            if(strcmp(reinterpret_cast<char*>(name->Name),target)) continue;
            DWORD protection;
            if(!VirtualProtect(&slots->u1.Function,sizeof(ULONG_PTR),PAGE_READWRITE,&protection)) return false;
            original=slots->u1.Function;patches.push_back({&slots->u1.Function,original,replacement});slots->u1.Function=replacement;
            DWORD ignored;VirtualProtect(&slots->u1.Function,sizeof(ULONG_PTR),protection,&ignored);return true;
        }
    }
    return false;
}
void capture(const std::string& name) {
    HWND hwnd=nullptr;int w=0,h=0;
    if(!dimensions(hwnd,w,h)) throw std::runtime_error("capture_dimensions");
    HDC source=GetDC(hwnd),memory=CreateCompatibleDC(source);
    HBITMAP bitmap=CreateCompatibleBitmap(source,w,h);
    HGDIOBJ previous=bitmap?SelectObject(memory,bitmap):nullptr;
    BOOL ok=bitmap && PrintWindow(hwnd,memory,PW_CLIENTONLY);
    if(previous) SelectObject(memory,previous);
    if(memory) DeleteDC(memory);if(source) ReleaseDC(hwnd,source);
    if(!ok) { if(bitmap) DeleteObject(bitmap);throw std::runtime_error("capture_failed"); }
    const auto destination=path(name),temporary=destination+L".part";
    Gdiplus::Status saved=Gdiplus::GenericError;
    {
        Gdiplus::Bitmap image(bitmap,nullptr);
        Gdiplus::BitmapData pixels;Gdiplus::Rect rect(0,0,w,h);
        if(image.LockBits(&rect,Gdiplus::ImageLockModeRead,PixelFormat32bppARGB,&pixels)==Gdiplus::Ok) {
            size_t black=0,white=0,sampled=0;
            for(int y=0;y<h;y+=8) for(int x=0;x<w;x+=8) {
                auto* p=static_cast<BYTE*>(pixels.Scan0)+y*pixels.Stride+4*x;
                ++sampled;if(p[0]<=2 && p[1]<=2 && p[2]<=2) ++black;
                if(p[0]>=253 && p[1]>=253 && p[2]>=253) ++white;
            }
            image.UnlockBits(&pixels);
            if(sampled && static_cast<double>(std::max(black,white))/sampled<=.999) {
                const CLSID png={0x557cf406,0x1a04,0x11d3,{0x9a,0x73,0x00,0x00,0xf8,0x1e,0xf3,0x2e}};
                saved=image.Save(temporary.c_str(),&png,nullptr);
            }
        }
    }
    DeleteObject(bitmap);
    if(saved!=Gdiplus::Ok || !MoveFileExW(temporary.c_str(),destination.c_str(),MOVEFILE_REPLACE_EXISTING|MOVEFILE_WRITE_THROUGH)) {
        DeleteFileW(temporary.c_str());throw std::runtime_error("capture_failed");
    }
}
void tick() {
    if(ticking || !core) return;ticking=true;updates_started=true;
    try {
        // Take the command before its lease and pause snapshots.
        auto fields=!core->active && !core->receipt.ready?read("request"):gameqa::Fields();
        double t=now(),expiry=lease();const auto p=generation();
        if(p!=core->pause || (!core->paused && expiry<t)) {
            if(p!=core->pause) { core->pause=p;core->fail("manual_pause"); }
            else core->fail("lease_expired");
        }
        if(core->end()) {
            const auto name="screenshots/"+core->command.id+".png";
            capture(name);core->captured(name);
        }
        core->begin(t,expiry,p);
        if(!fields.empty() && gameqa::get(fields,"session_id")==core->session && gameqa::get(fields,"protocol")=="1") {
            const auto command=gameqa::parse_command(fields);
            if(core->accept(command,t,expiry)) {
                HWND hwnd=nullptr;int w=0,h=0;
                if(!dimensions(hwnd,w,h)) core->fail("input_backend_unavailable");
                else if(command.action=="click" && (command.x>=w || command.y>=h)) core->fail("click_out_of_bounds");
            }
        }
        mouse();native_mouse();receipt();publish_status();
    } catch(const std::exception& e) {
        core->fail(e.what());cancel_mouse();try { receipt();publish_status(true); } catch(...) {}
    }
    ticking=false;
}
void message(HSPCTX* state) {
    if(state->runmode==RUNMODE_WAIT || state->runmode==RUNMODE_AWAIT) tick();
    original_message(state);
}
int event(int kind,int first,int,void* result) {
    if(kind==HSPEVENT_GETKEY) {
        guard();if(!core || core->paused) return 0;
        *static_cast<int*>(result)=(virtual_key(first)&0x8000)?1:0;return 1;
    }
    return 0;
}
int terminate(int) {
    cancel_mouse();
    if(context && context->msgfunc==message) context->msgfunc=original_message;
    if(core) { core->fail("plugin_unloaded");try { receipt();publish_status(true); } catch(...) {} }
    for(auto& p:patches) if(*p.slot==p.replacement) {
        DWORD protection;
        if(VirtualProtect(p.slot,sizeof(ULONG_PTR),PAGE_READWRITE,&protection)) {
            *p.slot=p.original;DWORD ignored;VirtualProtect(p.slot,sizeof(ULONG_PTR),protection,&ignored);
        }
    }
    patches.clear();if(gdiplus_token) Gdiplus::GdiplusShutdown(gdiplus_token);gdiplus_token=0;return 0;
}
}
extern "C" __declspec(dllexport) void WINAPI hsp3cmdinit(HSP3TYPEINFO* info) {
    try {
        directory=environment(L"GAMEQAKIT_BRIDGE_DIR");auto session=utf8(environment(L"GAMEQAKIT_SESSION_ID"));
        if(directory.empty() || session.empty()) return;
        write("plugin-stage.txt","initializing",true);
        wchar_t buffer[32768];DWORD length=GetModuleFileNameW(nullptr,buffer,32768);
        if(!length || length>=32768) return;
        executable.assign(buffer,length);executable_utf8=utf8(executable);process_id=GetCurrentProcessId();
        auto binding=read("connection");
        if(gameqa::get(binding,"session_id")!=session || gameqa::integer(binding,"pid")!=process_id ||
            _wcsicmp(wide(gameqa::get(binding,"exe_path")).c_str(),executable.c_str()) || gameqa::get(binding,"protocol")!="1") return;
        wchar_t desktop[256]={0};DWORD needed=0;
        const auto expected=environment(L"GAMEQAKIT_DESKTOP_NAME");
        if(expected.compare(0,10,L"gameqakit-") ||
            !GetUserObjectInformationW(GetThreadDesktop(GetCurrentThreadId()),UOI_NAME,desktop,sizeof(desktop),&needed) ||
            expected!=desktop) throw std::runtime_error("isolated_desktop_required");
        Gdiplus::GdiplusStartupInput startup;
        if(Gdiplus::GdiplusStartup(&gdiplus_token,&startup,nullptr)!=Gdiplus::Ok) return;
        api=info->hspexinfo;context=info->hspctx;
        if(!context || !context->msgfunc) throw std::runtime_error("missing_message_callback");
        core.reset(new gameqa::Core(session));info->termfunc=terminate;
        CreateDirectoryW(path("results").c_str(),nullptr);CreateDirectoryW(path("screenshots").c_str(),nullptr);
        ULONG_PTR key=0,cursor=0,active=0;
        if(!replace_import("GetAsyncKeyState",reinterpret_cast<ULONG_PTR>(virtual_key),key) ||
           !replace_import("GetCursorPos",reinterpret_cast<ULONG_PTR>(virtual_cursor),cursor) ||
           !replace_import("GetActiveWindow",reinterpret_cast<ULONG_PTR>(virtual_active),active)) {
            terminate(0);core.reset();return;
        }
        original_key=reinterpret_cast<AsyncKey>(key);original_cursor=reinterpret_cast<Cursor>(cursor);
        original_active=reinterpret_cast<ActiveWindow>(active);
        info->option=HSPEVENT_ENABLE_GETKEY;info->eventfunc=event;
        original_message=context->msgfunc;context->msgfunc=message;
        write("plugin-stage.txt","awaiting-first-update",true);
    } catch(const std::exception& e) { try { write("plugin-stage.txt",e.what(),true); } catch(...) {} terminate(0);core.reset(); }
}
