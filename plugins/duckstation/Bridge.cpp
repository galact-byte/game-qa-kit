#define NOMINMAX
#include <windows.h>
#include <bcrypt.h>
#include <gdiplus.h>
#include "Wire.hpp"
#include "FrameEvidence.hpp"
#include <atomic>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <memory>
#include <mutex>
#include <vector>
#pragma comment(lib,"bcrypt.lib")
#pragma comment(lib,"gdiplus.lib")
#pragma comment(lib,"user32.lib")

namespace {
namespace fs=std::filesystem;
struct VirtualJoystickDesc {
    uint32_t version;
    uint16_t type,padding,vendor,product,axes,buttons,balls,hats,touchpads,sensors,padding2[2];
    uint32_t button_mask,axis_mask;
    const char* name;const void* touchpad_desc;const void* sensor_desc;void* userdata;
    void (*Update)(void*);void (*SetPlayerIndex)(void*,int);
    bool (*Rumble)(void*,uint16_t,uint16_t);bool (*RumbleTriggers)(void*,uint16_t,uint16_t);
    bool (*SetLED)(void*,uint8_t,uint8_t,uint8_t);bool (*SendEffect)(void*,const void*,int);
    bool (*SetSensorsEnabled)(void*,bool);void (*Cleanup)(void*);
};
static_assert(sizeof(VirtualJoystickDesc)==136,"SDL virtual joystick ABI mismatch");
fs::path directory,executable,screenshot_dir;
std::string session,executable_utf8;
uintptr_t base=0;
std::mutex mutex;
std::unique_ptr<gameqa::Core> core;
std::atomic<void*> device{nullptr};
std::atomic<bool> ready{false},snapshot_requested{false};
std::atomic<unsigned> applied{0};
std::atomic<unsigned long long> callbacks{0};
bool (*set_button)(void*,int,bool)=nullptr;
bool (*get_button)(void*,int)=nullptr;
unsigned device_buttons=0;
double expiry=0;
std::string generation;
gameqa::Command pending;
bool has_pending=false;
ULONG_PTR imaging=0;

std::wstring environment(const wchar_t* key) {
    wchar_t text[32768];const DWORD n=GetEnvironmentVariableW(key,text,32768);
    return n && n<32768?std::wstring(text,n):L"";
}
std::string utf8(const std::wstring& text) {
    if(text.empty()) return "";
    int n=WideCharToMultiByte(CP_UTF8,0,text.data(),static_cast<int>(text.size()),nullptr,0,nullptr,nullptr);
    std::string out(n,0);WideCharToMultiByte(CP_UTF8,0,text.data(),static_cast<int>(text.size()),out.data(),n,nullptr,nullptr);return out;
}
double now() {
    FILETIME t;GetSystemTimeAsFileTime(&t);ULARGE_INTEGER value;value.LowPart=t.dwLowDateTime;value.HighPart=t.dwHighDateTime;
    return static_cast<double>(value.QuadPart)/10000000.0-11644473600.0;
}
std::string read_text(const fs::path& file) {
    std::ifstream input(file,std::ios::binary);
    if(!input) return "";
    std::string text;char buffer[4096];
    while(input.read(buffer,sizeof(buffer)) || input.gcount()) {
        text.append(buffer,static_cast<size_t>(input.gcount()));
        if(text.size()>65536) throw std::runtime_error("control_file_too_large");
    }
    return text;
}
gameqa::Fields read(const char* name) { return gameqa::ini(read_text(directory/(std::string(name)+".ini"))); }
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
std::string hash(const fs::path& path) {
    BCRYPT_ALG_HANDLE algorithm=nullptr;BCRYPT_HASH_HANDLE state=nullptr;
    if(BCryptOpenAlgorithmProvider(&algorithm,BCRYPT_SHA256_ALGORITHM,nullptr,0)<0)
        throw std::runtime_error("hash_backend_unavailable");
    unsigned char digest[32];bool valid=BCryptCreateHash(algorithm,&state,nullptr,0,nullptr,0,0)>=0;
    std::ifstream input(path,std::ios::binary);valid=valid && bool(input);char buffer[65536];
    while(valid && (input.read(buffer,sizeof(buffer)) || input.gcount()))
        valid=BCryptHashData(state,reinterpret_cast<PUCHAR>(buffer),static_cast<ULONG>(input.gcount()),0)>=0;
    if(valid) valid=BCryptFinishHash(state,digest,sizeof(digest),0)>=0;
    if(state) BCryptDestroyHash(state);BCryptCloseAlgorithmProvider(algorithm,0);
    if(!valid) throw std::runtime_error("image_hash_failed");
    std::ostringstream out;for(auto c:digest) out<<std::hex<<std::setw(2)<<std::setfill('0')<<static_cast<int>(c);return out.str();
}
template<class T> T native(uintptr_t rva) { return reinterpret_cast<T>(base+rva); }
template<class T> T symbol(HMODULE module,const char* name) {
    auto p=GetProcAddress(module,name);if(!p) throw std::runtime_error("missing_sdl_symbol");return reinterpret_cast<T>(p);
}
unsigned native_frame() { return *native<volatile unsigned*>(0xa60808); }
unsigned native_state() { return *native<volatile unsigned char*>(0xa60811); }
unsigned native_buttons() {
    const auto pad=*native<uintptr_t*>(0xd40f40);
    if(!pad) return 0;
    if(*reinterpret_cast<uintptr_t*>(pad)!=base+0x95e920) throw std::runtime_error("digital_controller_required");
    return static_cast<unsigned>(~*reinterpret_cast<volatile uint16_t*>(pad+0x10))&0xffffu;
}
unsigned sdl_mask(const std::string& key) {
    if(key=="up") return 1u<<11;if(key=="down") return 1u<<12;
    if(key=="left") return 1u<<13;if(key=="right") return 1u<<14;
    if(key=="ok") return 1u<<1;if(key=="cancel") return 1u<<0;
    if(key=="square") return 1u<<2;
    if(key=="shift") return 1u<<6;if(key=="pageup") return 1u<<9;
    if(key=="pagedown") return 1u<<10;return 0;
}
// SDL invokes this on DuckStation's normal input polling thread. Native stepping stays on that thread.
void update(void*) {
    ++callbacks;
    auto joystick=device.load();if(!joystick) return;
    std::lock_guard<std::mutex> lock(mutex);
    try {
        unsigned mask=0;
        unsigned observed=0;
        for(int i=0;i<21;++i) if(get_button(joystick,i)) observed|=1u<<i;
        device_buttons=observed;
        if(core) {
            auto lease=read("lease");
            expiry=gameqa::lease_expiry(expiry,session,lease);
            auto state=native_state();
            if(!ready && (state==2 || state==3)) { core->frame=native_frame();ready=true; }
            if(ready) {
                core->tick(native_frame(),now(),expiry,generation,native_buttons());
                if(has_pending && !core->active) {
                    native<void (*)(bool)>(0x2b6e70)(true);
                    core->accept(pending,now(),expiry);has_pending=false;
                }
                mask=sdl_mask(core->held);
                if(snapshot_requested.exchange(false)) mask|=1u<<15;
            }
        }
        const auto old=applied.load();
        for(int i=0;i<21;++i) if(((old^mask)&(1u<<i)) && !set_button(joystick,i,(mask&(1u<<i))!=0))
            throw std::runtime_error("virtual_button_failed");
        applied=mask;
        if(ready && core->active && !core->capture_pending && native_state()==3) {
            const bool pressed=true;native<void (*)(const void*,const bool*)>(0x2d0d10)(nullptr,&pressed);
        }
    } catch(const std::exception& e) {
        if(core) core->fail(e.what());snapshot_requested=false;
        for(int i=0;i<21;++i) set_button(joystick,i,false);applied=0;
    }
}
void receipt() {
    if(!core->receipt.ready) return;
    const auto& r=core->receipt;const auto& q=r.request;using gameqa::quote;
    std::ostringstream out;out<<"{\"protocol\":1,\"session_id\":"<<quote(session)<<",\"id\":"<<quote(q.id)
        <<",\"ok\":"<<(r.ok?"true":"false")<<",\"consumed\":"<<(r.consumed?"true":"false")
        <<",\"frame\":"<<r.frame<<",\"completed_at\":"<<std::setprecision(16)<<now()
        <<",\"after_frame\":"<<q.after<<",\"pause_id\":"<<quote(q.pause)
        <<",\"action\":"<<quote(q.action)<<",\"requested_frames\":"<<q.frames;
    if(r.ok) out<<",\"screenshot\":"<<quote(r.screenshot)<<",\"capture_frame\":"<<r.frame;
    else out<<",\"error\":"<<quote(r.error);
    out<<"}";write(fs::path("results")/(q.id+".json"),out.str());core->acknowledge();
}
void status() {
    using gameqa::quote;std::ostringstream out;
    out<<"{\"protocol\":1,\"session_id\":"<<quote(session)<<",\"pid\":"<<GetCurrentProcessId()
       <<",\"exe_path\":"<<quote(executable_utf8)<<",\"engine\":\"duckstation\",\"command_format\":\"ini\""
       <<",\"frame\":"<<core->frame<<",\"updated_at\":"<<std::setprecision(16)<<now()
       <<",\"capabilities\":[\"move\",\"confirm\",\"cancel\",\"press\",\"release\",\"wait\"],\"max_input_frames\":120"
       <<",\"buttons\":[\"up\",\"down\",\"left\",\"right\",\"ok\",\"cancel\",\"shift\",\"pageup\",\"pagedown\",\"square\"]"
       <<",\"paused\":"<<(core->paused?"true":"false")<<",\"input_ready\":"<<(ready?"true":"false")
       <<",\"held\":["<<(core->held.empty()?"":quote(core->held))<<"],\"error\":"<<quote(core->error)
       <<",\"native_buttons\":"<<(ready?native_buttons():0)<<",\"device_buttons\":"<<device_buttons
       <<",\"native_state\":"<<native_state()<<",\"busy\":"<<(core->active?"true":"false")
       <<",\"callbacks\":"<<callbacks.load()<<",\"applied\":"<<applied.load()<<",\"expiry\":"<<expiry
       <<",\"capture_pending\":"<<(core->capture_pending?"true":"false")
       <<",\"snapshot_requested\":"<<(snapshot_requested?"true":"false")<<"}";
    write("status.json",out.str());
}
std::set<fs::path> screenshots() {
    std::set<fs::path> result;
    if(fs::is_directory(screenshot_dir)) for(const auto& entry:fs::directory_iterator(screenshot_dir))
        if(entry.is_regular_file() && entry.path().extension()==L".png") result.insert(entry.path());
    return result;
}
enum class Capture { unreadable,blank,saved };
Capture save_capture(const fs::path& source,const fs::path& destination) {
    Gdiplus::Bitmap image(source.c_str());
    const auto width=image.GetWidth(),height=image.GetHeight();
    if(image.GetLastStatus()!=Gdiplus::Ok || !width || !height || static_cast<uint64_t>(width)*height>16777216)
        return Capture::unreadable;
    Gdiplus::Rect area(0,0,static_cast<INT>(width),static_cast<INT>(height));
    Gdiplus::BitmapData pixels{};
    if(image.LockBits(&area,Gdiplus::ImageLockModeRead,PixelFormat24bppRGB,&pixels)!=Gdiplus::Ok)
        return Capture::unreadable;
    const bool usable=gameqa::usable_bgr(static_cast<const unsigned char*>(pixels.Scan0),width,height,pixels.Stride);
    image.UnlockBits(&pixels);
    if(!usable) return Capture::blank;
    const CLSID png={0x557cf406,0x1a04,0x11d3,{0x9a,0x73,0x00,0x00,0xf8,0x1e,0xf3,0x2e}};
    auto tmp=destination;tmp+=L".tmp";
    if(image.Save(tmp.c_str(),&png,nullptr)!=Gdiplus::Ok) throw std::runtime_error("capture_save_failed");
    if(!MoveFileExW(tmp.c_str(),destination.c_str(),MOVEFILE_REPLACE_EXISTING|MOVEFILE_WRITE_THROUGH))
        throw std::runtime_error("capture_save_failed");
    return Capture::saved;
}
DWORD WINAPI run(void*) {
    try {
        directory=environment(L"GAMEQAKIT_BRIDGE_DIR");session=utf8(environment(L"GAMEQAKIT_SESSION_ID"));
        if(directory.empty() || session.empty()) return 1;
        wchar_t filename[32768];const auto n=GetModuleFileNameW(nullptr,filename,32768);
        if(!n || n>=32768) throw std::runtime_error("missing_executable_path");
        executable=std::wstring(filename,n);executable_utf8=utf8(executable.wstring());
        const auto binding=read("connection");
        if(gameqa::get(binding,"session_id")!=session || gameqa::integer(binding,"pid")!=GetCurrentProcessId() ||
           gameqa::get(binding,"exe_path")!=executable_utf8 || gameqa::get(binding,"protocol")!="1")
            throw std::runtime_error("connection_mismatch");
        wchar_t desktop[256]={0};DWORD needed=0;auto expected=environment(L"GAMEQAKIT_DESKTOP_NAME");
        if(expected.compare(0,10,L"gameqakit-") ||
           !GetUserObjectInformationW(GetThreadDesktop(GetCurrentThreadId()),UOI_NAME,desktop,sizeof(desktop),&needed) || expected!=desktop)
            throw std::runtime_error("isolated_desktop_required");
        if(hash(executable)!="2ea09675202a6758992571a57971b1cd57fd6b0dfde4b808624698bf52efdd57")
            throw std::runtime_error("unsupported_duckstation_binary");
        base=reinterpret_cast<uintptr_t>(GetModuleHandleW(nullptr));screenshot_dir=executable.parent_path()/"screenshots";
        fs::create_directories(directory/"results");fs::create_directories(directory/"screenshots");
        Gdiplus::GdiplusStartupInput startup;
        if(Gdiplus::GdiplusStartup(&imaging,&startup,nullptr)!=Gdiplus::Ok) throw std::runtime_error("imaging_unavailable");
        HMODULE sdl=nullptr;for(int i=0;i<1000 && !sdl;++i) { sdl=GetModuleHandleW(L"SDL3.dll");if(!sdl) Sleep(10); }
        if(!sdl) throw std::runtime_error("sdl_unavailable");
        auto initialized=symbol<uint32_t (*)(uint32_t)>(sdl,"SDL_WasInit");
        auto attach=symbol<uint32_t (*)(const VirtualJoystickDesc*)>(sdl,"SDL_AttachVirtualJoystick");
        auto open=symbol<void* (*)(uint32_t)>(sdl,"SDL_OpenJoystick");
        auto set_player=symbol<bool (*)(void*,int)>(sdl,"SDL_SetJoystickPlayerIndex");
        auto lock=symbol<void (*)()>(sdl,"SDL_LockJoysticks");auto unlock=symbol<void (*)()>(sdl,"SDL_UnlockJoysticks");
        set_button=symbol<bool (*)(void*,int,bool)>(sdl,"SDL_SetJoystickVirtualButton");
        get_button=symbol<bool (*)(void*,int)>(sdl,"SDL_GetJoystickButton");
        for(int i=0;i<1000 && !(initialized(0x200)&0x200);++i) Sleep(10);
        if(!(initialized(0x200)&0x200)) throw std::runtime_error("sdl_input_uninitialized");
        { std::lock_guard<std::mutex> guard(mutex);core.reset(new gameqa::Core(session,native_frame())); }
        VirtualJoystickDesc desc{};desc.version=sizeof(desc);desc.type=1;desc.buttons=21;
        desc.button_mask=(1u<<21)-1;desc.name="GameQA Protocol Controller";desc.Update=update;
        lock();const auto id=attach(&desc);void* joystick=id?open(id):nullptr;
        bool attached=joystick && set_player(joystick,15);if(attached) device=joystick;unlock();
        if(!attached) throw std::runtime_error("virtual_controller_unavailable");
        bool capturing=false;double started=0;std::set<fs::path> before;
        for(;;) {
            {
                std::lock_guard<std::mutex> guard(mutex);
                try {
                    auto pause=read("pause");
                    if(gameqa::get(pause,"session_id")==session) generation=gameqa::get(pause,"id");
                    auto lease=read("lease");
                    expiry=gameqa::lease_expiry(expiry,session,lease);
                    // The guard must run even if the emulator is paused. Only the SDL callback advances frame accounting.
                    core->tick(static_cast<unsigned>(core->frame),now(),expiry,generation,0);
                    receipt();
                    if(ready && !core->active) {
                        auto request=read("request");
                        if(gameqa::get(request,"session_id")==session && gameqa::get(request,"protocol")=="1") {
                            pending=gameqa::parse_command(request);has_pending=true;
                        }
                    }
                    if(core->capture_pending && !capturing) {
                        before=screenshots();started=now();capturing=true;snapshot_requested=true;
                    }
                    if(capturing && core->capture_pending) {
                        for(const auto& file:screenshots()) if(!before.count(file)) {
                            const std::string name="screenshots/"+core->command_id()+".png";
                            const auto capture=save_capture(file,directory/name);
                            if(capture==Capture::saved) { core->captured(name,now());break; }
                            if(capture==Capture::blank) { core->captured("",now());break; }
                        }
                        if(core->capture_pending && now()-started>3) core->fail("screenshot_timeout");
                    }
                    if(!core->capture_pending) { capturing=false;snapshot_requested=false; }
                    receipt();status();
                } catch(const std::exception& e) {
                    core->fail(e.what());snapshot_requested=false;capturing=false;
                    try { receipt();status(); } catch(...) {}
                }
            }
            Sleep(10);
        }
    } catch(const std::exception& e) {
        if(!directory.empty()) try { write("plugin-stage.txt",e.what()); } catch(...) {}
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
