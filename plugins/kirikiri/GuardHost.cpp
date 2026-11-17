#include "StartupGuard.hpp"
#include "Guard.hpp"
#include <iostream>
#define GQ_STRING_(x) #x
#define GQ_STRING(x) GQ_STRING_(x)
namespace fs=std::filesystem;
using namespace native_platform;
static void require(bool value,const char* reason){if(!value)throw std::runtime_error(reason);}
static DWORD remote(HANDLE process,void* function,void* parameter,bool startup,const fs::path& bridge){
    DWORD tid=0;auto thread=CreateRemoteThread(process,nullptr,0,reinterpret_cast<LPTHREAD_START_ROUTINE>(function),parameter,0,&tid);
    if(!thread) throw std::runtime_error("remote_thread_failed:"+std::to_string(GetLastError()));
    try{
        if(startup){kirikiri_startup::StartupGuard guard(process);guard.run(tid);guard.save(bridge);require(guard.clean(),"startup_write_denied");}
        require(WaitForSingleObject(thread,8000)==WAIT_OBJECT_0,"remote_timeout");DWORD result=0;
        require(GetExitCodeThread(thread,&result)!=FALSE,"remote_result_failed");CloseHandle(thread);return result;
    }catch(...){CloseHandle(thread);throw;}
}
static void boundary(fs::path path){
    while(!path.empty()){
        const auto attrs=GetFileAttributesW(path.c_str());
        require(attrs!=INVALID_FILE_ATTRIBUTES && !(attrs&FILE_ATTRIBUTE_REPARSE_POINT),"unsafe_path");
        auto parent=path.parent_path();if(parent==path)break;path=parent;
    }
}
static kirikiri_guard::State snapshot(HANDLE process,DWORD address){
    kirikiri_guard::State value{};SIZE_T got=0;
    require(ReadProcessMemory(process,reinterpret_cast<void*>(address),&value,sizeof(value),&got)&&got==sizeof(value),"guard_state_read_failed");
    require(value.magic==0x4b514731 && value.pid==GetProcessId(process),"guard_state_identity_failed");
    return value;
}
int wmain(int argc,wchar_t** argv){
    PROCESS_INFORMATION pi{};HANDLE job=nullptr;fs::path root;bool supervised=false;
    try{
        require(argc==4,"usage: KiriKiriHost SESSION_ROOT GUARD_DLL GUARD_SHA256");
        root=fs::absolute(argv[1]);auto dll=fs::absolute(argv[2]);boundary(root);boundary(dll);root=fs::canonical(root);dll=fs::canonical(dll);
        const auto game=root/L"game",exe=game/L"runtime.exe",bridge=root/L"bridge";
        boundary(exe);require(hash(dll)==utf8(argv[3]),"guard_dll_hash_mismatch");
#ifdef GAMEQA_OWNED_IMAGE
        const std::string expected=GQ_STRING(GAMEQA_OWNED_IMAGE);
#else
        const std::string expected="8db00ce0cd63748296d4e13b6916a4f1b5b166078f806d731202d15b210efe11";
        require(fs::is_regular_file(root/L"preparation.json"),"prepared_commercial_required");
        require(hash(game/L"data.xp3")=="b3c3d86d6e6a5c34100e1e2d8a6cde59ca7576fe1c6d8f0f0ff7c100a7ab045e","archive_hash_mismatch");
        require(hash(game/L"wuvorbis.dll")=="302b1eb97ed0f6110904e84c11bf7436e4cdddd414f4d24d105aba687f32b47f","audio_hash_mismatch");
        require(hash(game/L"AfterInit2.tjs")==GQ_STRING(GAMEQA_AFTERINIT_HASH)&&hash(game/L"KagBridge.tjs")==GQ_STRING(GAMEQA_KAG_HASH),"bridge_hash_mismatch");
#endif
        require(hash(exe)==expected,"target_hash_mismatch");
        require(fs::is_directory(bridge)&&!fs::exists(root/L"process.json")&&!fs::exists(root/L"resume"),"fresh_session_required");
        for(auto name:{L"savedata",L"appdata",L"localappdata",L"tmp"}){boundary(root/name);}
        SetEnvironmentVariableW(L"GAMEQAKIT_KIRIKIRI_ROOT",root.c_str());
        for(const auto& entry:std::vector<std::pair<const wchar_t*,const wchar_t*>>{{L"APPDATA",L"appdata"},{L"LOCALAPPDATA",L"localappdata"},{L"TEMP",L"tmp"},{L"TMP",L"tmp"}})
            SetEnvironmentVariableW(entry.first,(root/entry.second).c_str());
        SetEnvironmentVariableW(L"_NO_DEBUG_HEAP",L"1");
        job=CreateJobObjectW(nullptr,nullptr);require(job!=nullptr,"job_create_failed");
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits{};limits.BasicLimitInformation.LimitFlags=JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE|JOB_OBJECT_LIMIT_ACTIVE_PROCESS;
        limits.BasicLimitInformation.ActiveProcessLimit=1;
        require(SetInformationJobObject(job,JobObjectExtendedLimitInformation,&limits,sizeof(limits))!=FALSE,"job_limit_failed");
        STARTUPINFOW si{};si.cb=sizeof(si);si.dwFlags=STARTF_USESHOWWINDOW;si.wShowWindow=SW_SHOWNOACTIVATE;
        // Packed projects must select the archive; an explicit directory skips it.
        const auto project=fs::is_regular_file(game/L"data.xp3")?game/L"data.xp3":game;
        // Quote engine options as complete arguments, including roots containing spaces.
        auto command=L"\""+exe.wstring()+L"\" \""+project.wstring()+L"\" \"-datapath="+(root/L"savedata").wstring()+L"\" \"-gameqadir="+bridge.wstring()+L"\"";
#if defined(GAMEQA_OWNED_IMAGE) || defined(GAMEQA_KIRIKIRI_COMMERCIAL_COMPAT)
        command+=L" -wheel=message -joypad=no -dbstyle=gdi -usedb=yes";
#endif
        require(CreateProcessW(exe.c_str(),command.data(),nullptr,nullptr,FALSE,CREATE_SUSPENDED|DEBUG_ONLY_THIS_PROCESS,nullptr,game.c_str(),&si,&pi)!=FALSE,"target_create_failed");
        require(AssignProcessToJobObject(job,pi.hProcess)!=FALSE,"job_assign_failed");
        const auto bytes=(dll.wstring().size()+1)*sizeof(wchar_t);SIZE_T written=0;
        auto memory=VirtualAllocEx(pi.hProcess,nullptr,bytes,MEM_RESERVE|MEM_COMMIT,PAGE_READWRITE);
        require(memory&&WriteProcessMemory(pi.hProcess,memory,dll.c_str(),bytes,&written)&&written==bytes,"dll_argument_failed");
        const auto module=remote(pi.hProcess,reinterpret_cast<void*>(GetProcAddress(GetModuleHandleW(L"kernel32.dll"),"LoadLibraryW")),memory,true,bridge);
        require(module!=0,"guard_load_failed");VirtualFreeEx(pi.hProcess,memory,0,MEM_RELEASE);
        auto local=LoadLibraryExW(dll.c_str(),nullptr,DONT_RESOLVE_DLL_REFERENCES);require(local!=nullptr,"local_guard_map_failed");
        const auto entry=GetProcAddress(local,"_StartKiriKiri@4");require(entry!=nullptr,"guard_export_missing");
        const auto offset=reinterpret_cast<DWORD>(entry)-reinterpret_cast<DWORD>(local);FreeLibrary(local);
        const auto state_address=remote(pi.hProcess,reinterpret_cast<void*>(module+offset),nullptr,false,bridge);
        require(state_address!=0,"guard_start_failed");
        auto state=snapshot(pi.hProcess,state_address);
#ifdef GAMEQA_KIRIKIRI_COMMERCIAL_COMPAT
        require(state.shell_experiment==2,"commercial_compat_guard_required");
#elif defined(GAMEQA_OWNED_IMAGE)
        require(state.shell_experiment<=1,"commercial_guard_not_allowed_in_fixture_host");
#else
        require(state.shell_experiment==0,"fixture_guard_not_allowed");
#endif
        if(state.ready!=1||state.denied)
            throw std::runtime_error(std::string("guard_not_clean:")+state.api+":"+utf8(state.path)+":"+std::to_string(state.control));
        {std::ofstream out(bridge/L"native-guard.json");out<<"{\"pid\":"<<pi.dwProcessId<<",\"denied\":"<<state.denied<<",\"ready\":true,\"denial_policy\":"<<state.shell_experiment<<",\"runtime_sha256\":"<<native_input::quote(expected)<<"}";require(bool(out),"guard_record_failed");}
        {std::ofstream out(root/L"process.json");out<<"{\"pid\":"<<pi.dwProcessId<<",\"guard_ready\":true,\"all_threads_suspended\":true}";require(bool(out),"process_record_failed");}
        supervised=true;
        const auto deadline=GetTickCount64()+15000;
        while(!fs::exists(root/L"resume")){
            require(GetTickCount64()<deadline,"supervisor_start_timeout");
            require(!fs::exists(root/L"stop"),"stopped_before_resume");Sleep(10);
        }
        require(hash(exe)==expected&&hash(dll)==utf8(argv[3]),"input_changed_before_resume");
        state=snapshot(pi.hProcess,state_address);
        if(state.ready!=1||state.denied)
            throw std::runtime_error(std::string("guard_changed_before_resume:")+state.api+":"+utf8(state.path)+":"+std::to_string(state.control));
        const auto resume_process=reinterpret_cast<NTSTATUS (NTAPI*)(HANDLE)>(GetProcAddress(GetModuleHandleW(L"ntdll.dll"),"NtResumeProcess"));
        require(resume_process && resume_process(pi.hProcess)>=0,"process_resume_failed");
        require(ResumeThread(pi.hThread)==1,"primary_resume_failed");CloseHandle(pi.hThread);pi.hThread=nullptr;
        std::string reason="target_exit";LONG shell_reported=-1, shell_create_reported=-1, cng_reported=-1;
        while(WaitForSingleObject(pi.hProcess,25)==WAIT_TIMEOUT){
            state=snapshot(pi.hProcess,state_address);
            if(state.shell_denied!=shell_reported){
                std::ofstream out(bridge/L"nonfatal-rejections.json");
                out<<"{\"api\":\"NtOpenSection(write)\",\"path\":"<<native_input::quote(utf8(state.shell_counter))
                   <<",\"access\":6,\"status\":\"0xc0000022\",\"fatal\":false,\"count\":"<<state.shell_denied<<"}";
                require(bool(out),"guard_record_failed");shell_reported=state.shell_denied;
            }
            if(state.shell_create_denied!=shell_create_reported){
                std::ofstream out(bridge/L"nonfatal-creation-rejections.json");
                out<<"{\"api\":\"NtCreateSection(named)\",\"path\":"<<native_input::quote(utf8(state.shell_counter))
                   <<",\"access\":983047,\"status\":\"0xc0000022\",\"fatal\":false,\"count\":"<<state.shell_create_denied<<"}";
                require(bool(out),"guard_record_failed");shell_create_reported=state.shell_create_denied;
            }
            if(state.cng_denied!=cng_reported){
                std::ofstream out(bridge/L"nonfatal-cng-rejections.json");
                out<<"{\"api\":\"NtDeviceIoControlFile\",\"path\":\"\\\\Device\\\\CNG\",\"control\":3735560,"
                   <<"\"status\":\"0xc0000022\",\"fatal\":false,\"count\":"<<state.cng_denied<<"}";
                require(bool(out),"guard_record_failed");cng_reported=state.cng_denied;
            }
            if(state.ready!=1||state.denied){
                reason="write_guard_denied";
                {std::ofstream out(bridge/L"guard-denial.json");out<<"{\"denied\":"<<state.denied<<",\"api\":"<<native_input::quote(state.api)<<",\"path\":"<<native_input::quote(utf8(state.path))<<",\"control\":"<<state.control<<"}";}
                break;
            }
            if(fs::exists(root/L"stop")){reason="stopped";break;}
        }
        DWORD target_exit=STILL_ACTIVE;GetExitCodeProcess(pi.hProcess,&target_exit);
        require(TerminateJobObject(job,82)!=FALSE,"job_terminate_failed");require(WaitForSingleObject(pi.hProcess,3000)==WAIT_OBJECT_0,"target_reap_failed");
        if(reason=="target_exit" && target_exit!=0)reason="target_failed";
        {std::ofstream out(root/L"host-result.json");out<<"{\"reason\":"<<native_input::quote(reason)<<",\"target_exit\":"<<target_exit<<",\"target_alive\":false}";}
        CloseHandle(pi.hProcess);CloseHandle(job);return reason=="write_guard_denied"||reason=="target_failed"?2:0;
    }catch(const std::exception& error){
        if(job)TerminateJobObject(job,83);else if(pi.hProcess)TerminateProcess(pi.hProcess,83);
        DWORD target_exit=STILL_ACTIVE;DWORD reap=WAIT_FAILED;
        if(pi.hProcess){reap=WaitForSingleObject(pi.hProcess,3000);GetExitCodeProcess(pi.hProcess,&target_exit);CloseHandle(pi.hProcess);}if(pi.hThread)CloseHandle(pi.hThread);
        if(job)CloseHandle(job);
        if(supervised){
            try{
                std::ofstream out(root/L"host-result.json");
                out<<"{\"reason\":\"host_failed\",\"error\":"<<native_input::quote(error.what())
                   <<",\"target_exit\":"<<target_exit<<",\"target_alive\":"<<(reap==WAIT_OBJECT_0?"false":"true")<<"}";
                out.flush();if(!out)std::cerr<<"host_result_write_failed\n";
            }catch(const std::exception& record_error){std::cerr<<"host_result_write_failed:"<<record_error.what()<<'\n';}
        }
        std::cerr<<error.what()<<'\n';return 1;
    }
}
