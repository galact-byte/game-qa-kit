// Compile the production bridge cycle and input hook; only delay the OS status publication.
#include <windows.h>
#include <atomic>
#include <future>
#include <iostream>
#include <thread>
#include "Native.hpp"
static HANDLE release_read=nullptr;
static std::atomic<bool> delay_control_read{false};
namespace wolf {
std::string delayed_text(const std::filesystem::path& path) {
    if(delay_control_read && path.extension()==L".ini") WaitForSingleObject(release_read,3000);
    return text(path);
}
}
static HANDLE publishing=nullptr,release_publish=nullptr;
static std::atomic<bool> delay_status{false},fail_status{false},delay_receipt{false};
BOOL WINAPI delayed_replace(LPCWSTR from,LPCWSTR to,DWORD flags) {
    const auto name=wcsrchr(to,L'\\');
    const auto slash=wcsrchr(to,L'/');
    const wchar_t* leaf=name?name+1:slash?slash+1:to;
    if(wcscmp(leaf,L"status.json")==0) {
        if(fail_status) {SetLastError(ERROR_ACCESS_DENIED);return FALSE;}
        if(delay_status) {SetEvent(publishing);WaitForSingleObject(release_publish,3000);}
    }
    if(delay_receipt && wcscmp(leaf,L"evidence.json")==0) {
        SetEvent(publishing);WaitForSingleObject(release_publish,5000);
    }
    return MoveFileExW(from,to,flags);
}
#define MoveFileExW delayed_replace
#define text delayed_text
#include "Bridge.cpp"
#undef text
#undef MoveFileExW
void production_cycle() {
    unsigned audits=0;
#include "BridgeCycle.inc"
}
HRESULT WINAPI fixture_state(void*,DWORD,void*) {return S_OK;}
int wmain(int argc,wchar_t** argv) {
    if(argc!=2) return 2;
    directory=argv[1];fs::create_directories(directory/L"results");session="fixture";
    realtime.reset(new wolf::Realtime(GetCurrentProcessId(),session));
    executable=directory/L"Game.exe";executable_utf8=utf8(executable.wstring());
    ready=true;at_boundary=true;keyboard=reinterpret_cast<void*>(1);original_get_state=fixture_state;
    publishing=CreateEventW(nullptr,TRUE,FALSE,nullptr);release_publish=CreateEventW(nullptr,TRUE,FALSE,nullptr);
    bool passed=true;
    release_read=CreateEventW(nullptr,TRUE,FALSE,nullptr);
    core.reset(new gameqa::Core(session));
    delay_control_read=true;
    auto stalled_read=std::async(std::launch::async,[] {unsigned char buffer[256]{};get_state(keyboard,256,buffer);});
    const bool read_responsive=stalled_read.wait_for(std::chrono::milliseconds(250))==std::future_status::ready;
    SetEvent(release_read);stalled_read.get();delay_control_read=false;
    std::cout<<"control_read: "<<(read_responsive?"PASS":"FAIL")<<std::endl;
    passed=passed && read_responsive;CloseHandle(release_read);
    for(const auto* mode:{"progress","pause","lease"}) {
        core.reset(new gameqa::Core(session));completed=0;expiry=0;generation="";
        realtime->write(wolf::Realtime::Pause,"[bridge]\nsession_id=fixture\nid=\n");
        realtime->write(wolf::Realtime::Request,"");
        std::ostringstream lease;lease<<"[bridge]\nsession_id=fixture\nexpires_at="<<std::setprecision(16)<<now()+10<<"\n";
        realtime->write(wolf::Realtime::Lease,lease.str());
        core->paused=false;
        gameqa::Command press;press.session=session;press.id="press";press.action="press";press.button="up";
        core->accept(press,now(),now()+10);
        ResetEvent(publishing);ResetEvent(release_publish);delay_status=true;
        std::thread publisher(production_cycle);
        if(WaitForSingleObject(publishing,2000)!=WAIT_OBJECT_0) {SetEvent(release_publish);publisher.join();return 3;}
        if(std::string(mode)=="pause") realtime->write(wolf::Realtime::Pause,"[bridge]\nsession_id=fixture\nid=manual\n");
        if(std::string(mode)=="lease") realtime->write(wolf::Realtime::Lease,"[bridge]\nsession_id=fixture\nexpires_at=1\n");
        auto input=std::async(std::launch::async,[] {unsigned char buffer[256]{};get_state(keyboard,256,buffer);return buffer[0xc8];});
        const bool responsive=input.wait_for(std::chrono::milliseconds(250))==std::future_status::ready;
        SetEvent(release_publish);publisher.join();const auto key=input.get();delay_status=false;
        const bool expected=std::string(mode)=="progress"?key==128 && !core->paused:key==0 && core->paused;
        const bool ok=responsive && expected;passed=passed && ok;
        std::cout<<mode<<": "<<(ok?"PASS":"FAIL")<<" responsive="<<responsive<<" key="<<unsigned(key)<<" error="<<core->error<<std::endl;
    }
    core.reset(new gameqa::Core(session));core->paused=false;
    gameqa::Command press;press.session=session;press.id="write-failure";press.action="press";press.button="up";
    core->accept(press,now(),now()+10);fail_status=true;status();fail_status=false;
    const bool failed_safely=core->paused && core->held.empty() && core->error=="bridge_replace_failed" &&
        wolf::text(directory/L"results"/L"write-failure.json").find("\"ok\":false")!=std::string::npos;
    std::cout<<"publication_failure: "<<(failed_safely?"PASS":"FAIL")<<std::endl;
    passed=passed && failed_safely;
    core.reset(new gameqa::Core(session));completed=0;expiry=0;generation="";
    realtime->write(wolf::Realtime::Pause,"[bridge]\nsession_id=fixture\nid=\n");
    std::ostringstream lease;lease<<"[bridge]\nsession_id=fixture\nexpires_at="<<std::setprecision(16)<<now()+10<<"\n";
    realtime->write(wolf::Realtime::Lease,lease.str());core->paused=false;
    gameqa::Command waiting;waiting.session=session;waiting.id="evidence";waiting.action="wait";
    core->accept(waiting,now(),now()+10);
    for(unsigned i=1;i<=3;++i) {completed=i;guard();}
    core->captured("screenshots/evidence.png",now());
    ResetEvent(publishing);ResetEvent(release_publish);delay_receipt=true;
    std::thread evidence([] {receipt();});
    if(WaitForSingleObject(publishing,2000)!=WAIT_OBJECT_0) {SetEvent(release_publish);evidence.join();return 4;}
    realtime->write(wolf::Realtime::Pause,"[bridge]\nsession_id=fixture\nid=evidence-pause\n");
    auto safety=std::async(std::launch::async,[] {control_cycle();return core->paused && core->error=="manual_pause";});
    const bool bounded=safety.wait_for(std::chrono::milliseconds(250))==std::future_status::ready;
    SetEvent(release_publish);evidence.join();delay_receipt=false;
    const bool evidence_safe=safety.get() && bounded;
    std::cout<<"receipt_block: "<<(evidence_safe?"PASS":"FAIL")<<std::endl;passed=passed && evidence_safe;

    core.reset(new gameqa::Core(session));completed=0;expiry=0;generation="";
    realtime->write(wolf::Realtime::Pause,"[bridge]\nsession_id=fixture\nid=\n");
    core->paused=false;core->accept(waiting,now(),now()+10);
    for(unsigned i=1;i<=3;++i) {completed=i;guard();}
    Sleep(3100);control_cycle();
    const bool capture_stopped=core->paused && core->held.empty() && core->error=="screenshot_timeout";
    std::cout<<"capture_timeout: "<<(capture_stopped?"PASS":"FAIL")<<std::endl;passed=passed && capture_stopped;
    CloseHandle(publishing);CloseHandle(release_publish);
    return passed?0:1;
}
