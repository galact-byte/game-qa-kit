#include "StartupGuard.hpp"
#include <iostream>
namespace {
struct TimeFields { WORD year,month,day,hour,minute,second,milliseconds,weekday; };
struct TimeZone { LONG bias;WCHAR standard[32];TimeFields date;LONG standard_bias;WCHAR daylight[32];TimeFields daylight_date;LONG daylight_bias; };
struct LocaleBlock { ULONG ansi=932,oem=932,locale=0x411,charset=128,hook_ui=0;WCHAR face[32]{};TimeZone timezone{};unsigned long long registry_count=0; };
struct ProcessInfo { HANDLE process{},thread{};DWORD pid{},tid{};void* first_load{}; };
static_assert(offsetof(LocaleBlock,registry_count)==0x100);
static_assert(sizeof(ProcessInfo)==20);
using CreateLocale=LONG (WINAPI*)(LocaleBlock*,LPCWSTR,LPWSTR,LPCWSTR,ULONG,STARTUPINFOW*,ProcessInfo*,void*,void*,void*,HANDLE);
void require(bool ok,const char* error) { if(!ok) throw std::runtime_error(error); }
void publish(const wolf::fs::path& bridge,const std::string& session,DWORD pid,const char* stage) {
    const auto temporary=bridge/L"launch.ini.tmp";
    {std::ofstream out(temporary);out<<"[bridge]\nprotocol=1\nsession_id="<<session<<"\npid="<<pid<<"\nowner_pid="<<GetCurrentProcessId()<<"\nstage="<<stage<<"\n";require(bool(out),"launch_status_write_failed");}
    require(MoveFileExW(temporary.c_str(),(bridge/L"launch.ini").c_str(),MOVEFILE_REPLACE_EXISTING|MOVEFILE_WRITE_THROUGH)!=FALSE,"launch_status_publish_failed");
}
void bootstrap(const ProcessInfo& child,const wolf::fs::path& bridge) {
    const unsigned char code[]={0x31,0xc0,0xc2,0x04,0x00};
    auto remote=VirtualAllocEx(child.process,nullptr,sizeof(code),MEM_COMMIT|MEM_RESERVE,PAGE_READWRITE);
    SIZE_T count=0;DWORD old=0;
    require(remote && WriteProcessMemory(child.process,remote,code,sizeof(code),&count) && count==sizeof(code) &&
            VirtualProtectEx(child.process,remote,sizeof(code),PAGE_EXECUTE_READ,&old) && FlushInstructionCache(child.process,remote,sizeof(code)),"locale_bootstrap_write_failed");
    DWORD tid=0;auto thread=CreateRemoteThread(child.process,nullptr,0,reinterpret_cast<LPTHREAD_START_ROUTINE>(remote),nullptr,0,&tid);
    require(thread!=nullptr,"locale_bootstrap_thread_failed");
    try {
        wolf::StartupGuard guard(child.process);guard.run(tid);guard.save(bridge);
        require(WaitForSingleObject(thread,1000)==WAIT_OBJECT_0,"locale_bootstrap_timeout");
        DWORD result=1;require(GetExitCodeThread(thread,&result) && result==0,"locale_bootstrap_failed");
        CloseHandle(thread);VirtualFreeEx(child.process,remote,0,MEM_RELEASE);
    } catch(...) {CloseHandle(thread);throw;}
}
}
int wmain(int argc,wchar_t** argv) {
    ProcessInfo child{};PROCESS_INFORMATION injector{};wolf::fs::path bridge;
    try {
        require(argc==4,"usage: WolfLocaleHost.exe BRIDGE_DIR DLL INJECTOR");
        bridge=wolf::fs::canonical(argv[1]);const auto dll=wolf::fs::canonical(argv[2]);const auto loader_exe=wolf::fs::canonical(argv[3]);
        const auto root=bridge.parent_path(),executable=root/L"runtime"/L"Game.exe";
        require(wolf::fs::is_regular_file(root/L"preparation.json"),"prepared_runtime_required");
        const auto profile=wolf::validate_image(executable);
        require(profile.codepage==932,"locale_profile_required");
        const auto session=wolf::utf8(wolf::environment(L"GAMEQAKIT_SESSION_ID"));
        require(!session.empty() && session.find_first_not_of("0123456789abcdef")==std::string::npos &&
                wolf::same_path(wolf::environment(L"GAMEQAKIT_BRIDGE_DIR"),bridge),"locale_environment_mismatch");
        wchar_t desktop[256]{};DWORD needed=0;BOOL job=FALSE;
        const auto expected=wolf::environment(L"GAMEQAKIT_DESKTOP_NAME");
        require(expected.compare(0,10,L"gameqakit-")==0 && GetUserObjectInformationW(GetThreadDesktop(GetCurrentThreadId()),UOI_NAME,desktop,sizeof(desktop),&needed) &&
                expected==desktop && IsProcessInJob(GetCurrentProcess(),nullptr,&job) && job,"owned_isolated_desktop_required");
        wchar_t own_path[32768];require(GetModuleFileNameW(nullptr,own_path,32768)!=0,"locale_host_path_failed");
        const auto libraries=wolf::fs::path(own_path).parent_path();
        require(wolf::hash(libraries/L"LoaderDll.dll")=="82fae0f44f4ca0c9c37907df74cef2415eeb5fae1cf8d4f36f34ffcaf7e3cc0c" &&
                wolf::hash(libraries/L"LocaleEmulator.dll")=="c79c175fdad174aa46a72197d148316299a56f950aaab1b84930d09ee1084a88","unsupported_locale_binary");
        wolf_guard::install(root);
        const auto module=LoadLibraryExW((libraries/L"LoaderDll.dll").c_str(),nullptr,LOAD_WITH_ALTERED_SEARCH_PATH);
        require(module!=nullptr,"locale_loader_load_failed");
        const auto create=reinterpret_cast<CreateLocale>(GetProcAddress(module,"LeCreateProcess"));
        require(create!=nullptr,"locale_create_export_missing");
        LocaleBlock locale;locale.timezone.bias=-540;wcscpy_s(locale.timezone.standard,L"Tokyo Standard Time");wcscpy_s(locale.timezone.daylight,L"Tokyo Daylight Time");
        std::wstring command=L"\""+executable.wstring()+L"\"";STARTUPINFOW startup{};startup.cb=sizeof(startup);startup.lpDesktop=desktop;
        // Debug creation otherwise leaves costly heap checks active after bootstrap detach.
        require(SetEnvironmentVariableW(L"_NO_DEBUG_HEAP",L"1")!=FALSE,"locale_debug_heap_environment_failed");
        const auto status=create(&locale,executable.c_str(),command.data(),executable.parent_path().c_str(),CREATE_SUSPENDED|DEBUG_ONLY_THIS_PROCESS,&startup,&child,nullptr,nullptr,nullptr,nullptr);
        require(status==0 && child.process && child.thread,"locale_create_failed");
        require(IsProcessInJob(child.process,nullptr,&job) && job && GetProcessId(child.process)==child.pid,"locale_child_job_mismatch");
        bootstrap(child,bridge);
        publish(bridge,session,child.pid,"created");
        auto deadline=GetTickCount64()+10000;gameqa::Fields binding;
        while(GetTickCount64()<deadline) {
            const auto raw=wolf::text(bridge/L"connection.ini");
            if(!raw.empty()) {binding=gameqa::ini(raw);break;}
            Sleep(10);
        }
        require(gameqa::get(binding,"session_id")==session && gameqa::integer(binding,"pid")==child.pid &&
                gameqa::integer(binding,"owner_pid")==GetCurrentProcessId() && gameqa::get(binding,"protocol")=="1" &&
                gameqa::get(binding,"desktop")==wolf::utf8(expected) &&
                wolf::same_path(wolf::wide(gameqa::get(binding,"exe_path")),executable),"locale_binding_mismatch");
        std::wstring inject=L"\""+loader_exe.wstring()+L"\" "+std::to_wstring(child.pid)+L" \""+dll.wstring()+L"\" \""+bridge.wstring()+L"\"";
        require(CreateProcessW(loader_exe.c_str(),inject.data(),nullptr,nullptr,FALSE,CREATE_NO_WINDOW,nullptr,root.c_str(),&startup,&injector)!=FALSE,"locale_injector_create_failed");
        require(WaitForSingleObject(injector.hProcess,8000)==WAIT_OBJECT_0,"locale_injector_timeout");
        DWORD exit=1;require(GetExitCodeProcess(injector.hProcess,&exit) && exit==0,"locale_injector_failed");
        CloseHandle(injector.hThread);injector.hThread=nullptr;CloseHandle(injector.hProcess);injector.hProcess=nullptr;
        deadline=GetTickCount64()+5000;std::string stage;
        while(GetTickCount64()<deadline && (stage=wolf::text(bridge/L"plugin-stage.txt")).empty()) Sleep(10);
        require(stage=="ready","locale_bridge_not_ready");publish(bridge,session,child.pid,"ready");
        deadline=GetTickCount64()+10000;bool resume=false;
        while(GetTickCount64()<deadline) {
            const auto raw=wolf::text(bridge/L"start.ini");
            if(!raw.empty()) {
                const auto start=gameqa::ini(raw);
                require(gameqa::get(start,"session_id")==session && gameqa::integer(start,"pid")==child.pid,"locale_start_mismatch");
                resume=true;break;
            }
            Sleep(10);
        }
        require(resume && ResumeThread(child.thread)==1,"locale_resume_failed");
        CloseHandle(child.thread);child.thread=nullptr;publish(bridge,session,child.pid,"running");
        WaitForSingleObject(child.process,INFINITE);CloseHandle(child.process);return 0;
    } catch(const std::exception& error) {
        if(injector.hProcess) {TerminateProcess(injector.hProcess,2);CloseHandle(injector.hProcess);}
        if(injector.hThread) CloseHandle(injector.hThread);
        if(child.process) {TerminateProcess(child.process,2);WaitForSingleObject(child.process,2000);CloseHandle(child.process);}
        if(child.thread) CloseHandle(child.thread);
        if(!bridge.empty()) {std::ofstream out(bridge/L"launch-error.txt");out<<error.what();}
        std::cerr<<error.what()<<"\n";return 1;
    }
}
