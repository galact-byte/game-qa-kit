#include <windows.h>
#include <objidl.h>
#include <gdiplus.h>
#include "Native.hpp"
#include "WriteGuard.hpp"
#include "Wire.hpp"
#include "Realtime.hpp"
#include <algorithm>
#include <atomic>
#include <memory>
#include <mutex>

namespace {
namespace fs=std::filesystem;
using wolf::now;using wolf::utf8;
fs::path directory,executable;
std::string session,executable_utf8,generation;
uintptr_t base=0,input_function=0,tail_function=0,tail_resume=0,keyboard_address=0;
std::unique_ptr<gameqa::Core> core;
std::unique_ptr<wolf::Realtime> realtime;
std::mutex mutex;
std::atomic<unsigned> completed{0};
std::atomic<unsigned long long> polls{0},held_polls{0};
std::atomic<bool> ready{false},at_boundary{false};
DWORD game_thread=0;
void* keyboard=nullptr;
using GetState=HRESULT (WINAPI*)(void*,DWORD,void*);
GetState original_get_state=nullptr;
double expiry=0;
ULONG_PTR imaging=0;

gameqa::Fields read(const char* name) { return gameqa::ini(wolf::text(directory/(std::string(name)+".ini"))); }
void write(const fs::path& name,const std::string& text) {
    auto path=directory/name;auto tmp=path;tmp+=L".tmp";
    { std::ofstream out(tmp,std::ios::binary|std::ios::trunc);out<<text;out.flush();
      if(!out) throw std::runtime_error("bridge_write_failed"); }
    for(int i=0;i<8;++i) {
        if(MoveFileExW(tmp.c_str(),path.c_str(),MOVEFILE_REPLACE_EXISTING|MOVEFILE_WRITE_THROUGH)) return;
        Sleep(2);
    }
    throw std::runtime_error("bridge_replace_failed");
}
void guard() {
    const auto lease=gameqa::ini(realtime->read(wolf::Realtime::Lease));
    const auto pause=gameqa::ini(realtime->read(wolf::Realtime::Pause));
    expiry=gameqa::lease_expiry(expiry,session,lease);
    if(gameqa::get(pause,"session_id")==session) generation=gameqa::get(pause,"id");
    core->tick(completed.load(),now(),expiry,generation);
}
HRESULT WINAPI get_state(void* device,DWORD size,void* buffer) {
    const auto result=original_get_state(device,size,buffer);
    if(device!=keyboard || size!=256 || !buffer) return result;
    ++polls;std::lock_guard<std::mutex> lock(mutex);
    std::memset(buffer,0,256);
    try {
        guard();
        const auto code=gameqa::scan_code(core->held);
        if(!core->paused && code) {
            static_cast<unsigned char*>(buffer)[code]=128;++held_polls;
            core->observe(core->held);
        }
    } catch(const std::exception& e) { core->fail(e.what()); }
    return S_OK;
}
void install_device() {
    if(ready) return;
    keyboard=*reinterpret_cast<void**>(keyboard_address);
    if(!keyboard) throw std::runtime_error("keyboard_device_unavailable");
    auto table=*static_cast<void***>(keyboard);
    if(!table || !table[9]) throw std::runtime_error("keyboard_state_method_unavailable");
    original_get_state=reinterpret_cast<GetState>(table[9]);
    auto replacement=reinterpret_cast<void*>(&get_state);wolf::replace(table+9,&replacement,sizeof(replacement));
    ready=true;
}
void before_frame() {
    for(;;) {
        bool advance=false;
        {
            std::lock_guard<std::mutex> lock(mutex);
            try {
                if(!game_thread) game_thread=GetCurrentThreadId();
                if(game_thread!=GetCurrentThreadId()) throw std::runtime_error("game_thread_changed");
                at_boundary=true;
                install_device();guard();
                advance=core->active && !core->paused && !core->capture_pending;
                if(advance) at_boundary=false;
            } catch(const std::exception& e) { core->fail(e.what()); }
        }
        if(advance) return;
        MSG message{};
        for(int i=0;i<16 && PeekMessageW(&message,nullptr,0,0,PM_REMOVE);++i) {
            if(message.message==WM_QUIT) ExitProcess(0);
            TranslateMessage(&message);DispatchMessageW(&message);
        }
        Sleep(2);
    }
}
void after_frame() {
    std::lock_guard<std::mutex> lock(mutex);
    ++completed;
    try { guard(); } catch(const std::exception& e) { core->fail(e.what()); }
}
__declspec(naked) void input_hook() {
    __asm {
        pushfd
        pushad
        call before_frame
        popad
        popfd
        jmp dword ptr [input_function]
    }
}
__declspec(naked) void tail_hook() {
    __asm {
        mov dword ptr [esp], offset returned
        jmp dword ptr [tail_function]
    returned:
        pushfd
        pushad
        call after_frame
        popad
        popfd
        jmp dword ptr [tail_resume]
    }
}
void validate_call(uintptr_t address,uintptr_t target) {
    auto p=reinterpret_cast<const unsigned char*>(address);int32_t displacement=0;
    std::memcpy(&displacement,p+1,4);
    if(p[0]!=0xe8 || address+5+displacement!=target) throw std::runtime_error("game_callsite_mismatch");
}
void patch_call(uintptr_t address,void* hook) {
    unsigned char bytes[5]={0xe8};const auto offset=static_cast<int32_t>(reinterpret_cast<uintptr_t>(hook)-(address+5));
    std::memcpy(bytes+1,&offset,4);wolf::replace(reinterpret_cast<void*>(address),bytes,5);
}
void receipt() {
    gameqa::Receipt r;
    { std::lock_guard<std::mutex> lock(mutex);if(!core->receipt.ready) return;r=core->receipt; }
    const auto& q=r.request;using gameqa::quote;
    std::ostringstream out;out<<"{\"protocol\":1,\"session_id\":"<<quote(session)<<",\"id\":"<<quote(q.id)
        <<",\"ok\":"<<(r.ok?"true":"false")<<",\"consumed\":"<<(r.consumed?"true":"false")
        <<",\"frame\":"<<r.frame<<",\"completed_at\":"<<std::setprecision(16)<<now()
        <<",\"after_frame\":"<<q.after<<",\"pause_id\":"<<quote(q.pause)
        <<",\"action\":"<<quote(q.action)<<",\"requested_frames\":"<<q.frames;
    if(r.ok) out<<",\"screenshot\":"<<quote(r.screenshot)<<",\"capture_frame\":"<<r.frame;
    else out<<",\"error\":"<<quote(r.error);
    out<<"}";write(fs::path("results")/(q.id+".json"),out.str());
    if(realtime->write(wolf::Realtime::Result,out.str())) {
        std::lock_guard<std::mutex> lock(mutex);core->acknowledge();
    }
}
std::string status_text() {
    using gameqa::quote;std::ostringstream out;
    out<<"{\"protocol\":1,\"session_id\":"<<quote(session)<<",\"pid\":"<<GetCurrentProcessId()
       <<",\"exe_path\":"<<quote(executable_utf8)<<",\"engine\":\"wolf\",\"command_format\":\"ini\""
       <<",\"control_transport\":\"wolf-shm-v1\""
       <<",\"frame\":"<<core->frame<<",\"updated_at\":"<<std::setprecision(16)<<now()
       <<",\"capabilities\":[\"move\",\"confirm\",\"cancel\",\"press\",\"release\",\"wait\"],\"max_input_frames\":120"
       <<",\"buttons\":[\"up\",\"down\",\"left\",\"right\",\"ok\",\"cancel\"]"
       <<",\"paused\":"<<(core->paused?"true":"false")<<",\"input_ready\":"<<(ready?"true":"false")
       <<",\"at_frame_boundary\":"<<(at_boundary?"true":"false")
       <<",\"held\":["<<(core->held.empty()?"":quote(core->held))<<"],\"error\":"<<quote(core->error)
       <<",\"busy\":"<<(core->active?"true":"false")<<",\"input_polls\":"<<polls.load()<<",\"held_polls\":"<<held_polls.load()
       <<",\"expiry\":"<<expiry<<",\"capture_pending\":"<<(core->capture_pending?"true":"false")
       <<",\"denied_writes\":"<<wolf_guard::denied.load()<<",\"denied_registry_writes\":"<<wolf_guard::registry_denied.load()<<"}";
    return out.str();
}
void status() {
    std::string snapshot;
    { std::lock_guard<std::mutex> lock(mutex);snapshot=status_text(); }
    // Publishing a heartbeat must not hold up game input or its pause/lease checks.
    try { write("status.json",snapshot); }
    catch(const std::exception& e) {
        { std::lock_guard<std::mutex> lock(mutex);core->fail(e.what()); }
        try {receipt();} catch(...) {}
    }
}
void control_cycle() {
    std::lock_guard<std::mutex> lock(mutex);
    try {
        const auto request=gameqa::ini(realtime->read(wolf::Realtime::Request));
        guard();
        if(ready && at_boundary && !core->active && !core->receipt.ready &&
           gameqa::get(request,"session_id")==session && gameqa::get(request,"protocol")=="1")
            core->accept(gameqa::parse_command(request),now(),expiry);
        realtime->write(wolf::Realtime::Status,status_text());
    } catch(const std::exception& e) { core->fail(e.what()); }
}
DWORD WINAPI control_run(void*) {
    for(;;) { control_cycle();Sleep(2); }
}
BOOL CALLBACK collect(HWND window,LPARAM data) {
    DWORD pid=0;GetWindowThreadProcessId(window,&pid);RECT area{},previous{};
    auto selected=reinterpret_cast<HWND*>(data);
    if(pid==GetCurrentProcessId() && IsWindowVisible(window) && GetClientRect(window,&area) && area.right>100 && area.bottom>100) {
        if(*selected) GetClientRect(*selected,&previous);
        if(static_cast<int64_t>(area.right)*area.bottom>static_cast<int64_t>(previous.right)*previous.bottom) *selected=window;
    }
    return TRUE;
}
std::string capture(const std::string& id) {
    HWND window=nullptr;EnumThreadWindows(game_thread,collect,reinterpret_cast<LPARAM>(&window));
    if(!window || IsIconic(window)) throw std::runtime_error("capture_window_unavailable");
    struct DpiScope {
        DPI_AWARENESS_CONTEXT previous;
        explicit DpiScope(HWND target):previous(SetThreadDpiAwarenessContext(GetWindowDpiAwarenessContext(target))) {
            if(!previous) throw std::runtime_error("capture_dpi_context_failed");
        }
        ~DpiScope() { SetThreadDpiAwarenessContext(previous); }
    } dpi(window);
    RECT area{};
    if(!GetClientRect(window,&area)) throw std::runtime_error("capture_window_unavailable");
    const int width=area.right,height=area.bottom;
    if(width<=0 || height<=0 || static_cast<uint64_t>(width)*height>16777216) throw std::runtime_error("capture_dimensions_invalid");
    HDC screen=GetDC(window),memory=CreateCompatibleDC(screen);
    if(!screen || !memory) { if(memory) DeleteDC(memory);if(screen) ReleaseDC(window,screen);throw std::runtime_error("capture_dc_failed"); }
    BITMAPINFO info{};info.bmiHeader.biSize=sizeof(BITMAPINFOHEADER);info.bmiHeader.biWidth=width;
    info.bmiHeader.biHeight=-height;info.bmiHeader.biPlanes=1;info.bmiHeader.biBitCount=32;info.bmiHeader.biCompression=BI_RGB;
    auto bitmap=CreateCompatibleBitmap(screen,width,height);
    if(!bitmap) { DeleteDC(memory);ReleaseDC(window,screen);throw std::runtime_error("capture_bitmap_failed"); }
    auto old=SelectObject(memory,bitmap);
    const bool rendered=PrintWindow(window,memory,3) || PrintWindow(window,memory,1);
    SelectObject(memory,old);
    const int stride=width*4;std::vector<BYTE> pixels(static_cast<size_t>(stride)*height);
    const bool copied=rendered && GetDIBits(memory,bitmap,0,height,pixels.data(),&info,DIB_RGB_COLORS)==static_cast<int>(height);
    size_t black=0,white=0,sampled=0;const size_t total=static_cast<size_t>(width)*height,step=(std::max)(size_t(1),total/20000);
    if(copied) for(size_t i=0;i<total;i+=step) {
        const auto p=pixels.data()+i*4;
        if(p[0]==0 && p[1]==0 && p[2]==0) ++black;
        else if(p[0]==255 && p[1]==255 && p[2]==255) ++white;
        ++sampled;
    }
    const bool usable=sampled && static_cast<double>((std::max)(black,white))/sampled<0.999;
    std::string name="screenshots/"+id+".png";bool saved=false;
    if(usable) {
        Gdiplus::Bitmap image(width,height,stride,PixelFormat32bppRGB,pixels.data());
        const CLSID png={0x557cf406,0x1a04,0x11d3,{0x9a,0x73,0x00,0x00,0xf8,0x1e,0xf3,0x2e}};
        auto path=directory/name;auto tmp=path;tmp+=L".tmp";
        saved=image.Save(tmp.c_str(),&png,nullptr)==Gdiplus::Ok &&
            MoveFileExW(tmp.c_str(),path.c_str(),MOVEFILE_REPLACE_EXISTING|MOVEFILE_WRITE_THROUGH)!=FALSE;
    }
    SelectObject(memory,old);DeleteObject(bitmap);DeleteDC(memory);ReleaseDC(window,screen);
    if(!rendered || !copied) throw std::runtime_error("capture_failed");
    if(usable && !saved) throw std::runtime_error("capture_save_failed");
    return usable?name:"";
}
void audit_guard() {
    std::ostringstream out;out<<"{\"denied_files\":[";bool first=true;
    { std::lock_guard<std::mutex> lock(wolf_guard::audit_mutex);
      for(const auto& path:wolf_guard::denied_paths) { if(!first) out<<',';first=false;out<<gameqa::quote(utf8(path)); } }
    out<<"],\"denied_registry_writes\":"<<wolf_guard::registry_denied.load()<<"}";write("write-guard.json",out.str());
}
DWORD WINAPI run(void*) {
    try {
        directory=wolf::environment(L"GAMEQAKIT_BRIDGE_DIR");session=utf8(wolf::environment(L"GAMEQAKIT_SESSION_ID"));
        if(directory.empty() || session.empty()) return 1;
        wchar_t filename[32768];const auto n=GetModuleFileNameW(nullptr,filename,32768);
        if(!n || n>=32768) throw std::runtime_error("missing_executable_path");
        executable=std::wstring(filename,n);executable_utf8=utf8(executable.wstring());
        const auto binding=read("connection");
        if(gameqa::get(binding,"session_id")!=session || gameqa::integer(binding,"pid")!=GetCurrentProcessId() ||
           gameqa::get(binding,"exe_path")!=executable_utf8 || gameqa::get(binding,"protocol")!="1" ||
           !wolf::same_path(executable,directory.parent_path()/L"runtime"/L"Game.exe") ||
           !fs::is_regular_file(directory.parent_path()/L"preparation.json")) throw std::runtime_error("connection_mismatch");
        wchar_t desktop[256]={0};DWORD needed=0;auto expected=wolf::environment(L"GAMEQAKIT_DESKTOP_NAME");BOOL job=FALSE;
        if(expected.compare(0,10,L"gameqakit-") || gameqa::get(binding,"desktop")!=utf8(expected) ||
           !GetUserObjectInformationW(GetThreadDesktop(GetCurrentThreadId()),UOI_NAME,desktop,sizeof(desktop),&needed) || expected!=desktop ||
           !IsProcessInJob(GetCurrentProcess(),nullptr,&job) || !job) throw std::runtime_error("owned_isolated_desktop_required");
        const auto profile=wolf::validate_image(executable);base=reinterpret_cast<uintptr_t>(GetModuleHandleW(nullptr));
        if(profile.codepage && GetACP()!=profile.codepage) throw std::runtime_error("wolf_locale_codepage_mismatch");
        write("locale-status.json","{\"ansi\":"+std::to_string(GetACP())+",\"oem\":"+std::to_string(GetOEMCP())+",\"thread_locale\":"+std::to_string(GetThreadLocale())+"}");
        keyboard_address=base+profile.keyboard_device;
        input_function=base+profile.input_function;tail_function=base+profile.tail_function;tail_resume=base+profile.tail_call+5;
        validate_call(base+profile.input_call,input_function);validate_call(base+profile.tail_call,tail_function);
        fs::create_directories(directory/L"results");fs::create_directories(directory/L"screenshots");
        core.reset(new gameqa::Core(session));
        realtime.reset(new wolf::Realtime(GetCurrentProcessId(),session));
        wolf_guard::install(directory.parent_path());
        Gdiplus::GdiplusStartupInput startup;
        if(Gdiplus::GdiplusStartup(&imaging,&startup,nullptr)!=Gdiplus::Ok) throw std::runtime_error("imaging_unavailable");
        patch_call(base+profile.input_call,reinterpret_cast<void*>(&input_hook));patch_call(base+profile.tail_call,reinterpret_cast<void*>(&tail_hook));
        write("plugin-stage.txt","ready");
        HANDLE controller=CreateThread(nullptr,0,control_run,nullptr,0,nullptr);
        if(!controller) throw std::runtime_error("control_thread_failed");
        CloseHandle(controller);
        unsigned audits=0;
        for(;;) {
            std::string capture_id;
            {
                std::lock_guard<std::mutex> lock(mutex);
                if(at_boundary && core->capture_pending) capture_id=core->command_id();
            }
            try {receipt();} catch(const std::exception& e) {
                std::lock_guard<std::mutex> lock(mutex);core->fail(e.what());
            }
            status();
            if(!capture_id.empty()) {
                try {
                    const auto path=capture(capture_id);
                    {
                        std::lock_guard<std::mutex> lock(mutex);guard();
                        if(core->command_id()==capture_id) core->captured(path,now());
                    }
                    receipt();
                } catch(const std::exception& e) {
                    { std::lock_guard<std::mutex> lock(mutex);core->fail(e.what()); }
                    try {receipt();} catch(...) {}
                }
                status();
            }
            if(++audits%10==0) try {audit_guard();} catch(...) {}
            Sleep(10);
        }
    } catch(const std::exception& e) {
        if(!directory.empty()) try {write("plugin-stage.txt",e.what());} catch(...) {}
        return 2;
    }
}
}
BOOL WINAPI DllMain(HINSTANCE module,DWORD reason,void*) {
    if(reason==DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(module);HANDLE thread=CreateThread(nullptr,0,run,nullptr,0,nullptr);
        if(thread) CloseHandle(thread);
    }
    return TRUE;
}
