// Reproduce a syscall breakpoint queued between the last debug event and detach.
#include "WriteGuard.hpp"
#include "JsonWire.hpp"
#include <iostream>

namespace {
DWORD stopping_thread=0;
bool delay_handoff=false,stopping_event=false;
BOOL WINAPI observe_wait(LPDEBUG_EVENT event,DWORD timeout) {
    const auto result=WaitForDebugEvent(event,timeout);
    stopping_event=result && event->dwDebugEventCode==EXIT_THREAD_DEBUG_EVENT && event->dwThreadId==stopping_thread;
    return result;
}
BOOL WINAPI observe_continue(DWORD pid,DWORD tid,DWORD status) {
    const auto result=ContinueDebugEvent(pid,tid,status);
    // A scheduler can deschedule the host here while another target thread hits INT3.
    if(result && delay_handoff && stopping_event) Sleep(200);
    return result;
}
DWORD WINAPI read_loop(void*) {
    const auto deadline=GetTickCount64()+2000;
    while(GetTickCount64()<deadline) {
        auto file=CreateFileW(L"NUL",GENERIC_READ,FILE_SHARE_READ|FILE_SHARE_WRITE,nullptr,OPEN_EXISTING,0,nullptr);
        if(file!=INVALID_HANDLE_VALUE) CloseHandle(file);
        Sleep(1);
    }
    return 0;
}
DWORD WINAPI stop_soon(void*) { Sleep(150);return 0; }
}
#define WaitForDebugEvent observe_wait
#define ContinueDebugEvent observe_continue
#include "StartupGuard.hpp"
#undef WaitForDebugEvent
#undef ContinueDebugEvent

int main() {
    wchar_t image[32768]{};GetModuleFileNameW(nullptr,image,32768);
    STARTUPINFOW startup{};startup.cb=sizeof(startup);PROCESS_INFORMATION process{};
    auto command=std::wstring(L"\"")+image+L"\" --child";
    if(!CreateProcessW(image,command.data(),nullptr,nullptr,FALSE,CREATE_SUSPENDED|DEBUG_ONLY_THIS_PROCESS,
                       nullptr,nullptr,&startup,&process)) return 2;
    HANDLE reader=nullptr,stopper=nullptr;int result=1;
    try {
        MEMORY_BASIC_INFORMATION mapped{};
        if(!VirtualQueryEx(process.hProcess,reinterpret_cast<void*>(read_loop),&mapped,sizeof(mapped)) ||
           mapped.AllocationBase!=GetModuleHandleW(nullptr)) throw std::runtime_error("fixture_base_mismatch");
        reader=CreateRemoteThread(process.hProcess,nullptr,0,read_loop,nullptr,0,nullptr);
        stopper=CreateRemoteThread(process.hProcess,nullptr,0,stop_soon,nullptr,0,&stopping_thread);
        if(!reader || !stopper) throw std::runtime_error("fixture_thread_failed");
        delay_handoff=true;
        native_platform::StartupGuard guard(process.hProcess);
        guard.run(stopping_thread);
        if(WaitForSingleObject(reader,3000)!=WAIT_OBJECT_0)
            throw std::runtime_error("reader_did_not_finish_after_handoff");
        DWORD reader_code=0;
        if(!GetExitCodeThread(reader,&reader_code) || reader_code!=0)
            throw std::runtime_error("reader_failed_after_handoff:"+std::to_string(reader_code));
        const auto wait=WaitForSingleObject(process.hProcess,100);
        DWORD code=0;GetExitCodeProcess(process.hProcess,&code);
        if(wait!=WAIT_TIMEOUT) throw std::runtime_error("target_exited_after_guard_handoff:"+std::to_string(code));
        std::cout<<"PASS: concurrent syscall survives debugger handoff\n";
        result=0;
    } catch(const std::exception& error) { std::cerr<<error.what()<<'\n'; }
    TerminateProcess(process.hProcess,82);WaitForSingleObject(process.hProcess,3000);
    if(reader) CloseHandle(reader);if(stopper) CloseHandle(stopper);
    CloseHandle(process.hThread);CloseHandle(process.hProcess);
    return result;
}
