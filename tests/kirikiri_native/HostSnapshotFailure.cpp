// Exercise the real host error path with a deterministic post-wait snapshot fault.
#include "../../plugins/kirikiri/StartupGuard.hpp"
#include "../../plugins/kirikiri/Guard.hpp"
static unsigned snapshot_reads=0;
static bool exit_before_read=false;
static BOOL WINAPI snapshot_failure_read(HANDLE process,LPCVOID address,LPVOID buffer,SIZE_T bytes,SIZE_T* got){
    if(bytes==sizeof(kirikiri_guard::State) && ++snapshot_reads==3){
        if(exit_before_read){
            if(!TerminateProcess(process,0)||WaitForSingleObject(process,3000)!=WAIT_OBJECT_0)
                throw std::runtime_error("test_target_exit_failed");
            return ReadProcessMemory(process,address,buffer,bytes,got);
        }
        if(got)*got=0;SetLastError(ERROR_PARTIAL_COPY);return FALSE;
    }
    return ReadProcessMemory(process,address,buffer,bytes,got);
}
#define ReadProcessMemory snapshot_failure_read
#define wmain real_host_main
#include "../../plugins/kirikiri/GuardHost.cpp"
#undef wmain
#undef ReadProcessMemory
int wmain(int argc,wchar_t** argv){
    exit_before_read=argc==4 && std::filesystem::exists(std::filesystem::path(argv[1])/L"exit-before-read");
    return real_host_main(argc,argv);
}
