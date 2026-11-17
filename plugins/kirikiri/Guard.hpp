#pragma once
#include "../native_input/WriteGuard.hpp"
#include <array>

// Fixed x86 target policy. Unknown mutation classes fail closed.
namespace kirikiri_guard {
namespace fs = std::filesystem;
constexpr NTSTATUS denied_status = static_cast<NTSTATUS>(0xc0000022u);
constexpr ACCESS_MASK writes = 0x520d0116u;
struct State {
    DWORD magic=0x4b514731, pid=GetCurrentProcessId(); volatile LONG denied=0; DWORD ready=0;
    // 0: strict; 1: authored fixture experiment; 2: explicit commercial compatibility.
    DWORD shell_experiment=0; volatile LONG shell_denied=0, shell_create_denied=0, cng_denied=0; wchar_t shell_counter[128]{};
    volatile LONG reporting=0;char api[48]{};wchar_t path[512]{};DWORD control=0;
};
inline State state;
inline fs::path root;
inline std::wstring volume,drive;
inline std::vector<std::pair<unsigned char*,std::array<unsigned char,5>>> hooks;
inline NTSTATUS deny(const char* api="mutation",const std::wstring& path=L"",DWORD control=0) {
    if(InterlockedCompareExchange(&state.reporting,1,0)==0) {
        strncpy_s(state.api,api,_TRUNCATE);wcsncpy_s(state.path,path.c_str(),_TRUNCATE);
        state.control=control;
        MemoryBarrier();InterlockedExchange(&state.reporting,2);
    }
    InterlockedIncrement(&state.denied);
    return denied_status;
}
struct Pins {
    std::vector<HANDLE> items;
    ~Pins() { for(auto handle:items) CloseHandle(handle); }
};
inline std::wstring final_path(HANDLE handle) {
    std::vector<wchar_t> name(32768);
    const auto count=GetFinalPathNameByHandleW(handle,name.data(),static_cast<DWORD>(name.size()),FILE_NAME_NORMALIZED|VOLUME_NAME_NT);
    if(!count || count>=name.size()) return L"";
    std::wstring value(name.data(),count);
    if(value.size()>volume.size() && !volume.empty() && value[volume.size()]==L'\\' &&
       !_wcsnicmp(value.c_str(),volume.c_str(),volume.size())) value=drive+value.substr(volume.size());
    return value;
}
inline bool within(const fs::path& path,const fs::path& parent) {
    const auto value=path.wstring(),base=parent.wstring();
    return !_wcsicmp(value.c_str(),base.c_str()) ||
        (value.size()>base.size() && value[base.size()]==L'\\' && !_wcsnicmp(value.c_str(),base.c_str(),base.size()));
}
inline bool permitted(const fs::path& path) {
    for(const auto name:{L"savedata",L"appdata",L"localappdata",L"tmp"})
        if(within(path,root/name)) return true;
    for(const auto name:{L"target-observer.tjs",L"detached.tjs",L"target-error.tjs"})
        if(!_wcsicmp(path.c_str(),(root/L"bridge"/name).c_str())) return true;
    return false;
}
inline fs::path clean(std::wstring value) {
    if(value.compare(0,4,L"\\??\\")==0 || value.compare(0,4,L"\\\\?\\")==0) value.erase(0,4);
    if(value.size()<3 || value[1]!=L':' || value[2]!=L'\\' || value.find(L':',2)!=std::wstring::npos)
        return {};
    const fs::path path(value);
    for(const auto& part:path.relative_path()) {
        const auto text=part.wstring();
        if(text.empty() || text==L".." || text.back()==L'.' || text.back()==L' ') return {};
    }
    return path.lexically_normal();
}
inline fs::path object_path(POBJECT_ATTRIBUTES object) {
    if(!object || !object->ObjectName || !object->ObjectName->Buffer || (object->ObjectName->Length&1)) return {};
    std::wstring name(object->ObjectName->Buffer,object->ObjectName->Length/2);
    if(name.find(L'\0')!=std::wstring::npos) return {};
    if(object->RootDirectory) {
        const auto base=final_path(object->RootDirectory);
        if(base.empty() || name.empty() || name.front()==L'\\' || name.find(L':')!=std::wstring::npos) return {};
        name=base+L"\\"+name;
    }
    return clean(name);
}
inline bool pin_path(fs::path path,Pins& pins) {
    path=path.lexically_normal();
    if(path.empty() || !permitted(path)) return false;
    auto current=path;
    bool leaf=true;
    while(!current.empty()) {
        const auto attrs=GetFileAttributesW(current.c_str());
        if(attrs!=INVALID_FILE_ATTRIBUTES) {
            if(attrs&FILE_ATTRIBUTE_REPARSE_POINT) return false;
            const auto handle=CreateFileW(current.c_str(),FILE_READ_ATTRIBUTES,FILE_SHARE_READ|FILE_SHARE_WRITE,
                nullptr,OPEN_EXISTING,FILE_FLAG_BACKUP_SEMANTICS|FILE_FLAG_OPEN_REPARSE_POINT,nullptr);
            if(handle==INVALID_HANDLE_VALUE) return false;
            pins.items.push_back(handle);
            BY_HANDLE_FILE_INFORMATION info{};
            if(!GetFileInformationByHandle(handle,&info) || (info.dwFileAttributes&FILE_ATTRIBUTE_REPARSE_POINT) ||
               (!(info.dwFileAttributes&FILE_ATTRIBUTE_DIRECTORY) && info.nNumberOfLinks!=1) ||
               _wcsicmp(final_path(handle).c_str(),current.c_str())) return false;
        } else if(!leaf || GetLastError()!=ERROR_FILE_NOT_FOUND) return false;
        const auto parent=current.parent_path();
        if(parent==current) break;
        current=parent;leaf=false;
    }
    return true;
}
inline bool handle_allowed(HANDLE handle) {
    const auto path=clean(final_path(handle));
    if(path.empty() || !permitted(path)) return false;
    BY_HANDLE_FILE_INFORMATION info{};
    return GetFileInformationByHandle(handle,&info) && !(info.dwFileAttributes&FILE_ATTRIBUTE_REPARSE_POINT) &&
        ((info.dwFileAttributes&FILE_ATTRIBUTE_DIRECTORY) || info.nNumberOfLinks==1);
}
using CreateFile = native_guard::CreateFile;
using OpenFile = native_guard::OpenFile;
inline CreateFile create_file=nullptr;
inline OpenFile open_file=nullptr;
inline NTSTATUS NTAPI create(PHANDLE handle,ACCESS_MASK access,POBJECT_ATTRIBUTES object,PIO_STATUS_BLOCK io,
    PLARGE_INTEGER allocation,ULONG attrs,ULONG share,ULONG disposition,ULONG options,PVOID ea,ULONG ea_size) {
    if((access&writes) || disposition!=1 || (options&0x1000)) {
        Pins pins;
        if(!pin_path(object_path(object),pins) || (options&0x00200000) || ea_size) return deny("NtCreateFile",object_path(object).wstring());
        return create_file(handle,access,object,io,allocation,attrs,share,disposition,options,ea,ea_size);
    }
    return create_file(handle,access,object,io,allocation,attrs,share,disposition,options,ea,ea_size);
}
inline NTSTATUS NTAPI open(PHANDLE handle,ACCESS_MASK access,POBJECT_ATTRIBUTES object,PIO_STATUS_BLOCK io,ULONG share,ULONG options) {
    if((access&writes) || (options&0x1000)) {
        // Cryptography uses this exact OS device; it is not a filesystem destination.
        if(object && !object->RootDirectory && object->ObjectName && object->ObjectName->Buffer &&
           std::wstring(object->ObjectName->Buffer,object->ObjectName->Length/2)==L"\\Device\\KsecDD")
            return open_file(handle,access,object,io,share,options);
        Pins pins;
        if(!pin_path(object_path(object),pins) || (options&0x00200000)) return deny("NtOpenFile",object_path(object).wstring());
        return open_file(handle,access,object,io,share,options);
    }
    return open_file(handle,access,object,io,share,options);
}
using Write = NTSTATUS (NTAPI*)(HANDLE,HANDLE,PVOID,PVOID,PIO_STATUS_BLOCK,PVOID,ULONG,PLARGE_INTEGER,PULONG);
inline Write write_file=nullptr,write_gather=nullptr;
inline NTSTATUS NTAPI write(HANDLE file,HANDLE event,PVOID apc,PVOID context,PIO_STATUS_BLOCK io,PVOID buffer,ULONG length,PLARGE_INTEGER offset,PULONG key) {
    if(!handle_allowed(file)) return deny("NtWriteFile",final_path(file));
    return write_file(file,event,apc,context,io,buffer,length,offset,key);
}
inline NTSTATUS NTAPI gather(HANDLE file,HANDLE event,PVOID apc,PVOID context,PIO_STATUS_BLOCK io,PVOID buffer,ULONG length,PLARGE_INTEGER offset,PULONG key) {
    if(!handle_allowed(file)) return deny("NtWriteFileGather",final_path(file));
    return write_gather(file,event,apc,context,io,buffer,length,offset,key);
}
using SetInfo = NTSTATUS (NTAPI*)(HANDLE,PIO_STATUS_BLOCK,PVOID,ULONG,FILE_INFORMATION_CLASS);
inline SetInfo set_information=nullptr;
inline NTSTATUS NTAPI set_info(HANDLE file,PIO_STATUS_BLOCK io,PVOID data,ULONG length,FILE_INFORMATION_CLASS kind) {
    // File position/mode/completion binding change only this handle. Rename/link and
    // unknown classes are denied even inside the run, so no new aliases can be made.
    const auto value=static_cast<unsigned>(kind);
    if(value==14 || value==16 || value==30) return set_information(file,io,data,length,kind);
    if((value!=4 && value!=13 && value!=19 && value!=20 && value!=64) || !handle_allowed(file)) return deny("NtSetInformationFile",final_path(file));
    return set_information(file,io,data,length,kind);
}
using Delete = NTSTATUS (NTAPI*)(POBJECT_ATTRIBUTES);
inline Delete delete_file=nullptr;
inline NTSTATUS NTAPI remove(POBJECT_ATTRIBUTES object) {
    Pins pins;
    if(!pin_path(object_path(object),pins)) return deny();
    // Pins deliberately exclude delete sharing; use the handle-based disposition path.
    return deny();
}
using Section = NTSTATUS (NTAPI*)(PHANDLE,ACCESS_MASK,POBJECT_ATTRIBUTES,PLARGE_INTEGER,ULONG,ULONG,HANDLE);
inline std::wstring device_name(HANDLE file);
inline Section create_section=nullptr;
inline NTSTATUS NTAPI section(PHANDLE result,ACCESS_MASK access,POBJECT_ATTRIBUTES object,PLARGE_INTEGER size,ULONG protect,ULONG attrs,HANDLE file) {
    if(file && (protect&(PAGE_READWRITE|PAGE_WRITECOPY|PAGE_EXECUTE_READWRITE|PAGE_EXECUTE_WRITECOPY)) && !handle_allowed(file)) return deny("NtCreateSection",final_path(file));
    if(object && object->ObjectName && object->ObjectName->Length) {
        const auto name=object->ObjectName->Buffer?std::wstring(object->ObjectName->Buffer,object->ObjectName->Length/2):L"";
        const auto base=object->RootDirectory?device_name(object->RootDirectory):L"";
        if(object->ObjectName->Length&1) return deny("NtCreateSection(invalid)");
        const auto path=base.empty()?name:base+L"\\"+name;
        // Both explicit policies refuse this request without creating an object.
        if(state.shell_experiment && !file && access==0xf0007 && !path.empty() && path==state.shell_counter) {
            InterlockedIncrement(&state.shell_create_denied);
            return denied_status;
        }
        return deny("NtCreateSection(named)",path,access);
    }
    return create_section(result,access,object,size,protect,attrs,file);
}
using OpenSection = NTSTATUS (NTAPI*)(PHANDLE,ACCESS_MASK,POBJECT_ATTRIBUTES);
inline OpenSection open_section=nullptr;
inline NTSTATUS NTAPI section_open(PHANDLE result,ACCESS_MASK access,POBJECT_ATTRIBUTES object) {
    if(access&(SECTION_MAP_WRITE|SECTION_EXTEND_SIZE|MAXIMUM_ALLOWED|GENERIC_ALL|GENERIC_WRITE)) {
        const auto name=object&&object->ObjectName&&object->ObjectName->Buffer?
            std::wstring(object->ObjectName->Buffer,object->ObjectName->Length/2):L"";
        const auto base=object&&object->RootDirectory?device_name(object->RootDirectory):L"";
        if(object && object->ObjectName && (object->ObjectName->Length&1)) return deny("NtOpenSection(invalid)");
        const auto path=base.empty()?name:base+L"\\"+name;
        // The exact observed request stays denied; only batch termination is waived.
        if(state.shell_experiment && access==6 && !path.empty() && path==state.shell_counter) {
            InterlockedIncrement(&state.shell_denied);
            return denied_status;
        }
        return deny("NtOpenSection(write)",path,access);
    }
    return open_section(result,access,object);
}
using Duplicate = NTSTATUS (NTAPI*)(HANDLE,HANDLE,HANDLE,PHANDLE,ACCESS_MASK,ULONG,ULONG);
inline Duplicate duplicate=nullptr;
inline NTSTATUS NTAPI duplicate_object(HANDLE source,HANDLE object,HANDLE target,PHANDLE result,ACCESS_MASK access,ULONG attrs,ULONG options) {
    if(GetProcessId(source)!=GetCurrentProcessId() || GetProcessId(target)!=GetCurrentProcessId()) return deny("NtDuplicateObject(external)");
    return duplicate(source,object,target,result,access,attrs,options);
}
using Flush = NTSTATUS (NTAPI*)(HANDLE,PIO_STATUS_BLOCK);
inline Flush flush_file=nullptr;
inline NTSTATUS NTAPI flush(HANDLE file,PIO_STATUS_BLOCK io) {
    if(!handle_allowed(file)) return deny("NtFlushBuffersFile",final_path(file));
    return flush_file(file,io);
}
using Control = NTSTATUS (NTAPI*)(HANDLE,HANDLE,PVOID,PVOID,PIO_STATUS_BLOCK,ULONG,PVOID,ULONG,PVOID,ULONG);
inline Control device_control=nullptr,fs_control=nullptr;
inline std::wstring device_name(HANDLE file) {
    using Query=NTSTATUS (NTAPI*)(HANDLE,OBJECT_INFORMATION_CLASS,PVOID,ULONG,PULONG);
    auto query=reinterpret_cast<Query>(GetProcAddress(GetModuleHandleW(L"ntdll.dll"),"NtQueryObject"));
    alignas(void*) unsigned char buffer[4096]{};ULONG needed=0;
    if(query(file,static_cast<OBJECT_INFORMATION_CLASS>(1),buffer,sizeof(buffer),&needed)<0)return L"";
    auto name=reinterpret_cast<UNICODE_STRING*>(buffer);
    return name->Buffer?std::wstring(name->Buffer,name->Length/2):L"";
}
inline NTSTATUS NTAPI device_control_hook(HANDLE file,HANDLE event,PVOID apc,PVOID context,PIO_STATUS_BLOCK io,ULONG code,PVOID input,ULONG input_size,PVOID output,ULONG output_size) {
    const auto name=device_name(file);
    // IOCTL_CONDRV_ISSUE_USER_IO / GET_SERVER_PID: microsoft/terminal condrv.h.
    // Limit this exception to the OS console endpoint and these two observed codes.
    if((code==0x500016 || code==0x500023) && name==L"\\Device\\ConDrv")
        return device_control(file,event,apc,context,io,code,input,input_size,output,output_size);
    if(state.shell_experiment && name==L"\\Device\\CNG" && code==0x390008) {
        InterlockedIncrement(&state.cng_denied);
        return denied_status;
    }
    return deny("NtDeviceIoControlFile",name,code);
}
inline NTSTATUS NTAPI fsctl(HANDLE file,HANDLE,HANDLE,HANDLE,PIO_STATUS_BLOCK,ULONG code,PVOID,ULONG,PVOID,ULONG) {
    return deny("NtFsControlFile",device_name(file),code);
}
// Exact stdcall arities are required for x86 failure returns.
inline NTSTATUS NTAPI deny1(ULONG) { return deny(); }
inline NTSTATUS NTAPI deny2(ULONG,ULONG) { return deny(); }
inline NTSTATUS NTAPI deny3(ULONG,ULONG,ULONG) { return deny(); }
inline NTSTATUS NTAPI deny4(ULONG,ULONG,ULONG,ULONG) { return deny(); }
inline NTSTATUS NTAPI deny5(ULONG,ULONG,ULONG,ULONG,ULONG) { return deny(); }
inline NTSTATUS NTAPI deny6(ULONG,ULONG,ULONG,ULONG,ULONG,ULONG) { return deny(); }
inline NTSTATUS NTAPI deny7(ULONG,ULONG,ULONG,ULONG,ULONG,ULONG,ULONG) { return deny(); }
inline NTSTATUS NTAPI deny8(ULONG,ULONG,ULONG,ULONG,ULONG,ULONG,ULONG,ULONG) { return deny(); }
inline NTSTATUS NTAPI deny9(ULONG,ULONG,ULONG,ULONG,ULONG,ULONG,ULONG,ULONG,ULONG) { return deny(); }
inline NTSTATUS NTAPI deny11(ULONG,ULONG,ULONG,ULONG,ULONG,ULONG,ULONG,ULONG,ULONG,ULONG,ULONG) { return deny(); }
inline void* hook(const char* name,void* replacement) {
    auto entry=native_guard::syscall_entry(name);
    auto original=native_guard::hook(name,replacement);
    std::array<unsigned char,5> bytes{};std::memcpy(bytes.data(),entry,5);hooks.emplace_back(entry,bytes);
    return original;
}
inline bool intact() {
    for(const auto& item:hooks) if(std::memcmp(item.first,item.second.data(),5)) return false;
    return true;
}
inline void install(const fs::path& folder) {
    root=fs::canonical(folder);
    drive=root.root_name().wstring();wchar_t device[32768]{};
    if(!QueryDosDeviceW(drive.c_str(),device,32768)) throw std::runtime_error("guard_volume_query_failed");
    volume=device;
    DWORD session=0;
    if(!ProcessIdToSessionId(GetCurrentProcessId(),&session)) throw std::runtime_error("guard_session_query_failed");
    const auto counter=L"\\Sessions\\"+std::to_wstring(session)+L"\\BaseNamedObjects\\windows_shell_global_counters";
    wcsncpy_s(state.shell_counter,counter.c_str(),_TRUNCATE);
#ifdef GAMEQA_KIRIKIRI_COMMERCIAL_COMPAT
    state.shell_experiment=2;
#elif defined(GAMEQA_SHELL_DENIAL_EXPERIMENT)
    state.shell_experiment=1;
#endif
    if(!hooks.empty()) throw std::runtime_error("guard_already_installed");
    create_file=reinterpret_cast<CreateFile>(hook("NtCreateFile",reinterpret_cast<void*>(&create)));
    open_file=reinterpret_cast<OpenFile>(hook("NtOpenFile",reinterpret_cast<void*>(&open)));
    write_file=reinterpret_cast<Write>(hook("NtWriteFile",reinterpret_cast<void*>(&write)));
    write_gather=reinterpret_cast<Write>(hook("NtWriteFileGather",reinterpret_cast<void*>(&gather)));
    set_information=reinterpret_cast<SetInfo>(hook("NtSetInformationFile",reinterpret_cast<void*>(&set_info)));
    delete_file=reinterpret_cast<Delete>(hook("NtDeleteFile",reinterpret_cast<void*>(&remove)));
    create_section=reinterpret_cast<Section>(hook("NtCreateSection",reinterpret_cast<void*>(&section)));
    open_section=reinterpret_cast<OpenSection>(hook("NtOpenSection",reinterpret_cast<void*>(&section_open)));
    duplicate=reinterpret_cast<Duplicate>(hook("NtDuplicateObject",reinterpret_cast<void*>(&duplicate_object)));
    flush_file=reinterpret_cast<Flush>(hook("NtFlushBuffersFile",reinterpret_cast<void*>(&flush)));
    device_control=reinterpret_cast<Control>(hook("NtDeviceIoControlFile",reinterpret_cast<void*>(&device_control_hook)));
    fs_control=reinterpret_cast<Control>(hook("NtFsControlFile",reinterpret_cast<void*>(&fsctl)));
    const std::pair<const char*,void*> denied[] = {
        {"NtCreateKey",reinterpret_cast<void*>(&deny7)}, {"NtCreateKeyTransacted",reinterpret_cast<void*>(&deny8)},
        {"NtSetValueKey",reinterpret_cast<void*>(&deny6)}, {"NtDeleteKey",reinterpret_cast<void*>(&deny1)},
        {"NtDeleteValueKey",reinterpret_cast<void*>(&deny2)}, {"NtRenameKey",reinterpret_cast<void*>(&deny2)},
        {"NtSetInformationKey",reinterpret_cast<void*>(&deny4)}, {"NtRestoreKey",reinterpret_cast<void*>(&deny3)},
        {"NtReplaceKey",reinterpret_cast<void*>(&deny3)}, {"NtLoadKey",reinterpret_cast<void*>(&deny2)},
        {"NtExtendSection",reinterpret_cast<void*>(&deny2)}, {"NtSetSecurityObject",reinterpret_cast<void*>(&deny3)},
        {"NtCreateUserProcess",reinterpret_cast<void*>(&deny11)}, {"NtCreateProcess",reinterpret_cast<void*>(&deny8)},
        {"NtCreateProcessEx",reinterpret_cast<void*>(&deny9)}, {"NtCreatePagingFile",reinterpret_cast<void*>(&deny4)}
    };
    for(const auto& item:denied) hook(item.first,item.second);
    state.ready=1;
}
}
