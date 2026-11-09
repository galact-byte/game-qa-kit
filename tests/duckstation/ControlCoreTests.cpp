#include "../../plugins/duckstation/Wire.hpp"
#include "../../plugins/duckstation/FrameEvidence.hpp"
#include <cstdlib>
#include <iostream>
using namespace gameqa;
static void check(bool ok,const char* text) { if(!ok) { std::cerr<<text<<'\n';std::exit(1); } }
static Command request(const char* id,const char* action,int frames=1) {
    Command c;c.id=id;c.action=action;c.session="current";c.frames=frames;c.after=100;return c;
}
static void step(Core& c,unsigned frame,unsigned buttons=0) { c.tick(frame,10.0,20.0,c.pause,buttons); }
static Core resumed() {
    Core c("current",100);check(c.accept(request("resume","resume"),10,20),"resume rejected");
    step(c,101);step(c,102);step(c,103);check(c.capture_pending,"resume needs a new executed frame");
    c.captured("screenshots/resume.png",10);c.acknowledge();return c;
}
int main() {
    unsigned char black[16]={0,0,0,0,0,0,255,255,0,0,0,0,0,0,255,255};
    unsigned char white[16]={255,255,255,255,255,255,0,0,255,255,255,255,255,255,0,0};
    check(!usable_bgr(black,2,2,8),"black capture accepted because of row padding");
    check(!usable_bgr(white,2,2,8),"white capture accepted because of row padding");
    black[10]=1;check(usable_bgr(black,2,2,8),"near-black scene rejected as a black frame");
    check(usable_bgr(black+8,2,2,-8),"bottom-up scene rejected");
    black[10]=0;black[3]=black[4]=black[5]=255;
    check(usable_bgr(black,2,2,8),"mixed black and white scene rejected");
    Fields lease={{"session_id","current"},{"expires_at","20"}};
    check(lease_expiry(12,"current",{})==12,"temporary missing lease discarded valid deadline");
    check(lease_expiry(12,"current",lease)==20,"valid lease was not renewed");
    lease["session_id"]="foreign";lease["expires_at"]="999";
    check(lease_expiry(12,"current",lease)==12,"foreign lease extended deadline");
    lease["session_id"]="current";lease["expires_at"]="5";
    check(lease_expiry(12,"current",lease)==5,"expired lease deadline was extended");
    auto square=resumed();auto menu=request("square","press",2);menu.button="square";
    check(square.accept(menu,10,20) && square.held=="square","square button rejected");
    check(ps1_button("square")==32768,"square physical mapping is incorrect");
    step(square,104,32768);step(square,105,32768);
    check(square.held.empty(),"square was not released at requested frame");
    step(square,106);step(square,107);square.captured("screenshots/square.png",10);
    check(square.receipt.ok && square.receipt.consumed,"square consumption evidence missing");
    auto square_pause=resumed();menu.id="square-pause";check(square_pause.accept(menu,10,20),"square pause setup");
    square_pause.tick(103,10,20,"manual",0);
    check(square_pause.paused && square_pause.held.empty(),"manual pause left square pressed");
    auto c=resumed();
    check(c.frame==103 && !c.paused,"resume did not preserve native frame");
    auto move=request("move","move",2);move.direction="down";
    check(c.accept(move,10,20) && c.held=="down","direction not pressed");
    for(int n=0;n<20;++n) step(c,103,64);
    check(c.frame==103 && c.held=="down" && !c.capture_pending,"SDL callbacks counted as frames");
    step(c,104,64);check(c.held=="down","released before two executed frames");
    step(c,105,64);check(c.held.empty() && !c.capture_pending,"held beyond requested frames");
    step(c,106);step(c,107);check(c.capture_pending,"missing post-release capture");
    c.captured("screenshots/move.png",10);check(c.receipt.ok && c.receipt.consumed && c.receipt.frame==107,"receipt lacks consumption/frame");
    c.acknowledge();check(!c.accept(move,10,20),"duplicate command replayed");
    auto waiting=request("wait","wait");check(c.accept(waiting,10,20),"wait rejected");
    c.tick(107,10,20,"new-pause",0);check(c.paused && c.held.empty() && !c.receipt.ok,"manual pause failed");
    c.acknowledge();check(!c.accept(request("stale","resume"),10,20),"old pause generation resumed");
    auto a=resumed();auto old=request("old","move");old.session="old";old.direction="up";
    check(!a.accept(old,10,20) && !a.paused,"foreign session affected current control");
    auto down=request("lease","move",120);down.direction="down";check(a.accept(down,10,20),"lease setup rejected");
    a.tick(103,21,20,"",64);check(a.paused && a.held.empty() && a.receipt.error=="lease_expired","lease failed while frames paused");
    auto missed=resumed();check(missed.accept(down,10,20),"missed setup");step(missed,105,64);
    check(missed.paused && missed.receipt.error=="frame_boundary_missed","missed frame silently accepted");
    auto absent=resumed();check(absent.accept(down,10,20),"absent setup");
    for(unsigned f=104;f<=225 && absent.active;++f) step(absent,f);
    check(absent.paused && absent.receipt.error=="input_not_observed","unconsumed input reported success");
    auto rewind=resumed();step(rewind,2);check(rewind.paused && rewind.error=="native_frame_rewound","save-state frame rewind ignored");
    auto snap=resumed();check(snap.accept(request("shot","wait"),10,20),"shot setup");
    step(snap,104);step(snap,105);step(snap,106);snap.fail("capture_failed");
    check(snap.paused && !snap.receipt.ok && snap.held.empty(),"capture failure did not stop control");
    auto transition=resumed();check(transition.accept(down,10,20),"transition setup");
    for(unsigned f=104;f<=223;++f) step(transition,f,64);
    step(transition,224);step(transition,225);check(transition.capture_pending,"transition capture setup");
    transition.captured("",10);
    check(transition.active && !transition.capture_pending && !transition.receipt.ready && transition.held.empty(),
          "blank transition completed the action instead of observing with released input");
    check(!transition.accept(request("premature","confirm"),10,20),"blank transition accepted another input");
    for(unsigned f=226;f<=255;++f) step(transition,f);
    check(transition.capture_pending,"transition did not advance native observation frames");
    transition.captured("screenshots/transition.png",10);
    check(transition.receipt.ok && transition.receipt.consumed && transition.receipt.frame==255 && transition.held.empty(),
          "transition lost consumption, frame evidence or released input");
    auto persistent=resumed();check(persistent.accept(request("black","wait"),10,20),"persistent setup");
    step(persistent,104);step(persistent,105);step(persistent,106);persistent.captured("",10);
    for(unsigned f=107;f<=136;++f) persistent.tick(f,12.9,20,"",0);
    check(persistent.capture_pending,"persistent transition never returned for capture");persistent.captured("",12.9);
    persistent.tick(136,13.01,20,"",0);
    check(persistent.paused && !persistent.active && persistent.receipt.error=="screenshot_timeout" && persistent.held.empty(),
          "blank retries extended the original screenshot deadline");
    auto interrupted=resumed();check(interrupted.accept(request("transition-pause","wait"),10,20),"pause setup");
    step(interrupted,104);step(interrupted,105);step(interrupted,106);interrupted.captured("",10);
    interrupted.tick(106,10,20,"new-pause",0);
    check(interrupted.paused && interrupted.receipt.error=="manual_pause" && interrupted.held.empty(),"pause ignored while observing transition");
    auto expired=resumed();check(expired.accept(request("transition-lease","wait"),10,20),"transition lease setup");
    step(expired,104);step(expired,105);step(expired,106);expired.captured("",10);expired.tick(106,10.1,10.05,"",0);
    check(expired.paused && expired.receipt.error=="lease_expired" && expired.held.empty(),"lease ignored while observing transition");
    Core slow_capture("current",100);
    check(slow_capture.accept(request("slow-capture","resume"),10,20),"slow capture setup");
    slow_capture.tick(101,10.01,20,"",0);slow_capture.tick(102,10.02,20,"",0);slow_capture.tick(103,10.03,20,"",0);
    check(slow_capture.capture_pending,"slow capture never requested an image");
    slow_capture.tick(103,11.20,20,"",0);
    check(slow_capture.active,"waiting for an image was counted as execution stall");
    slow_capture.captured("",11.20);slow_capture.tick(103,11.21,20,"",0);
    check(slow_capture.active && !slow_capture.receipt.ready && slow_capture.held.empty(),
          "slow blank capture was counted as native execution stall before the next step");
    auto stalled=slow_capture;
    stalled.tick(103,12.21,20,"",0);
    check(stalled.paused && stalled.receipt.error=="native_frame_stalled" && stalled.held.empty(),
          "real execution stall after a slow image no longer stops control");
    auto delayed_pause=slow_capture;delayed_pause.tick(103,11.21,20,"new-pause",0);
    check(delayed_pause.paused && delayed_pause.receipt.error=="manual_pause" && delayed_pause.held.empty(),
          "slow image disabled manual pause");
    auto delayed_expiry=slow_capture;delayed_expiry.tick(103,11.21,11.205,"",0);
    check(delayed_expiry.paused && delayed_expiry.receipt.error=="lease_expired" && delayed_expiry.held.empty(),
          "slow image extended the lease");
    for(unsigned f=104;f<=133;++f) slow_capture.tick(f,11.22+(f-104)*.01,20,"",0);
    check(slow_capture.capture_pending,"slow capture never returned to frame observation");
    auto late_blank=slow_capture;late_blank.captured("",13.04);
    check(late_blank.paused && late_blank.receipt.error=="screenshot_timeout",
          "slow blank image extended the first capture deadline");
    auto late_image=slow_capture;late_image.captured("screenshots/late.png",13.04);
    check(late_image.paused && late_image.receipt.error=="screenshot_timeout",
          "late usable image bypassed the first capture deadline");
    slow_capture.captured("screenshots/slow.png",12.7);
    check(slow_capture.receipt.ok && slow_capture.receipt.frame==133 && slow_capture.receipt.consumed,
          "slow capture lost successful native frame evidence");
    auto invalid=resumed();auto click=request("bad","click");
    check(!invalid.accept(click,10,20) && invalid.paused,"unsupported pointer accepted");
    std::cout<<"DuckStation core: native frames, exact holds, consumption, pause, lease, sessions, replay, rewind, capture passed\n";
}
