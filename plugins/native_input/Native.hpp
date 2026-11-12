#pragma once
#include <windows.h>
#include <bcrypt.h>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <stdexcept>
#include <vector>
namespace native_platform {
namespace fs=std::filesystem;
inline std::wstring environment(const wchar_t* key) {
    wchar_t text[32768];const auto n=GetEnvironmentVariableW(key,text,32768);
    return n && n<32768?std::wstring(text,n):L"";
}
inline std::string utf8(const std::wstring& text) {
    if(text.empty()) return "";
    const auto n=WideCharToMultiByte(CP_UTF8,0,text.data(),static_cast<int>(text.size()),nullptr,0,nullptr,nullptr);
    std::string out(n,0);WideCharToMultiByte(CP_UTF8,0,text.data(),static_cast<int>(text.size()),out.data(),n,nullptr,nullptr);return out;
}
inline std::wstring wide(const std::string& text) {
    if(text.empty()) return L"";
    const auto n=MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,text.data(),static_cast<int>(text.size()),nullptr,0);
    if(!n) throw std::runtime_error("invalid_utf8");
    std::wstring out(n,0);MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,text.data(),static_cast<int>(text.size()),out.data(),n);return out;
}
inline double now() {
    FILETIME t;GetSystemTimeAsFileTime(&t);ULARGE_INTEGER value;value.LowPart=t.dwLowDateTime;value.HighPart=t.dwHighDateTime;
    return static_cast<double>(value.QuadPart)/10000000.0-11644473600.0;
}
inline void publish(const fs::path& temporary,const fs::path& destination) {
    const auto deadline=GetTickCount64()+100;
    for(;;){
        if(MoveFileExW(temporary.c_str(),destination.c_str(),MOVEFILE_REPLACE_EXISTING))return;
        const auto error=GetLastError();
        if((error!=ERROR_ACCESS_DENIED&&error!=ERROR_SHARING_VIOLATION&&error!=ERROR_LOCK_VIOLATION)||GetTickCount64()>=deadline)
            throw std::runtime_error("evidence_publish_failed:"+std::to_string(error));
        Sleep(2);
    }
}
inline std::string text(const fs::path& file) {
    const auto deadline=GetTickCount64()+100;
    std::vector<char> buffer(65537);HANDLE input=INVALID_HANDLE_VALUE;
    for(;;){
        input=CreateFileW(file.c_str(),GENERIC_READ,FILE_SHARE_READ|FILE_SHARE_WRITE|FILE_SHARE_DELETE,
                          nullptr,OPEN_EXISTING,FILE_ATTRIBUTE_NORMAL,nullptr);
        if(input!=INVALID_HANDLE_VALUE)break;
        const auto error=GetLastError();
        if(error==ERROR_FILE_NOT_FOUND||error==ERROR_PATH_NOT_FOUND)return "";
        if((error!=ERROR_ACCESS_DENIED&&error!=ERROR_SHARING_VIOLATION&&error!=ERROR_LOCK_VIOLATION)||GetTickCount64()>=deadline)
            throw std::runtime_error("control_read_failed:"+std::to_string(error));
        Sleep(2);
    }
    DWORD bytes=0;const auto read=ReadFile(input,buffer.data(),static_cast<DWORD>(buffer.size()),&bytes,nullptr);
    const auto error=GetLastError();CloseHandle(input);
    if(!read)throw std::runtime_error("control_read_failed:"+std::to_string(error));
    if(bytes>65536)throw std::runtime_error("control_file_too_large");
    return std::string(buffer.data(),bytes);
}
inline std::string hash(const fs::path& path) {
    BCRYPT_ALG_HANDLE algorithm=nullptr;BCRYPT_HASH_HANDLE state=nullptr;
    if(BCryptOpenAlgorithmProvider(&algorithm,BCRYPT_SHA256_ALGORITHM,nullptr,0)<0)
        throw std::runtime_error("hash_backend_unavailable");
    unsigned char digest[32];bool valid=BCryptCreateHash(algorithm,&state,nullptr,0,nullptr,0,0)>=0;
    std::ifstream input(path,std::ios::binary);valid=valid && bool(input);char buffer[65536];
    while(valid && (input.read(buffer,sizeof(buffer)) || input.gcount()))
        valid=BCryptHashData(state,reinterpret_cast<PUCHAR>(buffer),static_cast<ULONG>(input.gcount()),0)>=0;
    if(valid) valid=!input.bad() && BCryptFinishHash(state,digest,sizeof(digest),0)>=0;
    if(state) BCryptDestroyHash(state);BCryptCloseAlgorithmProvider(algorithm,0);
    if(!valid) throw std::runtime_error("image_hash_failed");
    std::ostringstream out;for(auto c:digest) out<<std::hex<<std::setw(2)<<std::setfill('0')<<static_cast<int>(c);return out.str();
}
inline void replace(void* address,const void* data,size_t size) {
    DWORD previous=0;
    if(!VirtualProtect(address,size,PAGE_EXECUTE_READWRITE,&previous)) throw std::runtime_error("hook_protect_failed");
    std::memcpy(address,data,size);DWORD unused=0;
    const bool restored=VirtualProtect(address,size,previous,&unused)!=FALSE;
    const bool flushed=FlushInstructionCache(GetCurrentProcess(),address,size)!=FALSE;
    if(!restored || !flushed) throw std::runtime_error("hook_publish_failed");
}
inline bool same_path(const fs::path& left,const fs::path& right) {
    return _wcsicmp(fs::weakly_canonical(left).c_str(),fs::weakly_canonical(right).c_str())==0;
}
}
