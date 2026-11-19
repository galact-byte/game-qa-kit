#pragma once
#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <Windows.h>
#include <memory>
#include <string>
#include "BridgeState.h"

extern bool _IO_KeyDown[];

namespace gameqa_gm {
inline std::wstring executable_directory() {
    wchar_t path[32768]={};
    const DWORD size=GetModuleFileNameW(nullptr,path,32768);
    if (!size || size>=32768) return {};
    std::wstring result(path,size);
    const auto slash=result.find_last_of(L"\\/");
    return slash==std::wstring::npos ? std::wstring() : result.substr(0,slash+1);
}

// Records are immutable after rename. Readers allow delete-sharing so retiring
// a consumed name never blocks the target game thread on an external reader.
inline int read_exact(const std::wstring& path, void* value, DWORD size) {
    HANDLE file=CreateFileW(path.c_str(),GENERIC_READ,FILE_SHARE_READ|FILE_SHARE_DELETE,
                           nullptr,OPEN_EXISTING,FILE_ATTRIBUTE_NORMAL,nullptr);
    if (file==INVALID_HANDLE_VALUE)
        return GetLastError()==ERROR_FILE_NOT_FOUND ? 0 : -1;
    LARGE_INTEGER length={}; DWORD bytes=0;
    const bool ok=GetFileSizeEx(file,&length) && length.QuadPart==size
                  && ReadFile(file,value,size,&bytes,nullptr) && bytes==size;
    CloseHandle(file);
    return ok ? 1 : -1;
}

class Bridge {
public:
    explicit Bridge(std::wstring directory=executable_directory()) : root(std::move(directory)) {
        if (root.empty()) return;
        if (root.back()!=L'\\' && root.back()!=L'/') root+=L"/";
        Binding binding={};
        if (read_exact(root+L"session.bin",&binding,sizeof(binding))!=1
            || std::memcmp(binding.magic,"GQBGM02",8) || binding.version!=2
            || binding.pid!=GetCurrentProcessId()) return;
        for (char ch:binding.token) if (!((ch>='0' && ch<='9') || (ch>='a' && ch<='f'))) return;
        state=std::make_unique<State>(binding);
    }

    void before() {
        _IO_KeyDown[VK_RIGHT]=false;
        if (!state) return;
        const auto now=GetTickCount64();
        bool processed=false;
        for (unsigned i=0;i<8;++i) {
            Command command={};
            const auto path=root+L"input."+std::to_wstring(generation)+L".bin";
            const int result=read_exact(path,&command,sizeof(command));
            if (!result) break;
            processed=true;
            if (result<0 || generation==UINT32_MAX) { state->fault(1); break; }
            state->tick(&command,now);
            if (generation) DeleteFileW((root+L"input."+std::to_wstring(generation-1)+L".bin").c_str());
            ++generation;
            if (state->reason==1 || state->reason==5) break;
        }
        if (!processed) {
            if (generation) {
                Command current={};
                const auto path=root+L"input."+std::to_wstring(generation-1)+L".bin";
                if (read_exact(path,&current,sizeof(current))!=1
                    || std::memcmp(&current,&state->command,sizeof(current))) {
                    state->fault(4); return;
                }
            }
            state->tick(nullptr,now);
        }
        _IO_KeyDown[VK_RIGHT]=state->held;
    }

    void after(bool consumed) {
        if (!state) { _IO_KeyDown[VK_RIGHT]=false; return; }
        state->after(consumed);
        const auto record=state->snapshot();
        const auto destination=root+L"consumed."+std::to_wstring(record.sample)+L".bin";
        const auto temporary=root+L"consumed.part";
        HANDLE file=CreateFileW(temporary.c_str(),GENERIC_WRITE,FILE_SHARE_READ,nullptr,
                               CREATE_ALWAYS,FILE_ATTRIBUTE_NORMAL,nullptr);
        DWORD bytes=0;
        bool ok=false;
        if (file!=INVALID_HANDLE_VALUE) {
            ok=WriteFile(file,&record,sizeof(record),&bytes,nullptr) && bytes==sizeof(record);
            ok=CloseHandle(file) && ok;
        }
        if (!ok || !MoveFileExW(temporary.c_str(),destination.c_str(),MOVEFILE_WRITE_THROUGH)) {
            state->fault(4); _IO_KeyDown[VK_RIGHT]=false; DeleteFileW(temporary.c_str());
            return;
        }
        if (record.sample>2)
            DeleteFileW((root+L"consumed."+std::to_wstring(record.sample-2)+L".bin").c_str());
        if (state->closing) PostQuitMessage(0);
    }

private:
    std::wstring root;
    std::unique_ptr<State> state;
    std::uint32_t generation=0;
};

inline Bridge& bridge() { static Bridge instance; return instance; }
}
