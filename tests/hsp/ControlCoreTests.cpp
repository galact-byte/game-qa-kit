#include "../../plugins/hsp/ControlCore.hpp"
#include <iostream>
#include <stdexcept>
#include <limits>
using namespace gameqa;
static int assertions=0;
static void check(bool value,const char* reason) {
    ++assertions; if(!value) throw std::runtime_error(reason);
}
static Command request(const char* id,const char* action="resume") {
    Command c;c.session="test";c.id=id;c.action=action;return c;
}
static void finish(Core& c,bool observed=true) {
    for(int i=0;i<8 && !c.receipt.ready;++i) {
        c.begin(100,102,c.pause);
        if(observed) c.observe(c.held);
        if(c.end()) c.captured("screenshots/test.png");
    }
}
static void resume(Core& c,const char* id="r") {
    auto r=request(id);r.pause=c.pause;
    check(c.accept(r,100,102),"valid resume rejected");finish(c);
    check(c.receipt.ready && c.receipt.ok && !c.paused,"resume did not finish");c.acknowledge();
}
int main() {
 try {
    Core c("test");check(c.paused && c.held.empty(),"startup must be paused");
    auto foreign=request("foreign");foreign.session="other";
    check(!c.accept(foreign,100,102) && !c.receipt.ready,"foreign request affected session");
    resume(c);
    auto move=request("move","move");move.direction="right";move.frames=2;move.after=c.frame;
    check(c.accept(move,100,102) && c.held=="right","direction not held");
    check(!c.accept(request("busy"),100,102),"accepted command while busy");
    const auto before=c.frame;finish(c);
    check(c.receipt.ok && c.receipt.consumed && c.receipt.frame>before && c.held.empty(),"fresh consumed frame missing");
    c.acknowledge();check(!c.accept(move,100,102) && !c.active,"duplicate command replayed");
    auto stale=request("stale");stale.pause="old";
    check(!c.accept(stale,100,102) && c.paused && c.receipt.error=="stale_command","old pause accepted");c.acknowledge();
    resume(c,"r2");move.id="interrupt";move.after=0;
    check(c.accept(move,100,102),"second move rejected");
    c.begin(100,102,"p1");
    check(c.paused && c.held.empty() && c.receipt.error=="manual_pause","pause failed to release input");c.acknowledge();
    resume(c,"r3");move.id="expiry";move.pause="p1";
    check(c.accept(move,100,102),"expiry setup failed");c.begin(103,102,"p1");
    check(c.paused && c.held.empty() && c.receipt.error=="lease_expired","expired lease retained key");c.acknowledge();
    auto invalid=request("invalid");invalid.pause="p1";invalid.frames=121;
    check(!c.accept(invalid,100,102) && c.receipt.error=="invalid_command","oversized hold accepted");c.acknowledge();
    auto future=request("future");future.pause="p1";future.after=c.frame+1;
    check(!c.accept(future,100,102),"future frame accepted");c.acknowledge();
    for(int i=0;i<3;++i) {
        auto r=request(i==0?"nan":i==1?"inf":"past");r.pause="p1";
        double expiry=i==0?std::numeric_limits<double>::quiet_NaN():i==1?std::numeric_limits<double>::infinity():99;
        check(!c.accept(r,100,expiry) && c.receipt.error=="stale_command","invalid expiry accepted");c.acknowledge();
    }
    auto badnow=request("badnow");badnow.pause="p1";
    check(!c.accept(badnow,std::numeric_limits<double>::quiet_NaN(),102),"invalid wall time accepted");c.acknowledge();
    auto traversal=request("../outside");traversal.pause="p1";
    check(!c.accept(traversal,100,102) && !c.receipt.ready,"unsafe command id accepted");
    resume(c,"r4");move.id="unobserved";move.pause="p1";
    check(c.accept(move,100,102),"unobserved setup failed");finish(c,false);
    check(c.paused && !c.receipt.ok && c.receipt.error=="input_not_observed","unread key reported consumed");c.acknowledge();
    auto click=request("bounds","click");click.pause="p1";click.x=-1;
    check(!c.accept(click,100,102) && c.receipt.error=="invalid_command","negative coordinate accepted");c.acknowledge();
    auto press=request("unknown","press");press.pause="p1";press.button="invented";
    check(!c.accept(press,100,102),"unknown input accepted");
    std::cout<<assertions<<" HSP control assertions passed\n";
 } catch(const std::exception& e) { std::cerr<<"FAIL: "<<e.what()<<"\n";return 1; }
}
