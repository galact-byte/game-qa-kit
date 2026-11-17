#include "Guard.hpp"
#include <cstdio>
#include <io.h>
#include <share.h>
#include <iostream>
#include <map>

using namespace kirikiri_guard;
static bool write_test(const fs::path& path) {
    const auto file=CreateFileW(path.c_str(),GENERIC_WRITE,FILE_SHARE_READ|FILE_SHARE_WRITE,nullptr,CREATE_ALWAYS,0,nullptr);
    if(file==INVALID_HANDLE_VALUE) return false;
    DWORD count=0;const auto ok=WriteFile(file,"changed",7,&count,nullptr) && count==7;
    CloseHandle(file);return ok;
}
int wmain(int argc,wchar_t** argv) {
    if(argc!=3) return 2;
    const fs::path folder=argv[1],outside=argv[2];
    const auto external=CreateFileW(outside.c_str(),GENERIC_READ|GENERIC_WRITE|DELETE,FILE_SHARE_READ|FILE_SHARE_WRITE|FILE_SHARE_DELETE,nullptr,OPEN_EXISTING,0,nullptr);
    if(external==INVALID_HANDLE_VALUE) return 3;
    auto old_stderr=GetStdHandle(STD_ERROR_HANDLE);SetStdHandle(STD_ERROR_HANDLE,external);
    DWORD session=0;
    if(!ProcessIdToSessionId(GetCurrentProcessId(),&session)) return 4;
    const auto directory=L"\\Sessions\\"+std::to_wstring(session)+L"\\BaseNamedObjects";
    UNICODE_STRING directory_name{};directory_name.Buffer=const_cast<wchar_t*>(directory.c_str());
    directory_name.Length=static_cast<USHORT>(directory.size()*2);directory_name.MaximumLength=directory_name.Length;
    OBJECT_ATTRIBUTES directory_object{};directory_object.Length=sizeof(directory_object);directory_object.ObjectName=&directory_name;
    HANDLE directory_handle=nullptr;
    const auto open_directory=reinterpret_cast<NTSTATUS (NTAPI*)(PHANDLE,ACCESS_MASK,POBJECT_ATTRIBUTES)>(GetProcAddress(GetModuleHandleW(L"ntdll.dll"),"NtOpenDirectoryObject"));
    if(open_directory(&directory_handle,3,&directory_object)<0) return 4;
    const auto cng=CreateFileW(L"\\\\?\\GLOBALROOT\\Device\\CNG",GENERIC_READ,FILE_SHARE_READ|FILE_SHARE_WRITE,nullptr,OPEN_EXISTING,0,nullptr);
    if(cng==INVALID_HANDLE_VALUE) return 6;
    install(folder);
    std::map<std::string,bool> result;
    const auto ntopen_section=reinterpret_cast<OpenSection>(GetProcAddress(GetModuleHandleW(L"ntdll.dll"),"NtOpenSection"));
    auto section_case=[&](const std::string& label,std::wstring path,ACCESS_MASK access,bool fatal,HANDLE base=nullptr) {
        UNICODE_STRING text{};text.Buffer=path.data();text.Length=static_cast<USHORT>(path.size()*2);text.MaximumLength=text.Length;
        OBJECT_ATTRIBUTES attrs{};attrs.Length=sizeof(attrs);attrs.RootDirectory=base;attrs.ObjectName=&text;
        HANDLE handle=nullptr;const auto previous=state.denied;
        const auto status=ntopen_section(&handle,access,&attrs);
        result[label]=status==denied_status && handle==nullptr && state.denied==previous+(fatal?1:0);
        if(handle) CloseHandle(handle);
    };
    const auto counter=directory+L"\\windows_shell_global_counters";
#if defined(GAMEQA_SHELL_DENIAL_EXPERIMENT) || defined(GAMEQA_KIRIKIRI_COMMERCIAL_COMPAT)
    constexpr bool experiment=true;
#else
    constexpr bool experiment=false;
#endif
#ifdef GAMEQA_KIRIKIRI_COMMERCIAL_COMPAT
    result["commercial_policy_identity"]=state.shell_experiment==2;
#endif
    const auto ntcontrol=reinterpret_cast<Control>(GetProcAddress(GetModuleHandleW(L"ntdll.dll"),"NtDeviceIoControlFile"));
    auto control_case=[&](const char* label,HANDLE file,ULONG code,bool fatal) {
        IO_STATUS_BLOCK io{};unsigned char output[32]{};const auto previous=state.denied;
        const auto status=ntcontrol(file,nullptr,nullptr,nullptr,&io,code,nullptr,0,output,sizeof(output));
        result[label]=status==denied_status && state.denied==previous+(fatal?1:0);
        for(auto byte:output) result[label]=result[label] && byte==0;
    };
    control_case("cng_exact_denial_policy",cng,0x390008,!experiment);
    control_case("cng_neighbor_code_is_fatal",cng,0x390009,true);
    control_case("cng_same_code_other_handle_is_fatal",external,0x390008,true);
    CloseHandle(cng);
    const auto direct_input=directory+L"\\DirectInput.{5944E681-C92E-11CF-BFC7-444553540000}";
    section_case("directinput_open_denial_policy",direct_input,6,true);
    section_case("directinput_open_neighbor_is_fatal",direct_input+L"_other",6,true);
    section_case("directinput_open_extra_access_is_fatal",direct_input,7,true);
    section_case("section_exact_denial_policy",counter,6,!experiment);
    section_case("section_relative_denial_policy",L"windows_shell_global_counters",6,!experiment,directory_handle);
    section_case("section_neighbor_is_fatal",counter+L"_other",6,true);
    section_case("section_extra_access_is_fatal",counter,7,true);
    section_case("section_write_only_is_fatal",counter,2,true);
    section_case("section_other_session_is_fatal",L"\\Sessions\\"+std::to_wstring(session+1)+L"\\BaseNamedObjects\\windows_shell_global_counters",6,true);
    const auto ntcreate_section=reinterpret_cast<Section>(GetProcAddress(GetModuleHandleW(L"ntdll.dll"),"NtCreateSection"));
    auto create_case=[&](const std::string& label,std::wstring path,ACCESS_MASK access,bool fatal,HANDLE base=nullptr,HANDLE file=nullptr) {
        UNICODE_STRING text{};text.Buffer=path.data();text.Length=static_cast<USHORT>(path.size()*2);text.MaximumLength=text.Length;
        OBJECT_ATTRIBUTES attrs{};attrs.Length=sizeof(attrs);attrs.RootDirectory=base;attrs.ObjectName=&text;
        LARGE_INTEGER size{};size.QuadPart=4096;
        HANDLE handle=nullptr;const auto previous=state.denied;
        const auto status=ntcreate_section(&handle,access,&attrs,&size,PAGE_READONLY,SEC_COMMIT,file);
        result[label]=status==denied_status && handle==nullptr && state.denied==previous+(fatal?1:0);
        if(handle) CloseHandle(handle);
    };
    create_case("directinput_create_denial_policy",direct_input,0xf0007,true);
    create_case("directinput_create_neighbor_is_fatal",direct_input+L"_other",0xf0007,true);
    create_case("directinput_create_extra_access_is_fatal",direct_input,0xf000f,true);
    create_case("section_create_exact_denial_policy",counter,0xf0007,!experiment);
    create_case("section_create_relative_denial_policy",L"windows_shell_global_counters",0xf0007,!experiment,directory_handle);
    create_case("section_create_neighbor_is_fatal",counter+L"_other",0xf0007,true);
    create_case("section_create_extra_access_is_fatal",counter,0xf000f,true);
    create_case("section_create_less_access_is_fatal",counter,6,true);
    create_case("section_create_other_session_is_fatal",L"\\Sessions\\"+std::to_wstring(session+1)+L"\\BaseNamedObjects\\windows_shell_global_counters",0xf0007,true);
    create_case("section_create_file_backed_is_fatal",counter,0xf0007,true,nullptr,external);
    const auto fatal_before_mapping=state.denied;
    const auto named=CreateFileMappingW(INVALID_HANDLE_VALUE,nullptr,PAGE_READWRITE,0,4096,L"Local\\gameqa-authored-denied-section");
    result["named_section_creation_is_fatal"]=named==nullptr && state.denied==fatal_before_mapping+1;
    if(named) CloseHandle(named);
    CloseHandle(directory_handle);
    { Pins pins;result["normal_parent_pinned"]=pin_path(folder/L"savedata/normal.bin",pins); }
    result["normal_write"]=write_test(folder/L"savedata/normal.bin");
    result["external_absolute"]=!write_test(outside);
    result["external_parent"]=!write_test(folder/L"savedata/../../outside/sentinel.bin");
    result["prefix_sibling"]=!write_test(folder/L"savedata-other/sentinel.bin");
    result["code_read_only"]=!write_test(folder/L"game/code.tjs");
    result["host_commands_read_only"]=!write_test(folder/L"bridge/target-input.tjs");
    result["hardlink_rejected"]=!write_test(folder/L"savedata/hardlink.bin");
    result["junction_rejected"]=!write_test(folder/L"savedata/junction/sentinel.bin");
    DWORD count=0;
    result["existing_handle_write"]=!WriteFile(external,"bad",3,&count,nullptr);
    result["existing_handle_truncate"]=!SetEndOfFile(external);
    result["external_delete"]=!DeleteFileW(outside.c_str());
    result["external_rename"]=!MoveFileExW(outside.c_str(),(folder/L"savedata/stolen.bin").c_str(),0);
    result["hardlink_creation"]=!CreateHardLinkW((folder/L"savedata/newlink.bin").c_str(),outside.c_str(),nullptr);
    auto map=CreateFileMappingW(external,nullptr,PAGE_READWRITE,0,0,nullptr);
    result["external_writable_mapping"]=map==nullptr;
    if(map) CloseHandle(map);
    const auto error_stream=_wfsopen(outside.c_str(),L"wb",_SH_DENYNO);
    result["crt_external_open"]=error_stream==nullptr;
    if(error_stream) fclose(error_stream);
    result["crt_external_error"]=!WriteFile(GetStdHandle(STD_ERROR_HANDLE),"error",5,&count,nullptr);
    SetStdHandle(STD_ERROR_HANDLE,old_stderr);
    const auto root_handle=CreateFileW(folder.parent_path().c_str(),FILE_LIST_DIRECTORY,FILE_SHARE_READ|FILE_SHARE_WRITE|FILE_SHARE_DELETE,nullptr,OPEN_EXISTING,FILE_FLAG_BACKUP_SEMANTICS,nullptr);
    std::wstring relative=L"outside\\sentinel.bin";
    UNICODE_STRING name{};name.Buffer=relative.data();name.Length=static_cast<USHORT>(relative.size()*2);name.MaximumLength=name.Length;
    OBJECT_ATTRIBUTES object{};object.Length=sizeof(object);object.RootDirectory=root_handle;object.ObjectName=&name;
    IO_STATUS_BLOCK io{};HANDLE relative_file=nullptr;
    const auto ntcreate=reinterpret_cast<kirikiri_guard::CreateFile>(GetProcAddress(GetModuleHandleW(L"ntdll.dll"),"NtCreateFile"));
    result["root_directory_relative"]=ntcreate(&relative_file,GENERIC_WRITE,&object,&io,nullptr,0,3,1,0,nullptr,0)<0;
    if(relative_file) CloseHandle(relative_file);CloseHandle(root_handle);
    result["hook_integrity"]=intact();
    std::ostringstream details;
    for(auto p=folder/L"savedata";!p.empty();) {
        auto h=CreateFileW(p.c_str(),FILE_READ_ATTRIBUTES,FILE_SHARE_READ|FILE_SHARE_WRITE,nullptr,OPEN_EXISTING,FILE_FLAG_BACKUP_SEMANTICS|FILE_FLAG_OPEN_REPARSE_POINT,nullptr);
        details<<native_platform::utf8(p.wstring())<<" => "<<native_platform::utf8(final_path(h))<<" error="<<GetLastError()<<'\n';if(h!=INVALID_HANDLE_VALUE)CloseHandle(h);
        auto parent=p.parent_path();if(parent==p)break;p=parent;
    }
    for(const auto& item:result) details<<item.first<<'='<<item.second<<'\n';
    auto detail=details.str();IO_STATUS_BLOCK printed{};
    write_file(GetStdHandle(STD_OUTPUT_HANDLE),nullptr,nullptr,nullptr,&printed,detail.data(),static_cast<ULONG>(detail.size()),nullptr,nullptr);
    std::ofstream report(folder/L"bridge/target-observer.tjs");
    report<<"{";bool first=true;for(const auto& item:result) {if(!first)report<<',';first=false;report<<'"'<<item.first<<"\":"<<(item.second?"true":"false");}report<<"}";report.close();
    for(const auto& item:result) if(!item.second) return 1;
    return report?0:5;
}
