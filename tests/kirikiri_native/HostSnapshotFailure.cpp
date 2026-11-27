// Exercise the real host error path with a deterministic post-wait snapshot fault.
#include "../../plugins/kirikiri/StartupGuard.hpp"
#include "../../plugins/kirikiri/Guard.hpp"
static unsigned snapshot_reads=0;
static bool exit_before_read=false,exit_after_wait=false;
static HANDLE target_process=nullptr;
static unsigned terminal_fault=0;
static DWORD WINAPI snapshot_failure_wait(HANDLE process,DWORD timeout){
    const auto result=WaitForSingleObject(process,timeout);
    if(timeout==25){
        target_process=process;
        if(exit_after_wait && result==WAIT_TIMEOUT){
            Sleep(300);
            if(WaitForSingleObject(process,0)!=WAIT_OBJECT_0)
                throw std::runtime_error("test_target_did_not_exit");
        }
    }
    return result;
}
static BOOL WINAPI snapshot_failure_read(HANDLE process,LPCVOID address,LPVOID buffer,SIZE_T bytes,SIZE_T* got){
    if(bytes==sizeof(kirikiri_guard::State) && ++snapshot_reads==3 && !exit_after_wait && !terminal_fault){
        if(exit_before_read){
            const auto target=target_process?target_process:process;
            if(!TerminateProcess(target,0)||WaitForSingleObject(target,3000)!=WAIT_OBJECT_0)
                throw std::runtime_error("test_target_exit_failed");
            if(got)*got=0;SetLastError(ERROR_PARTIAL_COPY);return FALSE;
        }
        if(got)*got=0;SetLastError(ERROR_PARTIAL_COPY);return FALSE;
    }
    const auto result=ReadProcessMemory(process,address,buffer,bytes,got);
    if(result && terminal_fault && bytes==sizeof(kirikiri_guard::State) && target_process &&
       WaitForSingleObject(target_process,0)==WAIT_OBJECT_0){
        auto& state=*static_cast<kirikiri_guard::State*>(buffer);
        if(terminal_fault==1)state.pid=0;
        if(terminal_fault==2)state.reporting=1;
        if(terminal_fault==3){state.denied=1;state.reporting=0;}
    }
    return result;
}
#define ReadProcessMemory snapshot_failure_read
#define WaitForSingleObject snapshot_failure_wait
#define wmain real_host_main
#include "../../plugins/kirikiri/GuardHost.cpp"
#undef wmain
#undef ReadProcessMemory
#undef WaitForSingleObject
int wmain(int argc,wchar_t** argv){
    exit_before_read=argc==4 && std::filesystem::exists(std::filesystem::path(argv[1])/L"exit-before-read");
    exit_after_wait=argc==4 && std::filesystem::exists(std::filesystem::path(argv[1])/L"exit-after-wait");
    if(argc==4){
        const auto root=std::filesystem::path(argv[1]);
        if(std::filesystem::exists(root/L"invalid-terminal-identity"))terminal_fault=1;
        if(std::filesystem::exists(root/L"incomplete-terminal-report"))terminal_fault=2;
        if(std::filesystem::exists(root/L"missing-terminal-report"))terminal_fault=3;
    }
    return real_host_main(argc,argv);
}
