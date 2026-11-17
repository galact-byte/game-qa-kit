#include <windows.h>
#include <winternl.h>
#include <filesystem>
#include <string>
namespace fs=std::filesystem;
static fs::path folder(){wchar_t p[32768]{};GetEnvironmentVariableW(L"GAMEQAKIT_KIRIKIRI_ROOT",p,32768);return p;}
static bool write(const fs::path& p){auto h=CreateFileW(p.c_str(),GENERIC_WRITE,FILE_SHARE_READ,nullptr,CREATE_ALWAYS,FILE_ATTRIBUTE_NORMAL,nullptr);if(h==INVALID_HANDLE_VALUE)return false;DWORD n=0;WriteFile(h,"changed",7,&n,nullptr);CloseHandle(h);return n==7;}
static bool denied_before_main=false;
struct Early {Early(){
    if(fs::exists(folder()/L"attempt-before-main")) {
        denied_before_main=!write(folder().parent_path()/L"outside/sentinel.bin");
        if(denied_before_main) write(folder()/L"savedata/denied-returned.bin");
    }
    else denied_before_main=write(folder()/L"savedata/early.bin");
}} early;
int wmain(){
    const auto root=folder();if(!denied_before_main)return 1;
    if(fs::exists(root/L"attempt-cng")) {
        const auto file=CreateFileW(L"\\\\?\\GLOBALROOT\\Device\\CNG",GENERIC_READ,FILE_SHARE_READ|FILE_SHARE_WRITE,nullptr,OPEN_EXISTING,0,nullptr);
        if(file==INVALID_HANDLE_VALUE)return 9;
        const auto control=reinterpret_cast<NTSTATUS (NTAPI*)(HANDLE,HANDLE,PVOID,PVOID,PIO_STATUS_BLOCK,ULONG,PVOID,ULONG,PVOID,ULONG)>(GetProcAddress(GetModuleHandleW(L"ntdll.dll"),"NtDeviceIoControlFile"));
        IO_STATUS_BLOCK io{};unsigned char buffer[32]{};
        const auto status=control(file,nullptr,nullptr,nullptr,&io,0x390008,nullptr,0,buffer,sizeof(buffer));
        CloseHandle(file);
        if(status!=static_cast<NTSTATUS>(0xc0000022u))return 10;
        if(!write(root/L"savedata/cng-denied-returned.bin"))return 11;
        Sleep(200);return 0;
    }
    const bool create_request=fs::exists(root/L"attempt-shell-create");
    if(create_request || fs::exists(root/L"attempt-shell-section")) {
        DWORD session=0;if(!ProcessIdToSessionId(GetCurrentProcessId(),&session))return 6;
        auto path=L"\\Sessions\\"+std::to_wstring(session)+L"\\BaseNamedObjects\\windows_shell_global_counters";
        UNICODE_STRING name{};name.Buffer=path.data();name.Length=static_cast<USHORT>(path.size()*2);name.MaximumLength=name.Length;
        OBJECT_ATTRIBUTES object{};object.Length=sizeof(object);object.ObjectName=&name;
        HANDLE handle=nullptr;
        NTSTATUS status=0;
        if(create_request) {
            auto create=reinterpret_cast<NTSTATUS (NTAPI*)(PHANDLE,ACCESS_MASK,POBJECT_ATTRIBUTES,PLARGE_INTEGER,ULONG,ULONG,HANDLE)>(GetProcAddress(GetModuleHandleW(L"ntdll.dll"),"NtCreateSection"));
            LARGE_INTEGER size{};size.QuadPart=4096;
            status=create(&handle,0xf0007,&object,&size,PAGE_READWRITE,SEC_COMMIT,nullptr);
        } else {
            auto open=reinterpret_cast<NTSTATUS (NTAPI*)(PHANDLE,ACCESS_MASK,POBJECT_ATTRIBUTES)>(GetProcAddress(GetModuleHandleW(L"ntdll.dll"),"NtOpenSection"));
            status=open(&handle,6,&object);
        }
        if(status!=static_cast<NTSTATUS>(0xc0000022u)||handle)return 7;
        if(!write(root/L"savedata/shell-denied-returned.bin"))return 8;
        Sleep(200);return 0;
    }
    if(fs::exists(root/L"attempt-dll-init")) LoadLibraryW((root/L"game/AuthoredInit.dll").c_str());
    if(fs::exists(root/L"exit-target")) return 0;
    if(fs::exists(root/L"damage-hook")) {
        auto entry=reinterpret_cast<unsigned char*>(GetProcAddress(GetModuleHandleW(L"ntdll.dll"),"NtWriteFile"));
        DWORD old=0;
        if(!VirtualProtect(entry,5,PAGE_EXECUTE_READWRITE,&old))return 4;
        entry[0]=0xcc;FlushInstructionCache(GetCurrentProcess(),entry,5);
        VirtualProtect(entry,5,old,&old);Sleep(10000);return 5;
    }
    // A later phase intentionally attempts forbidden writes; the launcher must stop the Job.
    if(fs::exists(root/L"attempt-outside")){
        if(!write(root.parent_path()/L"outside/sentinel.bin")) write(root/L"savedata/denied-returned.bin");
        Sleep(5000);return 2;
    }
    if(!write(root/L"savedata/static-init-confirmed.bin"))return 3;
    Sleep(10000);return 0;
}
