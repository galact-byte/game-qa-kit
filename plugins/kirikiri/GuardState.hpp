#pragma once
#include <windows.h>
#include <new>
#include <stdexcept>

namespace kirikiri_guard {
struct State {
    DWORD magic=0x4b514731, pid=GetCurrentProcessId(); volatile LONG denied=0; DWORD ready=0;
    // 0: strict; 1: authored fixture experiment; 2: explicit commercial compatibility.
    DWORD shell_experiment=0; volatile LONG shell_denied=0, shell_create_denied=0, cng_denied=0; wchar_t shell_counter[128]{};
    volatile LONG reporting=0;char api[48]{};wchar_t path[512]{};DWORD control=0;
};
constexpr DWORD state_protocol=0x47515332;
struct SharedState {
    DWORD magic=state_protocol, version=1, bytes=sizeof(SharedState), pid=0;
    FILETIME created{};
    State value{};
};
inline bool state_identity(const SharedState& shared,HANDLE process) {
    FILETIME created{},exited{},kernel{},user{};
    return shared.magic==state_protocol && shared.version==1 && shared.bytes==sizeof(SharedState) &&
        shared.pid==GetProcessId(process) && GetProcessTimes(process,&created,&exited,&kernel,&user) &&
        shared.created.dwLowDateTime==created.dwLowDateTime && shared.created.dwHighDateTime==created.dwHighDateTime;
}
// The unnamed section exists before the target runs. Keeping this view and handle
// in the host preserves the authoritative state after the entire target has exited.
class StateChannel {
    HANDLE section_=nullptr;
    SharedState* view_=nullptr;
public:
    StateChannel()=default;
    StateChannel(const StateChannel&)=delete;
    StateChannel& operator=(const StateChannel&)=delete;
    ~StateChannel(){if(view_)UnmapViewOfFile(view_);if(section_)CloseHandle(section_);}
    HANDLE create(HANDLE process) {
        if(section_)throw std::runtime_error("guard_state_already_created");
        section_=CreateFileMappingW(INVALID_HANDLE_VALUE,nullptr,PAGE_READWRITE,0,sizeof(SharedState),nullptr);
        if(!section_)throw std::runtime_error("guard_state_create_failed");
        const auto memory=MapViewOfFile(section_,FILE_MAP_READ|FILE_MAP_WRITE,0,0,sizeof(SharedState));
        if(!memory)throw std::runtime_error("guard_state_map_failed");
        view_=new(memory) SharedState{};
        FILETIME exited{},kernel{},user{};
        if(!GetProcessTimes(process,&view_->created,&exited,&kernel,&user))
            throw std::runtime_error("guard_state_process_identity_failed");
        view_->pid=GetProcessId(process);view_->value.pid=view_->pid;
        HANDLE remote=nullptr;
        if(!DuplicateHandle(GetCurrentProcess(),section_,process,&remote,SECTION_MAP_READ|SECTION_MAP_WRITE,FALSE,0))
            throw std::runtime_error("guard_state_duplicate_failed");
        return remote;
    }
    const SharedState& shared() const {
        if(!view_)throw std::runtime_error("guard_state_missing");
        return *view_;
    }
};
}
