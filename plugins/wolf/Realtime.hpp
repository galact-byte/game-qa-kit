#pragma once
#include <windows.h>
#include <sddl.h>
#include <cstring>
#include <stdexcept>
#include <string>
#include <vector>

namespace wolf {
// Five independently committed, bounded slots. A dead writer cannot stall a reader.
class Realtime {
    struct Slot { volatile LONG sequence;DWORD length;char data[65528]; };
    static_assert(sizeof(Slot)==65536,"IPC slot layout changed");
    HANDLE mapping=nullptr;
    HANDLE locks[5]{};
    Slot* slots=nullptr;
public:
    enum Index { Request,Lease,Pause,Status,Result };
    Realtime(DWORD pid,const std::string& session) {
        if(!pid || session.empty() || session.size()>64 ||
           session.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")!=std::string::npos)
            throw std::runtime_error("invalid_ipc_binding");
        HANDLE token=nullptr;DWORD needed=0;
        if(!OpenProcessToken(GetCurrentProcess(),TOKEN_QUERY,&token)) throw std::runtime_error("ipc_token_failed");
        GetTokenInformation(token,TokenUser,nullptr,0,&needed);
        std::vector<BYTE> user(needed);
        const bool obtained=GetTokenInformation(token,TokenUser,user.data(),needed,&needed)!=FALSE;
        CloseHandle(token);LPWSTR sid=nullptr;
        if(!obtained || !ConvertSidToStringSidW(reinterpret_cast<TOKEN_USER*>(user.data())->User.Sid,&sid))
            throw std::runtime_error("ipc_identity_failed");
        const std::wstring acl=L"D:P(A;;GA;;;SY)(A;;GA;;;"+std::wstring(sid)+L")";LocalFree(sid);
        PSECURITY_DESCRIPTOR descriptor=nullptr;
        if(!ConvertStringSecurityDescriptorToSecurityDescriptorW(acl.c_str(),SDDL_REVISION_1,&descriptor,nullptr))
            throw std::runtime_error("ipc_acl_failed");
        SECURITY_ATTRIBUTES security{sizeof(SECURITY_ATTRIBUTES),descriptor,FALSE};
        const auto name=L"Local\\gameqakit-wolf-"+std::to_wstring(pid)+L"-"+std::wstring(session.begin(),session.end());
        mapping=CreateFileMappingW(INVALID_HANDLE_VALUE,&security,PAGE_READWRITE,0,5*sizeof(Slot),name.c_str());
        const DWORD error=GetLastError();
        if(!mapping || error==ERROR_ALREADY_EXISTS) {
            LocalFree(descriptor);
            if(mapping) CloseHandle(mapping);mapping=nullptr;throw std::runtime_error("ipc_create_failed");
        }
        for(int i=0;i<5;++i) {
            locks[i]=CreateMutexW(&security,FALSE,(name+L"-"+std::to_wstring(i)).c_str());
            if(!locks[i] || GetLastError()==ERROR_ALREADY_EXISTS) {
                LocalFree(descriptor);for(auto h:locks) if(h) CloseHandle(h);
                CloseHandle(mapping);throw std::runtime_error("ipc_mutex_failed");
            }
        }
        LocalFree(descriptor);
        slots=static_cast<Slot*>(MapViewOfFile(mapping,FILE_MAP_ALL_ACCESS,0,0,5*sizeof(Slot)));
        if(!slots) { for(auto h:locks) CloseHandle(h);CloseHandle(mapping);mapping=nullptr;throw std::runtime_error("ipc_map_failed"); }
    }
    ~Realtime() { if(slots) UnmapViewOfFile(slots);if(mapping) CloseHandle(mapping);for(auto h:locks) if(h) CloseHandle(h); }
    Realtime(const Realtime&)=delete;
    Realtime& operator=(const Realtime&)=delete;
    struct Lock {
        HANDLE handle;
        explicit Lock(HANDLE h):handle(nullptr) {
            const auto result=WaitForSingleObject(h,0);
            if(result==WAIT_ABANDONED) { ReleaseMutex(h);throw std::runtime_error("ipc_writer_abandoned"); }
            if(result==WAIT_OBJECT_0) handle=h;
            else if(result!=WAIT_TIMEOUT) throw std::runtime_error("ipc_mutex_failed");
        }
        ~Lock() { if(handle) ReleaseMutex(handle); }
    };
    std::string read(Index index) const {
        Lock lock(locks[index]);if(!lock.handle) return {};
        auto& slot=slots[index];
        for(int attempt=0;attempt<3;++attempt) {
            const LONG before=InterlockedCompareExchange(&slot.sequence,0,0);
            if(before&1) return {};
            const DWORD length=slot.length;
            if(length>sizeof(slot.data)) throw std::runtime_error("ipc_message_too_large");
            std::string value(slot.data,length);MemoryBarrier();
            if(before==InterlockedCompareExchange(&slot.sequence,0,0)) return value;
        }
        return {};
    }
    bool write(Index index,const std::string& value) {
        if(value.size()>sizeof(Slot::data)) throw std::runtime_error("ipc_message_too_large");
        Lock lock(locks[index]);if(!lock.handle) return false;
        auto& slot=slots[index];const LONG before=InterlockedCompareExchange(&slot.sequence,0,0);
        const LONG odd=static_cast<LONG>(static_cast<unsigned long>(before)+1);
        if((before&1) || InterlockedCompareExchange(&slot.sequence,odd,before)!=before)
            throw std::runtime_error("ipc_writer_busy");
        std::memcpy(slot.data,value.data(),value.size());slot.length=static_cast<DWORD>(value.size());
        InterlockedExchange(&slot.sequence,static_cast<LONG>(static_cast<unsigned long>(before)+2));
        return true;
    }
};
}
