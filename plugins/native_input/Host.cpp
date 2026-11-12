#include "StartupGuard.hpp"
#include <iostream>
#define GAMEQA_STRING_VALUE_(value) #value
#define GAMEQA_STRING_VALUE(value) GAMEQA_STRING_VALUE_(value)
namespace fs=std::filesystem;
using namespace native_platform;
static void require(bool value,const char* reason){if(!value)throw std::runtime_error(reason);}
static void boundary(const fs::path& path){
    auto current=path;
    while(!current.empty()){
        const auto attrs=GetFileAttributesW(current.c_str());
        require(attrs!=INVALID_FILE_ATTRIBUTES&&!(attrs&FILE_ATTRIBUTE_REPARSE_POINT),"reparse_or_missing_path");
        auto parent=current.parent_path();if(parent==current)break;current=parent;
    }
}
static void x86(const fs::path& path){
    std::ifstream in(path,std::ios::binary);IMAGE_DOS_HEADER dos{};in.read(reinterpret_cast<char*>(&dos),sizeof(dos));
    require(bool(in)&&dos.e_magic==IMAGE_DOS_SIGNATURE&&dos.e_lfanew>=64&&dos.e_lfanew<1048576,"invalid_pe_image");
    in.seekg(dos.e_lfanew);DWORD signature=0;IMAGE_FILE_HEADER header{};
    in.read(reinterpret_cast<char*>(&signature),sizeof(signature));in.read(reinterpret_cast<char*>(&header),sizeof(header));
    require(bool(in)&&signature==IMAGE_NT_SIGNATURE&&header.Machine==IMAGE_FILE_MACHINE_I386,"x86_image_required");
}
static DWORD remote(HANDLE process,void* function,void* parameter,bool startup,const fs::path& bridge){
    DWORD tid=0;HANDLE thread=CreateRemoteThread(process,nullptr,0,reinterpret_cast<LPTHREAD_START_ROUTINE>(function),parameter,0,&tid);
    if(!thread)throw std::runtime_error("remote_thread_failed:"+std::to_string(GetLastError()));
    try{
        if(startup){StartupGuard guard(process);guard.run(tid);guard.save(bridge);}
        require(WaitForSingleObject(thread,8000)==WAIT_OBJECT_0,"remote_timeout");DWORD result=0;
        require(GetExitCodeThread(thread,&result)!=FALSE,"remote_exit_failed");CloseHandle(thread);return result;
    }catch(...){CloseHandle(thread);throw;}
}
int wmain(int argc,wchar_t** argv){
    PROCESS_INFORMATION process{};HANDLE job=nullptr;fs::path root;
    try{
        require(argc==4,"usage: host SESSION_ROOT DLL DLL_SHA256");
        boundary(fs::absolute(argv[1]));boundary(fs::absolute(argv[2]));root=fs::canonical(argv[1]);auto dll=fs::canonical(argv[2]);
#ifdef GAMEQA_OWNED_IMAGE
        auto exe=root/L"runtime/Target.exe";const std::string expected=GAMEQA_STRING_VALUE(GAMEQA_OWNED_IMAGE);
#else
        auto exe=root/L"runtime/game.exe";const std::string expected="8e123a4c07cfbc9143c266faf87bb66ef3600ab1254ee6d294fbaf685bb502a7";
        require(fs::is_regular_file(root/L"runtime/data.dts")&&fs::is_regular_file(root/L"preparation.json"),"prepared_data_mode_required");
#endif
        boundary(exe);require(hash(exe)==expected,"unsupported_native_image");x86(exe);x86(dll);
        const auto dll_hash=hash(dll);require(dll_hash==utf8(argv[3]),"dll_hash_mismatch");
        const auto bridge=root/L"bridge";require(fs::is_directory(bridge)&&fs::is_directory(root/L"temp"),"session_directories_missing");
        require(!fs::exists(bridge/L"connection.json"),"new_session_required");
        BYTE random[16];require(BCryptGenRandom(nullptr,random,sizeof(random),BCRYPT_USE_SYSTEM_PREFERRED_RNG)>=0,"session_random_failed");
        std::ostringstream session;for(auto value:random)session<<std::hex<<std::setw(2)<<std::setfill('0')<<static_cast<unsigned>(value);
        SetEnvironmentVariableW(L"GAMEQAKIT_NATIVE_ROOT",root.c_str());
        SetEnvironmentVariableW(L"GAMEQAKIT_NATIVE_SESSION",wide(session.str()).c_str());
        SetEnvironmentVariableW(L"GAMEQAKIT_NATIVE_DLL_SHA256",wide(dll_hash).c_str());
        SetEnvironmentVariableW(L"GAMEQAKIT_DENIED_FILE",(root.parent_path()/(root.filename().wstring()+L"-outside.tmp")).c_str());
        SetEnvironmentVariableW(L"TEMP",(root/L"temp").c_str());SetEnvironmentVariableW(L"TMP",(root/L"temp").c_str());
        SetEnvironmentVariableW(L"_NO_DEBUG_HEAP",L"1");
        job=CreateJobObjectW(nullptr,nullptr);require(job!=nullptr,"job_create_failed");
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION limit{};limit.BasicLimitInformation.LimitFlags=JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        require(SetInformationJobObject(job,JobObjectExtendedLimitInformation,&limit,sizeof(limit))!=FALSE,"job_limit_failed");
        STARTUPINFOW startup{};startup.cb=sizeof(startup);startup.dwFlags=STARTF_USESHOWWINDOW;startup.wShowWindow=SW_SHOWNOACTIVATE;
        std::wstring command=L"\""+exe.wstring()+L"\"";
        require(CreateProcessW(exe.c_str(),command.data(),nullptr,nullptr,FALSE,CREATE_SUSPENDED|DEBUG_ONLY_THIS_PROCESS,nullptr,exe.parent_path().c_str(),&startup,&process)!=FALSE,"target_create_failed");
        require(AssignProcessToJobObject(job,process.hProcess)!=FALSE,"job_assign_failed");
        {std::ofstream out(root/L"process.json");out<<"{\"pid\":"<<process.dwProcessId<<",\"host_pid\":"<<GetCurrentProcessId()<<"}";}
        SIZE_T written=0;const auto bytes=(dll.wstring().size()+1)*sizeof(wchar_t);
        auto memory=VirtualAllocEx(process.hProcess,nullptr,bytes,MEM_COMMIT|MEM_RESERVE,PAGE_READWRITE);
        require(memory&&WriteProcessMemory(process.hProcess,memory,dll.c_str(),bytes,&written)&&written==bytes,"dll_argument_failed");
        require(hash(exe)==expected&&hash(dll)==dll_hash,"image_changed_before_load");
        const auto module=remote(process.hProcess,reinterpret_cast<void*>(GetProcAddress(GetModuleHandleW(L"kernel32.dll"),"LoadLibraryW")),memory,true,bridge);
        require(module!=0,"dll_load_failed");VirtualFreeEx(process.hProcess,memory,0,MEM_RELEASE);
        auto local=LoadLibraryExW(dll.c_str(),nullptr,DONT_RESOLVE_DLL_REFERENCES);require(local!=nullptr,"local_dll_map_failed");
        auto entry=GetProcAddress(local,"_StartNative@4");if(!entry){FreeLibrary(local);throw std::runtime_error("start_export_missing");}
        const auto offset=reinterpret_cast<uintptr_t>(entry)-reinterpret_cast<uintptr_t>(local);FreeLibrary(local);
        require(remote(process.hProcess,reinterpret_cast<void*>(module+offset),nullptr,false,bridge)==0,"native_initialize_failed");
        require(fs::is_regular_file(bridge/L"guard-ready.json"),"guard_not_ready");
        require(ResumeThread(process.hThread)==1,"target_resume_failed");CloseHandle(process.hThread);process.hThread=nullptr;
        auto start=GetTickCount64();bool connected=false;std::string reason="stopped";
        while(WaitForSingleObject(process.hProcess,50)==WAIT_TIMEOUT){
            if(fs::exists(root/L"stop"))break;
            if(fs::exists(bridge/L"fatal.json")){reason="bridge_failed";break;}
            if(fs::exists(bridge/L"status.json")){
                connected=true;
                const auto age=fs::file_time_type::clock::now()-fs::last_write_time(bridge/L"status.json");
                if(age>std::chrono::seconds(3)){reason="bridge_heartbeat_timeout";break;}
            }else if(GetTickCount64()-start>=15000){reason="startup_timeout";break;}
        }
        DWORD exit=0;GetExitCodeProcess(process.hProcess,&exit);
        if(exit==STILL_ACTIVE){require(TerminateJobObject(job,82)!=FALSE,"job_terminate_failed");require(WaitForSingleObject(process.hProcess,3000)==WAIT_OBJECT_0,"job_reap_failed");GetExitCodeProcess(process.hProcess,&exit);}
        {std::ofstream out(root/L"host-result.json");out<<"{\"pid\":"<<process.dwProcessId<<",\"target_exit_code\":"<<exit<<",\"connected\":"<<(connected?"true":"false")<<",\"reason\":"<<native_input::quote(reason)<<"}";}
        CloseHandle(process.hProcess);CloseHandle(job);return reason=="stopped"?0:2;
    }catch(const std::exception& error){
        if(job)TerminateJobObject(job,83);else if(process.hProcess)TerminateProcess(process.hProcess,83);
        if(process.hProcess){WaitForSingleObject(process.hProcess,3000);CloseHandle(process.hProcess);}if(process.hThread)CloseHandle(process.hThread);if(job)CloseHandle(job);
        std::cerr<<error.what()<<'\n';return 1;
    }
}
