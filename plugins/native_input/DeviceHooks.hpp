#pragma once
#define DIRECTINPUT_VERSION 0x0800
#include <dinput.h>
#include "EasyHook.hpp"
#include <map>
#include <set>
#include <mutex>
#include <array>
#include "DeviceQueue.hpp"
namespace native_bridge {
struct Device{native_input::DeviceQueue queue;native_input::ReleaseTracker releases;bool mouse=false;unsigned code=0;};
inline std::map<void*,Device> devices;
inline std::mutex hooks_mutex;
inline std::set<void*> hooked;
inline std::vector<HOOK_TRACE_INFO> hooks;
inline unsigned scan(const std::string& key){
    if(key=="right")return DIK_RIGHT;if(key=="left")return DIK_LEFT;if(key=="up")return DIK_UP;
    if(key=="down")return DIK_DOWN;if(key=="ok")return DIK_Z;if(key=="cancel")return DIK_X;return 0;
}
inline void hook(void* address,void* callback){
    std::lock_guard<std::mutex> lock(hooks_mutex);if(hooked.count(address))return;
    HOOK_TRACE_INFO trace{};
    if(!address||LhInstallHook(address,callback,nullptr,&trace)<0)throw std::runtime_error("input_hook_failed");
    ULONG dummy=0;if(LhSetExclusiveACL(&dummy,0,&trace)<0)throw std::runtime_error("input_hook_acl_failed");
    hooks.push_back(trace);hooked.insert(address);
}
inline void synchronize(){
    const auto now=GetTickCount64();
    for(auto& item:devices){auto& device=item.second;if(device.mouse)continue;
        const auto code=scan(core.held);
        if(core.paused){device.queue.release_all();device.code=0;}
        else if(device.code!=code){
            if(device.code&&!device.queue.set(device.code,false,core.action_sequence))core.fail("device_queue_overflow");
            device.code=code;
            if(code&&!device.queue.set(code,true,core.action_sequence))core.fail("device_queue_overflow");
        }
        if(device.releases.expired(core.paused?0:code,now))core.fail("input_release_unconfirmed");
    }
    if(core.paused)for(auto& item:devices){item.second.queue.release_all();item.second.code=0;}
}
inline HRESULT STDMETHODCALLTYPE state(void* self,DWORD size,LPVOID output){
    std::lock_guard<std::mutex> lock(mutex);auto found=devices.find(self);
    if(found==devices.end())return DIERR_NOTACQUIRED;
    auto& device=found->second;
    if(!output||(device.mouse?(size!=sizeof(DIMOUSESTATE)&&size!=sizeof(DIMOUSESTATE2)):size!=256))return DIERR_INVALIDPARAM;
    core.tick(GetTickCount64());synchronize();std::memset(output,0,size);
    if(!device.mouse){
        if(device.code)static_cast<BYTE*>(output)[device.code]=0x80;
        device.releases.observed.fill(false);if(device.code)device.releases.record(device.code,true);
        core.query(GetTickCount64(),device.code!=0);synchronize();
    }
    return DI_OK;
}
inline HRESULT STDMETHODCALLTYPE data(void* self,DWORD stride,LPDIDEVICEOBJECTDATA output,LPDWORD count,DWORD flags){
    std::lock_guard<std::mutex> lock(mutex);auto found=devices.find(self);
    if(found==devices.end())return DIERR_NOTACQUIRED;
    if(!count||(stride!=sizeof(DIDEVICEOBJECTDATA)&&stride!=16)||(flags&~DIGDD_PEEK))return DIERR_INVALIDPARAM;
    core.tick(GetTickCount64());synchronize();auto& device=found->second;
    if(device.mouse){*count=0;return DI_OK;}
    const auto requested=*count;const bool peek=(flags&DIGDD_PEEK)!=0;
    std::array<native_input::KeyEvent,128> events{};
    const auto amount=device.queue.read(events.data(),(std::min)(static_cast<size_t>(requested),events.size()),peek||!output);
    if(output)for(size_t i=0;i<amount;++i){
        DIDEVICEOBJECTDATA event{};event.dwOfs=events[i].code;event.dwData=events[i].down?0x80:0;
        event.dwTimeStamp=static_cast<DWORD>(GetTickCount());event.dwSequence=events[i].sequence;
        std::memcpy(reinterpret_cast<BYTE*>(output)+i*stride,&event,stride);
        if(!peek){device.releases.record(events[i].code,events[i].down);core.consume(GetTickCount64(),events[i].down,events[i].action);}
    }
    if(!output&&!peek&&requested==INFINITE)device.queue.read(events.data(),events.size(),false);
    if(!peek&&output&&amount==0){core.tick(GetTickCount64());core.last_query=GetTickCount64();}
    *count=static_cast<DWORD>(amount);synchronize();return DI_OK;
}
inline HRESULT STDMETHODCALLTYPE acquire(void* self){std::lock_guard<std::mutex> lock(mutex);return devices.count(self)?DI_OK:DIERR_NOTACQUIRED;}
inline HRESULT STDMETHODCALLTYPE cooperative(void* self,HWND,DWORD){return acquire(self);}
inline ULONG STDMETHODCALLTYPE release(void* self){
    const auto original=reinterpret_cast<ULONG(STDMETHODCALLTYPE*)(void*)>((*static_cast<void***>(self))[2]);
    const auto remaining=original(self);
    if(!remaining){std::lock_guard<std::mutex> lock(mutex);auto found=devices.find(self);if(found!=devices.end()){devices.erase(found);if(core.active)core.fail("device_destroyed");}}
    return remaining;
}
inline HRESULT STDMETHODCALLTYPE create_device(void* self,REFGUID guid,void** output,LPUNKNOWN outer){
    auto original=reinterpret_cast<HRESULT(STDMETHODCALLTYPE*)(void*,REFGUID,void**,LPUNKNOWN)>((*static_cast<void***>(self))[3]);
    const auto result=original(self,guid,output,outer);
    if(SUCCEEDED(result)&&output&&*output){
        try{
            const bool keyboard=IsEqualGUID(guid,GUID_SysKeyboard)!=FALSE,mouse=IsEqualGUID(guid,GUID_SysMouse)!=FALSE;
            if(!keyboard&&!mouse)throw std::runtime_error("unsupported_input_device");
            {std::lock_guard<std::mutex> lock(mutex);if(core.active)core.fail("device_recreated");
                if(devices.size()>=16&&!devices.count(*output))throw std::runtime_error("device_capacity_exceeded");
                devices[*output].mouse=mouse;}
            const auto table=*static_cast<void***>(*output);
            hook(table[2],reinterpret_cast<void*>(release));hook(table[7],reinterpret_cast<void*>(acquire));
            hook(table[9],reinterpret_cast<void*>(state));hook(table[10],reinterpret_cast<void*>(data));hook(table[13],reinterpret_cast<void*>(cooperative));
        }catch(const std::exception& error){std::lock_guard<std::mutex> lock(mutex);core.fail(error.what());return DIERR_UNSUPPORTED;}
    }
    return result;
}
inline HRESULT WINAPI create(HINSTANCE instance,DWORD version,REFIID iid,LPVOID* output,LPUNKNOWN outer){
    auto original=reinterpret_cast<HRESULT(WINAPI*)(HINSTANCE,DWORD,REFIID,LPVOID*,LPUNKNOWN)>(GetProcAddress(GetModuleHandleW(L"dinput8.dll"),"DirectInput8Create"));
    const auto result=original(instance,version,iid,output,outer);
    if(SUCCEEDED(result)&&output&&*output)try{hook((*static_cast<void***>(*output))[3],reinterpret_cast<void*>(create_device));}
        catch(const std::exception& error){std::lock_guard<std::mutex> lock(mutex);core.fail(error.what());return DIERR_UNSUPPORTED;}
    return result;
}
inline void install_directinput(){
    auto module=LoadLibraryW(L"dinput8.dll");if(!module)throw std::runtime_error("directinput_unavailable");
    hook(reinterpret_cast<void*>(GetProcAddress(module,"DirectInput8Create")),reinterpret_cast<void*>(create));
}
}
