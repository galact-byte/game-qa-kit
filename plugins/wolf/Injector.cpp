#include <windows.h>
#include <tlhelp32.h>
#include "Native.hpp"
#include "Wire.hpp"
#include <iostream>

namespace {
DWORD parent_of(DWORD pid) {
    HANDLE snapshot=CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS,0);
    if(snapshot==INVALID_HANDLE_VALUE) throw std::runtime_error("process_snapshot_failed");
    PROCESSENTRY32W item{};item.dwSize=sizeof(item);DWORD parent=0;
    if(Process32FirstW(snapshot,&item)) do { if(item.th32ProcessID==pid) {parent=item.th32ParentProcessID;break;} }
        while(Process32NextW(snapshot,&item));
    CloseHandle(snapshot);return parent;
}
uintptr_t remote_module(DWORD pid,const std::wstring& name) {
    HANDLE snapshot=CreateToolhelp32Snapshot(TH32CS_SNAPMODULE|TH32CS_SNAPMODULE32,pid);
    if(snapshot==INVALID_HANDLE_VALUE) throw std::runtime_error("module_snapshot_failed:"+std::to_string(GetLastError()));
    MODULEENTRY32W item{};item.dwSize=sizeof(item);uintptr_t address=0;
    if(Module32FirstW(snapshot,&item)) do { if(!_wcsicmp(item.szModule,name.c_str())) {address=reinterpret_cast<uintptr_t>(item.modBaseAddr);break;} }
        while(Module32NextW(snapshot,&item));
    CloseHandle(snapshot);if(!address) throw std::runtime_error("loader_module_missing");return address;
}
}
int wmain(int argc,wchar_t** argv) {
    HANDLE process=nullptr,thread=nullptr;void* remote=nullptr;bool finished=false;
    try {
        if(argc!=4) throw std::runtime_error("usage: Injector.exe PID DLL BRIDGE_DIR");
        wchar_t* end=nullptr;const auto value=wcstoul(argv[1],&end,10);
        if(!value || *end) throw std::runtime_error("invalid_pid");
        const DWORD pid=value;const auto dll=wolf::fs::canonical(argv[2]);const auto bridge=wolf::fs::canonical(argv[3]);
        const auto binding=gameqa::ini(wolf::text(bridge/L"connection.ini"));
        const auto owner=parent_of(GetCurrentProcessId());
        if(!owner || parent_of(pid)!=owner || gameqa::integer(binding,"owner_pid")!=owner ||
           gameqa::integer(binding,"pid")!=pid || gameqa::get(binding,"session_id").empty() ||
           gameqa::get(binding,"protocol")!="1") throw std::runtime_error("owned_process_binding_required");
        const auto executable=wolf::fs::path(wolf::wide(gameqa::get(binding,"exe_path")));
        if(!wolf::same_path(executable,bridge.parent_path()/L"runtime"/L"Game.exe") ||
           !wolf::fs::is_regular_file(bridge.parent_path()/L"preparation.json")) throw std::runtime_error("prepared_runtime_required");
        wolf::validate_image(executable);
        process=OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION|PROCESS_CREATE_THREAD|PROCESS_VM_OPERATION|PROCESS_VM_WRITE|PROCESS_VM_READ,FALSE,pid);
        if(!process) throw std::runtime_error("open_owned_process_failed");
        wchar_t actual[32768];DWORD length=32768;BOOL job=FALSE,wow=FALSE;
        if(!QueryFullProcessImageNameW(process,0,actual,&length) || !wolf::same_path(std::wstring(actual,length),executable) ||
           !IsProcessInJob(process,nullptr,&job) || !job || !IsWow64Process(process,&wow) || !wow)
            throw std::runtime_error("target_path_job_or_architecture_mismatch");
        // A suspended WOW64 process has no initialized 32-bit loader list yet.
        // Initialize Windows DLL loading on a return-only thread; the game entry stays suspended.
        const unsigned char bootstrap[]={0x31,0xc0,0xc2,0x04,0x00};
        remote=VirtualAllocEx(process,nullptr,sizeof(bootstrap),MEM_COMMIT|MEM_RESERVE,PAGE_READWRITE);
        SIZE_T initialized=0;DWORD prior=0;
        if(!remote || !WriteProcessMemory(process,remote,bootstrap,sizeof(bootstrap),&initialized) || initialized!=sizeof(bootstrap) ||
           !VirtualProtectEx(process,remote,sizeof(bootstrap),PAGE_EXECUTE_READ,&prior) ||
           !FlushInstructionCache(process,remote,sizeof(bootstrap))) throw std::runtime_error("loader_bootstrap_write_failed");
        thread=CreateRemoteThread(process,nullptr,0,reinterpret_cast<LPTHREAD_START_ROUTINE>(remote),nullptr,0,nullptr);
        if(!thread || WaitForSingleObject(thread,5000)!=WAIT_OBJECT_0) throw std::runtime_error("loader_bootstrap_timeout");
        finished=true;DWORD boot_result=1;
        if(!GetExitCodeThread(thread,&boot_result) || boot_result) throw std::runtime_error("loader_bootstrap_failed");
        VirtualFreeEx(process,remote,0,MEM_RELEASE);remote=nullptr;CloseHandle(thread);thread=nullptr;finished=false;
        const auto load=GetProcAddress(GetModuleHandleW(L"kernel32.dll"),"LoadLibraryW");HMODULE module=nullptr;
        if(!load || !GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                                       reinterpret_cast<LPCWSTR>(load),&module)) throw std::runtime_error("loader_unavailable");
        wchar_t module_path[32768];if(!GetModuleFileNameW(module,module_path,32768)) throw std::runtime_error("loader_path_missing");
        const auto remote_load=remote_module(pid,wolf::fs::path(module_path).filename().wstring())+
            reinterpret_cast<uintptr_t>(load)-reinterpret_cast<uintptr_t>(module);
        const auto bytes=(dll.wstring().size()+1)*sizeof(wchar_t);
        remote=VirtualAllocEx(process,nullptr,bytes,MEM_COMMIT|MEM_RESERVE,PAGE_READWRITE);
        SIZE_T written=0;
        if(!remote || !WriteProcessMemory(process,remote,dll.c_str(),bytes,&written) || written!=bytes)
            throw std::runtime_error("remote_loader_write_failed");
        thread=CreateRemoteThread(process,nullptr,0,reinterpret_cast<LPTHREAD_START_ROUTINE>(remote_load),remote,0,nullptr);
        if(!thread || WaitForSingleObject(thread,5000)!=WAIT_OBJECT_0) throw std::runtime_error("remote_loader_timeout");
        finished=true;DWORD result=0;
        if(!GetExitCodeThread(thread,&result) || !result) throw std::runtime_error("dll_load_failed");
        VirtualFreeEx(process,remote,0,MEM_RELEASE);remote=nullptr;
        CloseHandle(thread);thread=nullptr;CloseHandle(process);process=nullptr;
        std::cout<<"Wolf DLL loaded; host must verify plugin-stage before resuming the owned process\n";return 0;
    } catch(const std::exception& e) {
        if(thread) CloseHandle(thread);
        if(remote && process && finished) VirtualFreeEx(process,remote,0,MEM_RELEASE);
        if(process) CloseHandle(process);
        std::cerr<<e.what()<<"\n";return 1;
    }
}
