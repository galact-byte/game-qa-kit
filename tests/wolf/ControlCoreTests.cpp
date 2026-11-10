#include "../../plugins/wolf/Wire.hpp"
#include "../../plugins/wolf/StartupGuard.hpp"
#include <cassert>
#include <iostream>
#include <limits>

using namespace gameqa;
Command request(const char* id,const char* action="resume",int frames=1) {
    Command c;c.session="test";c.id=id;c.action=action;c.frames=frames;return c;
}
void run(Core& core,int frames,double now=1.0) {
    for(int i=0;i<frames;++i) core.tick(static_cast<unsigned>(core.frame+1),now+i*.01,100,core.pause);
}
void finish(Core& core) {
    run(core,3);assert(core.capture_pending);core.captured("screenshots/ok.png",1.1);core.acknowledge();
}
DWORD WINAPI small_stack_guard(void*) {
    wchar_t path[]=L"\\??\\E:\\DevData\\wolf-test\\runtime\\Game.ini";
    UNICODE_STRING name{};name.Buffer=path;name.Length=static_cast<USHORT>(wcslen(path)*sizeof(wchar_t));
    OBJECT_ATTRIBUTES object{};object.Length=sizeof(object);object.ObjectName=&name;
    return wolf_guard::permit(&object)?0:1;
}
int wmain(int argc,wchar_t** argv) {
    if(argc==3 && std::wstring(argv[1])==L"--startup-fixture") {
        const auto file=CreateFileW(argv[2],GENERIC_WRITE,0,nullptr,CREATE_ALWAYS,0,nullptr);
        if(file!=INVALID_HANDLE_VALUE) {CloseHandle(file);return 2;}
        HKEY key=nullptr;
        const auto status=RegCreateKeyExW(HKEY_CURRENT_USER,L"Software\\GameQAKitStartupGuardFixture",0,nullptr,0,KEY_WRITE,nullptr,&key,nullptr);
        if(status==ERROR_SUCCESS) {RegCloseKey(key);RegDeleteKeyW(HKEY_CURRENT_USER,L"Software\\GameQAKitStartupGuardFixture");return 3;}
        return 0;
    }
    {
        wchar_t executable[32768];assert(GetModuleFileNameW(nullptr,executable,32768));
        auto marker=wolf::fs::path(executable).parent_path()/L"startup-forbidden.txt";
        assert(!wolf::fs::exists(marker));
        std::wstring command=L"\""+std::wstring(executable)+L"\" --startup-fixture \""+marker.wstring()+L"\"";
        STARTUPINFOW startup{};startup.cb=sizeof(startup);PROCESS_INFORMATION child{};
        assert(CreateProcessW(executable,command.data(),nullptr,nullptr,FALSE,CREATE_SUSPENDED|DEBUG_ONLY_THIS_PROCESS,nullptr,nullptr,&startup,&child));
        assert(ResumeThread(child.hThread)==1);
        wolf::StartupGuard guard(child.hProcess);guard.run(child.dwThreadId,true);guard.save(marker.parent_path());
        assert(WaitForSingleObject(child.hProcess,5000)==WAIT_OBJECT_0);
        DWORD result=99;assert(GetExitCodeProcess(child.hProcess,&result));
        CloseHandle(child.hThread);CloseHandle(child.hProcess);
        if(result!=0 || wolf::fs::exists(marker)) {std::cerr<<"startup writes were not denied: exit="<<result<<" marker="<<wolf::fs::exists(marker)<<"\n";return 1;}
    }
    const auto fixture=wolf::environment(L"WOLF_TEST_ORIGINAL_EXE");
    if(!fixture.empty()) {
        bool accepted=false;
        try {
            const auto profile=wolf::validate_image(fixture);
            accepted=profile.input_call==0xcb114 && profile.input_function==0x2edf0 &&
                     profile.tail_call==0xcb1b9 && profile.tail_function==0x322a0 &&
                     profile.keyboard_device==0x757454;
        } catch(const std::exception&) {}
        if(!accepted) { std::cerr<<"known original image rejected\n";return 1; }
    }
    wolf_guard::root=L"E:\\DevData\\wolf-test";
    HANDLE thread=CreateThread(nullptr,65536,small_stack_guard,nullptr,STACK_SIZE_PARAM_IS_A_RESERVATION,nullptr);
    assert(thread && WaitForSingleObject(thread,5000)==WAIT_OBJECT_0);
    DWORD exit_code=1;assert(GetExitCodeThread(thread,&exit_code) && exit_code==0);CloseHandle(thread);
    assert(wolf_guard::allowed(L"\\??\\E:\\DevData\\wolf-test\\runtime\\Game.ini"));
    assert(!wolf_guard::allowed(L"\\??\\E:\\DevData\\wolf-test-other\\Game.ini"));
    assert(!wolf_guard::allowed(L"\\??\\E:\\DevData\\wolf-test\\..\\original\\Save9.dat"));
    assert(!wolf_guard::allowed(L"\\Device\\HarddiskVolume1\\Users\\original.dat"));
    assert(!wolf_guard::allowed(L"C:\\Users\\original.dat"));
    Core core("test");assert(core.paused && core.held.empty());
    assert(!core.accept(request("blocked","wait"),1,100));
    assert(core.receipt.error=="control_paused");core.acknowledge();
    assert(core.accept(request("resume"),1,100));finish(core);
    auto c=request("right","move",12);c.direction="right";
    const auto start=core.frame;assert(core.accept(c,1,100));
    for(int i=0;i<80;++i) core.observe("right");
    assert(core.frame==start && core.held=="right");
    run(core,11);assert(core.held=="right" && !core.capture_pending);
    run(core,1);assert(core.held.empty() && !core.capture_pending);
    run(core,2);assert(core.frame==start+14 && core.capture_pending);
    core.captured("screenshots/right.png",1.2);assert(core.receipt.ok && core.receipt.consumed);
    core.acknowledge();assert(!core.accept(c,2,100));assert(!core.active);

    Core missing("test");assert(missing.accept(request("resume"),1,100));finish(missing);
    c=request("not-consumed","press");c.button="ok";assert(missing.accept(c,1,100));run(missing,3);
    assert(missing.paused && missing.held.empty() && missing.receipt.error=="input_not_observed");

    Core guard("test");c=request("old-session");c.session="old";
    assert(!guard.accept(c,1,100) && !guard.active && guard.paused);
    c=request("old-pause");c.pause="old";assert(!guard.accept(c,1,100));
    assert(guard.receipt.error=="stale_command");guard.acknowledge();
    assert(guard.accept(request("valid"),1,100));
    guard.tick(0,1,100,"pause-1");assert(guard.paused && guard.held.empty());
    assert(guard.receipt.error=="manual_pause");guard.acknowledge();
    c=request("resume-new");c.pause="pause-1";assert(guard.accept(c,1,100));
    guard.tick(0,2,1.5,"pause-1");assert(guard.receipt.error=="lease_expired");

    Core stalled("test");assert(stalled.accept(request("resume"),1,100));
    stalled.tick(0,2.01,100,"");assert(stalled.receipt.error=="native_frame_stalled");
    Core rewind("test");assert(rewind.accept(request("resume"),1,100));run(rewind,1);
    rewind.tick(0,1.2,100,"");assert(rewind.receipt.error=="native_frame_rewound");
    Core jump("test");assert(jump.accept(request("resume"),1,100));jump.tick(2,1.1,100,"");
    assert(jump.receipt.error=="frame_boundary_missed");

    Core capture("test");assert(capture.accept(request("resume"),1,100));run(capture,3);
    assert(capture.capture_pending);capture.captured("",2.2);capture.tick(3,2.21,100,"");
    assert(!capture.paused);run(capture,30,2.22);assert(capture.capture_pending);
    capture.captured("screenshots/late.png",4.1);assert(capture.receipt.error=="screenshot_timeout");
    Core failed("test");assert(failed.accept(request("resume"),1,100));run(failed,3);
    failed.fail("capture_save_failed");assert(failed.paused && !failed.active && failed.held.empty());
    assert(!failed.receipt.ok && failed.receipt.error=="capture_save_failed");

    Core invalid("test");c=request("bad-frame");c.frames=121;assert(!invalid.accept(c,1,100));
    assert(invalid.receipt.error=="invalid_command");invalid.acknowledge();
    c=request("future");c.after=1;assert(!invalid.accept(c,1,100));invalid.acknowledge();
    assert(!invalid.accept(request("nan"),1,std::numeric_limits<double>::quiet_NaN()));
    assert(invalid.receipt.error=="stale_command");
    auto f=ini("[bridge]\nprotocol=1\npayload.frames=1\nafter_frame=0\npayload.action=resume\n");
    assert(parse_command(f).action=="resume");bool rejected=false;
    try {ini("[bridge]\nid=a\nid=b\n");} catch(...) {rejected=true;}assert(rejected);
    assert(lease_expiry(100,"test",ini("[bridge]\nsession_id=old\nexpires_at=200\n"))==100);
    std::cout<<"Wolf core: all safety checks passed\n";
}
