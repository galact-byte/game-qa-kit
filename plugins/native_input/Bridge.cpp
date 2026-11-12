#include "EasyHook.hpp"
#include "Native.hpp"
#include "WriteGuard.hpp"
#include "InputCore.hpp"
#include "JsonWire.hpp"
#include <atomic>
#include <map>
#include <mutex>
#include <set>
#include <thread>
#include <objidl.h>
#include <gdiplus.h>
namespace native_bridge {
namespace fs=std::filesystem;
using namespace native_platform;
using namespace native_input;
inline Core core;
inline std::mutex mutex;
inline fs::path directory,root,executable;
inline std::string session,exe_utf8;
inline uint64_t observation=0;
inline HMODULE own_module=nullptr;
inline std::atomic<bool> running{false};
inline ULONG_PTR imaging=0;
inline void write(const fs::path& relative,const std::string& text){
    auto destination=directory/relative;auto temporary=destination;temporary+=L".tmp";
    {std::ofstream out(temporary,std::ios::binary|std::ios::trunc);out<<text;out.close();if(!out)throw std::runtime_error("evidence_write_failed");}
    publish(temporary,destination);
}
inline std::string identity(){return "\"protocol\":2,\"session_id\":"+quote(session)+",\"pid\":"+std::to_string(GetCurrentProcessId())+",\"exe_path\":"+quote(exe_utf8);}
inline std::string semantics(){return "\"timing_mode\":\"duration_ms\",\"pause_scope\":\"input\",\"simulation_pause\":false";}
}
#include "DeviceHooks.hpp"
#include "Isolation.hpp"
#include "Capture.hpp"
namespace native_bridge {
inline std::string state_text(){
    std::lock_guard<std::mutex> lock(mutex);std::ostringstream out;
    out<<"{"<<identity()<<","<<semantics()<<",\"updated_at\":"<<std::setprecision(16)<<now()<<",\"observation_seq\":"<<observation
       <<",\"paused\":"<<(core.paused?"true":"false")<<",\"held\":["<<(core.held.empty()?"":quote(core.held))<<"]"
       <<",\"max_duration_ms\":1000,\"capabilities\":[\"move\",\"confirm\",\"cancel\",\"wait\"],\"buttons\":[\"up\",\"down\",\"left\",\"right\",\"ok\",\"cancel\"]"
       <<",\"window_id\":"<<reinterpret_cast<uintptr_t>(window.load())<<",\"error\":"<<quote(core.error)<<"}";return out.str();
}
inline void exact(const Fields& fields,const std::set<std::string>& keys){
    if(fields.size()!=keys.size())throw std::runtime_error("unexpected_control_fields");
    for(const auto& item:fields)if(!keys.count(item.first))throw std::runtime_error("unexpected_control_fields");
}
inline bool identifier(const std::string& id){
    if(id.empty()||id.size()>64)return false;
    for(unsigned char c:id)if(!((c>='a'&&c<='z')||(c>='A'&&c<='Z')||(c>='0'&&c<='9')||c=='_'||c=='-'))return false;
    return true;
}
inline void accept(const std::string& request,const std::string& lease,const std::string& pause){
    const auto command=request.empty()?Fields{}:json(request);
    const auto funding=lease.empty()?Fields{}:json(lease);
    const auto stop=pause.empty()?Fields{}:json(pause);
    const auto time=GetTickCount64();const auto wall=now();
    std::string generation;
    if(!stop.empty()){
        exact(stop,{"id","session_id","at"});generation=get(stop,"id");
        if(get(stop,"session_id")!=session||!identifier(generation))throw std::runtime_error("invalid_pause_identity");
        number(stop,"at");
    }
    double remaining=0;
    if(!funding.empty()){
        exact(funding,{"session_id","expires_at"});
        if(get(funding,"session_id")!=session)throw std::runtime_error("invalid_lease_identity");
        remaining=number(funding,"expires_at")-wall;
        if(remaining>1.501)throw std::runtime_error("invalid_lease_duration");
    }
    std::lock_guard<std::mutex> lock(mutex);
    if(generation!=core.pause_id){core.pause(generation);synchronize();}
    const auto expiry=remaining>0?time+static_cast<uint64_t>(remaining*1000):time;
    if(!core.paused&&remaining>0)core.renew(time,expiry);
    core.tick(time);synchronize();
    if(command.empty())return;
    const auto id=get(command,"id");if(!identifier(id))throw std::runtime_error("invalid_command_id");
    if(core.seen.count(id))return;
    const auto action=get(command,"payload.action");
    std::set<std::string> keys={"protocol","session_id","id","payload.action","after_observation_seq","pause_id","created_at"};
    if(action!="resume")keys.insert("payload.duration_ms");if(action=="move")keys.insert("payload.direction");exact(command,keys);
    if(integer(command,"protocol")!=2||get(command,"session_id")!=session)throw std::runtime_error("invalid_request_identity");
    const auto after=integer(command,"after_observation_seq");
    if(after<0||static_cast<uint64_t>(after)>observation)throw std::runtime_error("invalid_observation");
    const auto age=wall-number(command,"created_at");if(age< -1||age>2)throw std::runtime_error("stale_command");
    const auto duration=action=="resume"?0:integer(command,"payload.duration_ms");
    if(duration<0||duration>1000)throw std::runtime_error("invalid_duration");
    const auto button=action=="move"?get(command,"payload.direction"):action=="confirm"?"ok":action=="cancel"?"cancel":"";
    if(!core.begin(id,action,button,static_cast<uint32_t>(duration),time,expiry,get(command,"pause_id")))
        throw std::runtime_error("command_rejected: active="+std::to_string(core.active)+", paused="+std::to_string(core.paused)+
            ", lease_ms="+std::to_string(static_cast<int64_t>(expiry)-static_cast<int64_t>(time))+", duration_ms="+std::to_string(duration)+
            ", generation_matches="+std::to_string(get(command,"pause_id")==core.pause_id)+", lease_present="+std::to_string(!funding.empty())+
            ", remaining_ms="+std::to_string(remaining*1000)+", request_age_ms="+std::to_string(age*1000));
    synchronize();
}
inline DWORD WINAPI watchdog(void*){
    while(running){
        {std::lock_guard<std::mutex> lock(mutex);core.tick(GetTickCount64());synchronize();}
        Sleep(5);
    }
    return 0;
}
inline void guard_audit(){
    std::ostringstream out;out<<"{\"denied_files\":[";bool first=true;
    {std::lock_guard<std::mutex> lock(native_guard::audit_mutex);for(const auto& path:native_guard::denied_paths){if(!first)out<<',';first=false;out<<quote(utf8(path));}}
    out<<"],\"denied_registry_writes\":"<<native_guard::registry_denied.load()<<"}";write(L"write-guard.json",out.str());
}
inline DWORD WINAPI worker(void*){
    try{
        fs::create_directories(directory/L"results");fs::create_directories(directory/L"screenshots");
        Gdiplus::GdiplusStartupInput startup;if(Gdiplus::GdiplusStartup(&imaging,&startup,nullptr)!=Gdiplus::Ok)throw std::runtime_error("imaging_unavailable");
        const auto beginning=GetTickCount64();
        while(!bind_window()){if(GetTickCount64()-beginning>=15000)throw std::runtime_error("startup_window_timeout");Sleep(20);}
        std::string initial;
        while(initial.empty()){
            initial=capture("startup");
            if(!initial.empty())break;
            if(GetTickCount64()-beginning>=15000)throw std::runtime_error("startup_invalid_image");
            Sleep(20);
        }
        {std::lock_guard<std::mutex> lock(mutex);++observation;}
        write(L"status.json",state_text());
        write(L"connection.json","{"+identity()+",\"command_format\":\"json\",\"control_transport\":\"files-v1\",\"backend\":\"native-input-x86\"}");
        std::string published;uint64_t updated=0,captured=GetTickCount64();
        while(running){
            if(!IsWindow(window.load()))throw std::runtime_error("window_destroyed");
            // Read the request first, then the latest lease and pause generation.
            const auto request=text(directory/L"request.json");const auto lease=text(directory/L"lease.json");const auto pause=text(directory/L"pause.json");
            accept(request,lease,pause);
            Core snapshot;bool pending=false;
            {std::lock_guard<std::mutex> lock(mutex);snapshot=core;pending=core.active&&core.capture_pending;}
            if(snapshot.error=="input_release_unconfirmed")throw std::runtime_error(snapshot.error);
            if(pending){
                const auto image=capture(snapshot.id);if(image.empty())throw std::runtime_error("invalid_capture_image");
                const auto digest=hash(directory/image);uint64_t sequence=0,completed=GetTickCount64();
                {std::lock_guard<std::mutex> lock(mutex);core.tick(completed);synchronize();
                    if(core.active&&core.id==snapshot.id&&core.pause_id==snapshot.pause_id&&core.capture_pending){sequence=++observation;snapshot=core;core.finish();}}
                if(sequence){
                    write(L"status.json",state_text());updated=GetTickCount64();
                    const bool keyed=snapshot.keyed();
                    write(fs::path(L"results")/(wide(snapshot.id)+L".json"),"{"+identity()+","+semantics()+",\"id\":"+quote(snapshot.id)+",\"ok\":true,\"consumed\":"+(keyed?"true":"false")+
                        ",\"press_consumed\":"+(snapshot.press_consumed?"true":"false")+",\"release_consumed\":"+(snapshot.release_consumed?"true":"false")+
                        ",\"pause_id\":"+quote(snapshot.pause_id)+",\"held\":[],\"observation_seq\":"+std::to_string(sequence)+",\"started_monotonic_ms\":"+std::to_string(snapshot.started)+
                        ",\"completed_monotonic_ms\":"+std::to_string(completed)+",\"screenshot\":"+quote(image)+",\"screenshot_sha256\":"+quote(digest)+"}");published=snapshot.id;
                }
                captured=GetTickCount64();
            }else if(!snapshot.active&&GetTickCount64()-captured>=500){
                if(capture("observation").empty())throw std::runtime_error("invalid_observation_image");
                {std::lock_guard<std::mutex> lock(mutex);++observation;}captured=GetTickCount64();
            }
            {std::lock_guard<std::mutex> lock(mutex);snapshot=core;}
            if(!snapshot.active&&!snapshot.id.empty()&&snapshot.id!=published&&!snapshot.error.empty()){
                write(fs::path(L"results")/(wide(snapshot.id)+L".json"),"{"+identity()+",\"id\":"+quote(snapshot.id)+",\"ok\":false,\"error\":"+quote(snapshot.error)+"}");published=snapshot.id;
            }
            if(GetTickCount64()-updated>=50){write(L"status.json",state_text());guard_audit();updated=GetTickCount64();}
            Sleep(5);
        }
    }catch(const std::exception& error){
        {std::lock_guard<std::mutex> lock(mutex);core.fail(error.what());synchronize();}
        try{write(L"fatal.json","{\"error\":"+quote(error.what())+"}");write(L"status.json",state_text());guard_audit();}catch(...){}
    }
    return 0;
}
}
extern "C" __declspec(dllexport) DWORD WINAPI StartNative(void*){
    using namespace native_bridge;
    try{
        root=environment(L"GAMEQAKIT_NATIVE_ROOT");session=utf8(environment(L"GAMEQAKIT_NATIVE_SESSION"));directory=root/L"bridge";
        if(root.empty()||!identifier(session))throw std::runtime_error("invalid_native_environment");
        wchar_t path[32768];const auto length=GetModuleFileNameW(nullptr,path,32768);if(!length||length>=32768)throw std::runtime_error("executable_path_missing");
        executable=std::wstring(path,length);exe_utf8=utf8(executable.wstring());
        if(!same_path(executable.parent_path(),root/L"runtime"))throw std::runtime_error("runtime_path_mismatch");
        BOOL job=FALSE;if(!IsProcessInJob(GetCurrentProcess(),nullptr,&job)||!job)throw std::runtime_error("owned_job_required");
        const auto dll_length=GetModuleFileNameW(own_module,path,32768);if(!dll_length||dll_length>=32768||hash(std::wstring(path,dll_length))!=utf8(environment(L"GAMEQAKIT_NATIVE_DLL_SHA256")))throw std::runtime_error("loaded_dll_hash_mismatch");
        native_guard::install(root);install_isolation();install_directinput();
        running=true;auto timer=CreateThread(nullptr,0,watchdog,nullptr,0,nullptr);if(!timer)throw std::runtime_error("watchdog_thread_failed");CloseHandle(timer);
        auto control=CreateThread(nullptr,0,worker,nullptr,0,nullptr);if(!control)throw std::runtime_error("worker_thread_failed");CloseHandle(control);
        write(L"guard-ready.json","{\"file_guard\":true,\"input_isolation\":true,\"watchdog\":true}");return 0;
    }catch(const std::exception& error){try{write(L"fatal.json","{\"error\":"+quote(error.what())+"}");}catch(...){}return 1;}
}
extern "C" BOOL APIENTRY EasyHookDllMain(HMODULE,DWORD,LPVOID);
BOOL WINAPI DllMain(HINSTANCE module,DWORD reason,LPVOID reserved){
    if(reason==DLL_PROCESS_ATTACH)native_bridge::own_module=module;
    return EasyHookDllMain(module,reason,reserved);
}
