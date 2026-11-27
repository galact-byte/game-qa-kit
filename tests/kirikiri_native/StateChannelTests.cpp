#include "../../plugins/kirikiri/GuardState.hpp"
#include <iostream>

int main(){
    using namespace kirikiri_guard;
    DWORD before=0,after=0;
    if(!GetProcessHandleCount(GetCurrentProcess(),&before))return 1;
    for(unsigned i=0;i<32;++i){
        StateChannel channel;
        const auto handle=channel.create(GetCurrentProcess());
        DWORD flags=0;
        if(!GetHandleInformation(handle,&flags) || (flags&HANDLE_FLAG_INHERIT))return 2;
        auto shared=static_cast<SharedState*>(MapViewOfFile(handle,FILE_MAP_READ|FILE_MAP_WRITE,0,0,sizeof(SharedState)));
        CloseHandle(handle);
        if(!shared || !state_identity(*shared,GetCurrentProcess()))return 3;
        const auto magic=shared->magic;shared->magic=0;
        if(state_identity(*shared,GetCurrentProcess()))return 4;
        shared->magic=magic;shared->version=2;
        if(state_identity(*shared,GetCurrentProcess()))return 5;
        shared->version=1;--shared->bytes;
        if(state_identity(*shared,GetCurrentProcess()))return 6;
        ++shared->bytes;shared->pid=0;
        if(state_identity(*shared,GetCurrentProcess()))return 7;
        shared->pid=GetCurrentProcessId();++shared->created.dwLowDateTime;
        if(state_identity(*shared,GetCurrentProcess()))return 8;
        --shared->created.dwLowDateTime;
        if(!state_identity(*shared,GetCurrentProcess()))return 9;
        shared->value.shell_create_denied=7;
        if(channel.shared().value.shell_create_denied!=7)return 10;
        if(!UnmapViewOfFile(shared))return 11;
    }
    if(!GetProcessHandleCount(GetCurrentProcess(),&after) || before!=after)return 12;
    std::cout<<"{\"protocol_identity\":true,\"version_length_pid_creation_rejected\":true,"
                "\"shared_updates\":true,\"non_inherited_handle\":true,\"cycles\":32,\"handles_before\":"
             <<before<<",\"handles_after\":"<<after<<"}";
    return 0;
}
