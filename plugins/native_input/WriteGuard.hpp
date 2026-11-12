#pragma once
#include "Native.hpp"
#include <winternl.h>
#include <atomic>
#include <mutex>
namespace native_guard {
inline native_platform::fs::path root;
inline std::atomic<unsigned> denied{0},registry_denied{0};
inline std::mutex audit_mutex;
inline std::vector<std::wstring> denied_paths;
inline bool allowed(std::wstring path) {
    if(path.compare(0,4,L"\\??\\")==0 || path.compare(0,4,L"\\\\?\\")==0) path.erase(0,4);
    if(path.size()<3 || path[1]!=L':' || (path[2]!=L'\\' && path[2]!=L'/')) return false;
    path=native_platform::fs::path(path).lexically_normal().wstring();
    auto prefix=root.lexically_normal().wstring();
    while(!prefix.empty() && (prefix.back()==L'\\' || prefix.back()==L'/')) prefix.pop_back();
    return !_wcsicmp(path.c_str(),prefix.c_str()) ||
        (path.size()>prefix.size() && path[prefix.size()]==L'\\' && !_wcsnicmp(path.c_str(),prefix.c_str(),prefix.size()));
}
inline bool permit(POBJECT_ATTRIBUTES object) {
    std::wstring path;
    if(object && object->ObjectName && object->ObjectName->Buffer) {
        path.assign(object->ObjectName->Buffer,object->ObjectName->Length/sizeof(wchar_t));
        if(!path.empty() && path.front()!=L'\\' && object->RootDirectory) {
            // Graphics-driver workers can have a very small stack.
            std::vector<wchar_t> parent(32768);
            const auto n=GetFinalPathNameByHandleW(object->RootDirectory,parent.data(),static_cast<DWORD>(parent.size()),FILE_NAME_NORMALIZED);
            if(n && n<parent.size()) path=std::wstring(parent.data(),n)+L"\\"+path;
        }
    }
    if(allowed(path)) return true;
    ++denied;std::lock_guard<std::mutex> lock(audit_mutex);
    if(denied_paths.size()<256) denied_paths.push_back(path);
    return false;
}
using CreateFile=NTSTATUS (NTAPI*)(PHANDLE,ACCESS_MASK,POBJECT_ATTRIBUTES,PIO_STATUS_BLOCK,PLARGE_INTEGER,ULONG,ULONG,ULONG,ULONG,PVOID,ULONG);
using OpenFile=NTSTATUS (NTAPI*)(PHANDLE,ACCESS_MASK,POBJECT_ATTRIBUTES,PIO_STATUS_BLOCK,ULONG,ULONG);
inline CreateFile create_file=nullptr;
inline OpenFile open_file=nullptr;
inline NTSTATUS NTAPI guarded_create(PHANDLE handle,ACCESS_MASK access,POBJECT_ATTRIBUTES object,PIO_STATUS_BLOCK io,
                                    PLARGE_INTEGER allocation,ULONG attributes,ULONG share,ULONG disposition,ULONG options,PVOID ea,ULONG ea_size) {
    if(((access&0x520d0116u)!=0 || disposition!=1) && !permit(object)) return static_cast<NTSTATUS>(0xc0000022u);
    return create_file(handle,access,object,io,allocation,attributes,share,disposition,options,ea,ea_size);
}
inline NTSTATUS NTAPI guarded_open(PHANDLE handle,ACCESS_MASK access,POBJECT_ATTRIBUTES object,PIO_STATUS_BLOCK io,ULONG share,ULONG options) {
    const auto name=object?object->ObjectName:nullptr;
    if(object&&!object->RootDirectory&&name&&name->Buffer&&
       std::wstring(name->Buffer,name->Length/sizeof(wchar_t))==L"\\Device\\KsecDD")
        return open_file(handle,access,object,io,share,options);
    if((access&0x520d0116u)!=0 && !permit(object)) return static_cast<NTSTATUS>(0xc0000022u);
    return open_file(handle,access,object,io,share,options);
}
inline NTSTATUS denied_registry() { ++registry_denied;return static_cast<NTSTATUS>(0xc0000022u); }
inline NTSTATUS NTAPI create_key(PHANDLE,ACCESS_MASK,POBJECT_ATTRIBUTES,ULONG,PUNICODE_STRING,ULONG,PULONG) { return denied_registry(); }
inline NTSTATUS NTAPI set_value(HANDLE,PUNICODE_STRING,ULONG,ULONG,PVOID,ULONG) { return denied_registry(); }
inline NTSTATUS NTAPI delete_key(HANDLE) { return denied_registry(); }
inline NTSTATUS NTAPI delete_value(HANDLE,PUNICODE_STRING) { return denied_registry(); }
inline unsigned char* syscall_entry(const char* name) {
    auto entry=reinterpret_cast<unsigned char*>(GetProcAddress(GetModuleHandleW(L"ntdll.dll"),name));
    // The only relocated instruction is the five-byte x86 MOV EAX, imm32.
    if(!entry || entry[0]!=0xb8) throw std::runtime_error("unsupported_ntdll_syscall_entry");
    return entry;
}
inline void* hook(const char* name,void* replacement) {
    auto entry=syscall_entry(name);
    auto trampoline=static_cast<unsigned char*>(VirtualAlloc(nullptr,10,MEM_COMMIT|MEM_RESERVE,PAGE_READWRITE));
    if(!trampoline) throw std::runtime_error("guard_trampoline_allocation_failed");
    std::memcpy(trampoline,entry,5);trampoline[5]=0xe9;
    auto offset=static_cast<int32_t>(entry+5-(trampoline+10));std::memcpy(trampoline+6,&offset,4);
    DWORD old=0;
    if(!VirtualProtect(trampoline,10,PAGE_EXECUTE_READ,&old) || !FlushInstructionCache(GetCurrentProcess(),trampoline,10))
        throw std::runtime_error("guard_trampoline_publish_failed");
    unsigned char patch[5]={0xe9};offset=static_cast<int32_t>(static_cast<unsigned char*>(replacement)-(entry+5));std::memcpy(patch+1,&offset,4);
    native_platform::replace(entry,patch,5);return trampoline;
}
inline void install(const native_platform::fs::path& workspace) {
    root=native_platform::fs::canonical(workspace);
    for(const auto name:{"NtCreateFile","NtOpenFile","NtCreateKey","NtSetValueKey","NtDeleteKey","NtDeleteValueKey"}) syscall_entry(name);
    create_file=reinterpret_cast<CreateFile>(hook("NtCreateFile",reinterpret_cast<void*>(&guarded_create)));
    open_file=reinterpret_cast<OpenFile>(hook("NtOpenFile",reinterpret_cast<void*>(&guarded_open)));
    hook("NtCreateKey",reinterpret_cast<void*>(&create_key));hook("NtSetValueKey",reinterpret_cast<void*>(&set_value));
    hook("NtDeleteKey",reinterpret_cast<void*>(&delete_key));hook("NtDeleteValueKey",reinterpret_cast<void*>(&delete_value));
}
}
