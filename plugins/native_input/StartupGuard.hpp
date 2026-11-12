#pragma once
#include "WriteGuard.hpp"
#include "JsonWire.hpp"
#include <map>
namespace native_platform {
// No filesystem or registry mutations are allowed before the in-process guard is ready.
class StartupGuard {
    struct Point { std::string name; unsigned char original[5]; unsigned arguments; };
    HANDLE process_;
    std::map<DWORD,Point> points_;
    std::map<DWORD,HANDLE> threads_;
    std::vector<std::string> events_;
    unsigned denied_=0;
    static void require(bool ok,const char* error) { if(!ok) throw std::runtime_error(error); }
    template<class T> T read(DWORD address) {
        T value{};SIZE_T count=0;
        require(ReadProcessMemory(process_,reinterpret_cast<void*>(address),&value,sizeof(value),&count) && count==sizeof(value),"startup_read_failed");
        return value;
    }
    void byte(DWORD address,unsigned char value) {
        DWORD old=0,ignored=0;SIZE_T count=0;
        auto pointer=reinterpret_cast<void*>(address);
        require(VirtualProtectEx(process_,pointer,1,PAGE_EXECUTE_READWRITE,&old)!=FALSE,"startup_protect_failed");
        require(WriteProcessMemory(process_,pointer,&value,1,&count) && count==1,"startup_patch_failed");
        require(VirtualProtectEx(process_,pointer,1,old,&ignored) && FlushInstructionCache(process_,pointer,1),"startup_publish_failed");
    }
    void install(DWORD base) {
        const std::pair<const char*,unsigned> apis[]={{"NtCreateFile",11},{"NtOpenFile",6},{"NtWriteFile",9},
            {"NtSetInformationFile",5},{"NtDeleteFile",1},{"NtCreateKey",7},{"NtSetValueKey",6},{"NtDeleteKey",1},{"NtDeleteValueKey",2}};
        const auto local=GetModuleHandleW(L"ntdll.dll");
        for(const auto& api:apis) {
            const auto entry=GetProcAddress(local,api.first);
            require(entry!=nullptr,"startup_api_missing");
            const auto address=base+reinterpret_cast<DWORD>(entry)-reinterpret_cast<DWORD>(local);
            Point point{};point.name=api.first;point.arguments=api.second;SIZE_T count=0;
            require(ReadProcessMemory(process_,reinterpret_cast<void*>(address),point.original,5,&count) && count==5 && point.original[0]==0xb8,"startup_syscall_mismatch");
            points_.emplace(address,point);byte(address,0xcc);
        }
    }
    std::wstring object_name(DWORD object_address) {
        const auto object=read<OBJECT_ATTRIBUTES>(object_address);
        if(!object.ObjectName) return L"";
        const auto name=read<UNICODE_STRING>(reinterpret_cast<DWORD>(object.ObjectName));
        if(!name.Buffer || name.Length>32766) return L"";
        std::wstring text(name.Length/2,L'\0');SIZE_T count=0;
        require(ReadProcessMemory(process_,name.Buffer,text.data(),name.Length,&count) && count==name.Length,"startup_object_read_failed");
        if(!object.RootDirectory) return text;
        HANDLE handle=nullptr;
        require(DuplicateHandle(process_,object.RootDirectory,GetCurrentProcess(),&handle,0,FALSE,DUPLICATE_SAME_ACCESS)!=FALSE,"startup_root_duplicate_failed");
        const auto query=reinterpret_cast<NTSTATUS (NTAPI*)(HANDLE,OBJECT_INFORMATION_CLASS,PVOID,ULONG,PULONG)>(GetProcAddress(GetModuleHandleW(L"ntdll.dll"),"NtQueryObject"));
        std::vector<unsigned char> buffer(4096);ULONG needed=0;
        const auto status=query(handle,static_cast<OBJECT_INFORMATION_CLASS>(1),buffer.data(),static_cast<ULONG>(buffer.size()),&needed);
        CloseHandle(handle);require(status>=0,"startup_root_query_failed");
        const auto root=reinterpret_cast<const UNICODE_STRING*>(buffer.data());
        return root->Buffer?std::wstring(root->Buffer,root->Length/2)+text:text;
    }
    DWORD exception(const DEBUG_EVENT& event) {
        const auto& info=event.u.Exception.ExceptionRecord;
        const auto found=points_.find(reinterpret_cast<DWORD>(info.ExceptionAddress));
        if(info.ExceptionCode==EXCEPTION_BREAKPOINT && found!=points_.end()) {
            CONTEXT context{};context.ContextFlags=CONTEXT_FULL;
            const auto thread=threads_.at(event.dwThreadId);
            require(GetThreadContext(thread,&context)!=FALSE,"startup_context_read_failed");
            const auto& point=found->second;bool deny=true;std::wstring path;
            if(point.name=="NtCreateFile" || point.name=="NtOpenFile") {
                path=object_name(read<DWORD>(context.Esp+12));
                const auto access=read<DWORD>(context.Esp+8);
                const auto disposition=point.name=="NtCreateFile"?read<DWORD>(context.Esp+32):1;
                deny=(access&0x520d0116u)!=0 || disposition!=1;
                // These are OS console/cryptography devices, not filesystem destinations.
                if(deny && ((point.name=="NtCreateFile" && path==L"\\Device\\ConDrv\\Connect") ||
                            (point.name=="NtOpenFile" && path==L"\\Device\\KsecDD"))) deny=false;
            }
            require(events_.size()<4096,"startup_audit_limit");
            events_.push_back("{\"api\":"+native_input::quote(point.name)+",\"path\":"+native_input::quote(utf8(path))+",\"denied\":"+(deny?"true":"false")+"}");
            if(deny) {
                ++denied_;context.Eax=0xc0000022u;context.Eip=read<DWORD>(context.Esp);
                context.Esp+=4+point.arguments*4;
            } else {
                std::memcpy(&context.Eax,point.original+1,4);context.Eip=found->first+5;
            }
            require(SetThreadContext(thread,&context)!=FALSE,"startup_context_write_failed");return DBG_CONTINUE;
        }
        if(info.ExceptionCode==EXCEPTION_BREAKPOINT || info.ExceptionCode==0x4000001fu) return DBG_CONTINUE;
        return DBG_EXCEPTION_NOT_HANDLED;
    }
public:
    explicit StartupGuard(HANDLE process):process_(process) {}
    StartupGuard(const StartupGuard&)=delete;
    StartupGuard& operator=(const StartupGuard&)=delete;
    ~StartupGuard() { for(const auto& item:threads_) CloseHandle(item.second); }
    void run(DWORD stop_thread,bool process_exit=false) {
        const auto deadline=GetTickCount64()+8000;bool done=false,exited=false;
        while(!done && GetTickCount64()<deadline) {
            DEBUG_EVENT event{};if(!WaitForDebugEvent(&event,100)) continue;
            DWORD proceed=DBG_CONTINUE;
            switch(event.dwDebugEventCode) {
            case CREATE_PROCESS_DEBUG_EVENT:
                threads_[event.dwThreadId]=event.u.CreateProcessInfo.hThread;
                if(event.u.CreateProcessInfo.hProcess!=process_) CloseHandle(event.u.CreateProcessInfo.hProcess);
                if(event.u.CreateProcessInfo.hFile) CloseHandle(event.u.CreateProcessInfo.hFile);
                break;
            case CREATE_THREAD_DEBUG_EVENT:threads_[event.dwThreadId]=event.u.CreateThread.hThread;break;
            case LOAD_DLL_DEBUG_EVENT: {
                const auto file=event.u.LoadDll.hFile;wchar_t path[32768]{};
                if(file && GetFinalPathNameByHandleW(file,path,32768,0) &&
                   !_wcsicmp(fs::path(path).filename().c_str(),L"ntdll.dll") && points_.empty())
                    install(reinterpret_cast<DWORD>(event.u.LoadDll.lpBaseOfDll));
                if(file) CloseHandle(file);break;
            }
            case EXCEPTION_DEBUG_EVENT:proceed=exception(event);break;
            case EXIT_THREAD_DEBUG_EVENT:done=!process_exit && event.dwThreadId==stop_thread;break;
            case EXIT_PROCESS_DEBUG_EVENT:done=true;exited=true;break;
            default:break;
            }
            require(ContinueDebugEvent(event.dwProcessId,event.dwThreadId,proceed)!=FALSE,"startup_continue_failed");
        }
        require(done && !points_.empty() && (!exited || process_exit),"startup_guard_incomplete");
        if(!exited) {
            for(const auto& point:points_) byte(point.first,point.second.original[0]);
            require(DebugActiveProcessStop(GetProcessId(process_))!=FALSE,"startup_detach_failed");
        }
    }
    void save(const fs::path& bridge) const {
        std::ofstream out(bridge/L"startup-guard.json");out<<"{\"denied\":"<<denied_<<",\"calls\":[";
        for(size_t i=0;i<events_.size();++i) {if(i) out<<',';out<<events_[i];}out<<"]}";
        require(bool(out),"startup_audit_save_failed");
    }
};
}
