#include "Native.hpp"
#include "InputCore.hpp"
#include "DeviceQueue.hpp"
#include "JsonWire.hpp"
#include "CapturePixels.hpp"
#include <iostream>
#include <stdexcept>
#include <thread>
using native_input::Core;
void require(bool value,const char* message){if(!value)throw std::runtime_error(message);}
Core ready(){Core c;require(c.paused,"startup paused");require(c.begin("1","resume","",0,1000,2500,""),"resume");c.finish();return c;}
int main(){try{
    namespace fs=std::filesystem;
    const auto folder=fs::temp_directory_path()/("native-publish-"+std::to_string(GetCurrentProcessId()));
    fs::create_directory(folder);const auto destination=folder/"status.json",temporary=folder/"status.json.tmp";
    {std::ofstream out(destination);out<<"old";}{std::ofstream out(temporary);out<<"new";}
    auto reader=CreateFileW(destination.c_str(),GENERIC_READ,FILE_SHARE_READ|FILE_SHARE_WRITE,nullptr,OPEN_EXISTING,0,nullptr);
    require(reader!=INVALID_HANDLE_VALUE,"open concurrent reader");
    std::thread closing([reader]{Sleep(30);CloseHandle(reader);});bool published=false;
    try{native_platform::publish(temporary,destination);published=true;}catch(const std::exception& error){std::cerr<<"publication diagnostic: "<<error.what()<<"\n";}
    closing.join();
    const bool replacement=published&&native_platform::text(destination)=="new"&&!fs::exists(temporary);
    fs::remove_all(folder);require(replacement,"brief concurrent reader must not prevent atomic publication");
    fs::create_directory(folder);{std::ofstream out(destination);out<<"old";}{std::ofstream out(temporary);out<<"new";}
    reader=CreateFileW(destination.c_str(),GENERIC_READ,FILE_SHARE_READ|FILE_SHARE_WRITE,nullptr,OPEN_EXISTING,0,nullptr);
    require(reader!=INVALID_HANDLE_VALUE,"open persistent reader");const auto blocked=GetTickCount64();bool blocked_rejected=false;
    try{native_platform::publish(temporary,destination);}catch(const std::exception& e){const std::string error=e.what();blocked_rejected=error=="evidence_publish_failed:32"||error=="evidence_publish_failed:5";}
    CloseHandle(reader);
    const bool preserved=blocked_rejected&&GetTickCount64()-blocked<1000&&native_platform::text(destination)=="old"&&native_platform::text(temporary)=="new";
    fs::remove_all(folder);require(preserved,"persistent reader fails within bound and preserves evidence");
    fs::create_directory(folder);{std::ofstream out(destination);out<<"lease";}
    reader=CreateFileW(destination.c_str(),GENERIC_READ,0,nullptr,OPEN_EXISTING,0,nullptr);
    require(reader!=INVALID_HANDLE_VALUE,"open exclusive control reader");
    std::thread unblock([reader]{Sleep(30);CloseHandle(reader);});std::string content;
    try{content=native_platform::text(destination);}catch(const std::exception& error){std::cerr<<error.what()<<'\n';}
    unblock.join();require(content=="lease","transient sharing conflict must not become an absent lease");
    reader=CreateFileW(destination.c_str(),GENERIC_READ,0,nullptr,OPEN_EXISTING,0,nullptr);
    require(reader!=INVALID_HANDLE_VALUE,"open persistent control reader");const auto read_started=GetTickCount64();bool read_rejected=false;
    try{native_platform::text(destination);}catch(const std::exception& error){read_rejected=std::string(error.what()).find("control_read_failed:")==0;}
    CloseHandle(reader);fs::remove_all(folder);
    require(read_rejected&&GetTickCount64()-read_started<1000,"persistent control sharing conflict fails within bound");
    require(native_platform::text(destination).empty(),"absent optional control file stays empty");
    fs::create_directory(folder);{std::ofstream out(destination);out<<std::string(65537,'a');}bool oversized=false;
    try{native_platform::text(destination);}catch(const std::exception& error){oversized=std::string(error.what())=="control_file_too_large";}
    fs::remove_all(folder);require(oversized,"oversized control file rejected");
    std::vector<unsigned char> blank_pixels(464*281*4,0);
    for(int x=0;x<464;++x)for(int c=0;c<3;++c)blank_pixels[x*4+c]=51;
    for(int y=0;y<281;++y)for(int c=0;c<3;++c)blank_pixels[(y*464+463)*4+c]=85;
    require(!native_input::usable_pixels(blank_pixels.data(),464,281),"compositor perimeter cannot make a blank client image usable");
    for(int y=100;y<148;++y)for(int x=100;x<148;++x)blank_pixels[(y*464+x)*4]=240;
    require(native_input::usable_pixels(blank_pixels.data(),464,281),"visible client content accepted");
    Core cold;require(!cold.begin("1","move","right",100,1000,2000,""),"cannot input before resume");
    auto c=ready();
    require(c.begin("2","move","right",100,1000,2000,""),"move");
    require(c.held=="right","held right");c.query(1099,true);c.tick(1099);require(c.held=="right","bounded hold");
    c.tick(1100);require(c.held.empty()&&!c.capture_pending,"release before capture");
    c.query(1101,false);require(c.capture_pending&&c.release_consumed,"release consumed before capture");c.finish();
    require(!c.begin("2","move","right",100,1200,2000,""),"duplicate command");
    require(c.begin("3","move","right",1000,1200,1300,""),"begin lease test");
    c.tick(1300);require(c.paused&&c.held.empty()&&c.error=="lease_expired","lease releases");
    auto boundary=ready();require(boundary.begin("2","move","right",100,1000,2500,""),"start boundary lease");
    boundary.tick(2500);require(boundary.paused&&boundary.error=="lease_expired","expiry wins at equal deadline");
    auto paused=ready();require(paused.begin("2","move","right",500,1400,2000,""),"begin pause test");
    paused.pause("human");require(paused.paused&&paused.held.empty(),"pause releases");
    require(!paused.begin("3","resume","",0,1500,2500,""),"stale pause generation");
    require(paused.begin("4","resume","",0,1500,2500,"human"),"new generation resume");paused.finish();
    auto capture=ready();require(capture.begin("2","wait","",100,1000,2500,""),"wait");capture.tick(1100);
    capture.renew(2000,3500);capture.tick(2000);capture.renew(3000,4500);capture.tick(4100);
    require(capture.paused&&capture.error=="capture_timeout","capture timeout");
    require(!capture.begin("3","move","right",1001,4700,6000,""),"duration limit");
    auto idle=ready();require(idle.begin("2","move","right",1000,1000,2500,""),"move idle test");idle.tick(2000);
    require(idle.paused&&idle.error=="input_not_queried","one second no input fails");
    auto release=ready();require(release.begin("2","confirm","ok",100,1000,2500,""),"confirm");
    release.query(1050,true);release.tick(1100);
    for(uint64_t t=1200;t<2100;t+=100)release.query(t,true);
    release.tick(2100);
    require(release.paused&&release.error=="input_release_unconfirmed","release timeout");
    auto expired=ready();expired.renew(2500,4000);
    require(expired.paused&&expired.error=="lease_expired","late renewal cannot revive expired lease");
    auto queried=ready();require(queried.begin("2","move","right",100,1000,2500,""),"capture query test");
    queried.query(1050,true);queried.tick(1100);queried.query(1101,false);
    for(uint64_t t=1200;t<4200;t+=100){queried.renew(t,t+1500);queried.query(t,false);}
    queried.tick(4101);
    require(queried.paused&&queried.error=="capture_timeout","queries cannot extend capture deadline");
    auto late_query=ready();require(late_query.begin("2","move","right",100,1000,2500,""),"late query setup");
    late_query.query(2500,true);
    require(late_query.paused&&late_query.held.empty()&&!late_query.press_consumed,"expired query cannot consume a held key");
    auto scoped=ready();require(scoped.begin("2","move","right",100,1000,2500,""),"scoped consumption setup");
    scoped.consume(1050,true,scoped.action_sequence-1);require(!scoped.press_consumed,"old buffered event cannot consume new action");
    scoped.consume(1051,true,scoped.action_sequence);require(scoped.press_consumed,"current buffered event consumes action");
    native_input::ReleaseTracker tracker;tracker.record(0xcd,true);
    require(!tracker.expired(0xcd,1000),"held key does not start release timer");
    require(!tracker.expired(0,1100)&&!tracker.expired(0,2099)&&tracker.expired(0,2100),"release confirmation has fixed one-second deadline");
    tracker.record(0xcd,false);require(!tracker.expired(0,2200)&&tracker.deadline==0,"consumed release clears confirmation deadline");
    native_input::DeviceQueue queue;
    require(queue.set(0xcd,true,7),"queue press");
    native_input::KeyEvent event{};
    require(queue.read(&event,1,true)==1&&event.code==0xcd&&event.down&&event.action==7,"peek press");
    require(queue.size()==1&&queue.state(0xcd),"peek does not consume or alter state");
    require(queue.read(&event,1,false)==1&&queue.size()==0,"consume press");
    require(queue.set(0xcd,false,7)&&!queue.state(0xcd),"queue release");
    require(queue.read(&event,1,false)==1&&!event.down&&event.sequence==2,"release sequence");
    require(queue.set(0xc8,true,8)&&queue.set(0x1c,true,8),"two pending downs");queue.release_all();
    native_input::KeyEvent released[4]{};
    const auto released_count=queue.read(released,4,false);
    require(released_count==2&&!released[0].down&&!released[1].down&&!queue.state(0xc8)&&!queue.state(0x1c),"pause discards downs and preserves releases");
    native_input::DeviceQueue pending_release;
    require(pending_release.set(0xcd,true,11),"press before repeated pause");
    pending_release.read(&event,1,false);pending_release.release_all();pending_release.release_all();
    require(pending_release.read(&event,1,false)==1&&!event.down,"repeated pause preserves unconsumed release");
    for(unsigned i=0;i<128;++i)require(queue.set(0xcd,i%2==0,9),"fill bounded queue");
    require(!queue.set(0xcd,true,9)&&queue.size()<=1&&!queue.state(0xcd),"overflow releases and fails");
    native_input::DeviceSlots slots;
    auto first=slots.add(10);require(first&&first->queue.set(0xcd,true,10),"register device");slots.remove(10);
    auto recreated=slots.add(10);require(recreated&&recreated->queue.size()==0&&!recreated->queue.state(0xcd),"recreated pointer has no stale input");
    require(slots.add(10)==recreated&&slots.count()==1,"same live device does not reset state");
    for(uintptr_t i=11;i<26;++i)require(slots.add(i)!=nullptr,"device capacity");
    require(!slots.add(26)&&slots.count()==16,"device registry bounded");
    const auto parsed=native_input::json("{\"protocol\":2,\"payload\":{\"duration_ms\":100},\"id\":\"a\\u0031\",\"created_at\":1.2e3}");
    require(native_input::integer(parsed,"protocol")==2&&native_input::integer(parsed,"payload.duration_ms")==100&&native_input::get(parsed,"id")=="a1"&&native_input::number(parsed,"created_at")==1200,"typed JSON wire");
    for(const auto& invalid:{"{\"protocol\":2,\"protocol\":1}","{\"a\":{\"b\":{}}}","{\"a\":[]}","{\"n\":NaN}","{\"n\":01}","{\"n\":1} garbage","{\"a.b\":1}","{\"id\":\"\\u0000\"}"}){
        bool rejected=false;try{native_input::json(invalid);}catch(const std::exception&){rejected=true;}require(rejected,"malformed JSON rejected");
    }
    for(const auto& invalid:{"{\"n\":true}","{\"n\":1.0}","{\"n\":\"1\"}","{\"n\":9223372036854775808}"}){
        bool rejected=false;try{native_input::integer(native_input::json(invalid),"n");}catch(const std::exception&){rejected=true;}require(rejected,"noninteger rejected");
    }
    std::cout<<"native core: PASS timing pause lease duplicate capture_timeout input_timeout release_timeout queue peek overflow device_lifecycle JSON_wire\n";return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
