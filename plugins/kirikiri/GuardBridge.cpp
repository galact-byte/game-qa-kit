#include "Guard.hpp"
static DWORD WINAPI watch(void*) {
    while(true) {
        Sleep(25);
        if(!kirikiri_guard::intact()) {kirikiri_guard::deny("hook_integrity");return 0;}
    }
}
extern "C" __declspec(dllexport) DWORD WINAPI StartKiriKiri(void* section) {
    try {
        if(!section)return 0;
        auto shared=static_cast<kirikiri_guard::SharedState*>(MapViewOfFile(section,FILE_MAP_READ|FILE_MAP_WRITE,0,0,sizeof(kirikiri_guard::SharedState)));
        CloseHandle(section);
        if(!shared)return 0;
        if(!kirikiri_guard::state_identity(*shared,GetCurrentProcess()) ||
           shared->value.magic!=0x4b514731 || shared->value.pid!=GetCurrentProcessId()){
            UnmapViewOfFile(shared);return 0;
        }
        kirikiri_guard::active_state=&shared->value;
        wchar_t root[32768]{};
        if(!GetEnvironmentVariableW(L"GAMEQAKIT_KIRIKIRI_ROOT",root,32768)) return 0;
        auto thread=CreateThread(nullptr,0,watch,nullptr,CREATE_SUSPENDED,nullptr);
        if(!thread) return 0;
        try { kirikiri_guard::install(root); }
        catch(...) {TerminateThread(thread,1);CloseHandle(thread);throw;}
        if(ResumeThread(thread)!=1){CloseHandle(thread);return 0;}
        CloseHandle(thread);
        return kirikiri_guard::state_protocol;
    } catch(...) { return 0; }
}
BOOL WINAPI DllMain(HINSTANCE,DWORD,LPVOID) {return TRUE;}
