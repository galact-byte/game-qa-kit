#include <windows.h>
#include <string>
BOOL WINAPI DllMain(HINSTANCE,DWORD reason,LPVOID) {
    if(reason==DLL_PROCESS_ATTACH) {
        wchar_t buffer[32768]{};
        GetEnvironmentVariableW(L"GAMEQAKIT_KIRIKIRI_ROOT",buffer,32768);
        const std::wstring root(buffer);
        const auto outside=root+L"\\..\\outside\\sentinel.bin";
        auto file=CreateFileW(outside.c_str(),GENERIC_WRITE,FILE_SHARE_READ,nullptr,CREATE_ALWAYS,0,nullptr);
        if(file!=INVALID_HANDLE_VALUE) {CloseHandle(file);return TRUE;}
        if(GetLastError()!=ERROR_ACCESS_DENIED)return TRUE;
        file=CreateFileW((root+L"\\savedata\\denied-returned.bin").c_str(),GENERIC_WRITE,FILE_SHARE_READ,nullptr,CREATE_ALWAYS,0,nullptr);
        if(file!=INVALID_HANDLE_VALUE)CloseHandle(file);
    }
    return TRUE;
}
