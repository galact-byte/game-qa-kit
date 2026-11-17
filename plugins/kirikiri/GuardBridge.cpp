#include "Guard.hpp"
static DWORD WINAPI watch(void*) {
    while(true) {
        Sleep(25);
        if(!kirikiri_guard::intact()) {kirikiri_guard::deny("hook_integrity");return 0;}
    }
}
extern "C" __declspec(dllexport) DWORD WINAPI StartKiriKiri(void*) {
    try {
        wchar_t root[32768]{};
        if(!GetEnvironmentVariableW(L"GAMEQAKIT_KIRIKIRI_ROOT",root,32768)) return 0;
        auto thread=CreateThread(nullptr,0,watch,nullptr,CREATE_SUSPENDED,nullptr);
        if(!thread) return 0;
        try { kirikiri_guard::install(root); }
        catch(...) {TerminateThread(thread,1);CloseHandle(thread);throw;}
        if(ResumeThread(thread)!=1){CloseHandle(thread);return 0;}
        CloseHandle(thread);
        return reinterpret_cast<DWORD>(&kirikiri_guard::state);
    } catch(...) { return 0; }
}
BOOL WINAPI DllMain(HINSTANCE,DWORD,LPVOID) {return TRUE;}
