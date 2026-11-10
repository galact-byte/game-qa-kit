#pragma once
#include <cmath>
#include <set>
#include <string>
namespace gameqa {
struct Command {
    int protocol=1,frames=1;
    long long after=0;
    std::string session,id,pause,action,direction,button;
};
struct Receipt {
    Command request;
    bool ready=false,ok=false,consumed=false;
    long long frame=0;
    std::string error,screenshot;
};
inline unsigned scan_code(const std::string& button) {
    if(button=="up") return 0xc8;if(button=="down") return 0xd0;
    if(button=="left") return 0xcb;if(button=="right") return 0xcd;
    if(button=="ok") return 0x1c;if(button=="cancel") return 0x01;
    if(button=="shift") return 0x2a;if(button=="pageup") return 0xc9;
    if(button=="pagedown") return 0xd1;return 0;
}
class Core {
    std::set<std::string> seen;
    int remaining=0,settle=0;
    bool consumed=false,observing=false;
    double last_progress=0,capture_started=0;
    Command command;
    static bool identifier(const std::string& s) {
        if(s.empty() || s.size()>64) return false;
        for(unsigned char c:s) if(!((c>='a' && c<='z') || (c>='A' && c<='Z') ||
                (c>='0' && c<='9') || c=='_' || c=='-')) return false;
        return true;
    }
public:
    std::string session,pause,error,held;
    long long frame=0;
    bool paused=true,active=false,capture_pending=false;
    Receipt receipt;
    explicit Core(std::string value):session(value) {}
    bool accept(const Command& c,double now,double expiry) {
        if(c.protocol!=1 || c.session!=session || !identifier(c.id) || seen.count(c.id) || active || receipt.ready) return false;
        seen.insert(c.id);command=c;active=true;consumed=false;
        const auto& a=c.action;
        bool valid=c.frames>=1 && c.frames<=120 && c.after>=0 && c.after<=frame &&
            (a=="resume" || a=="move" || a=="confirm" || a=="cancel" || a=="press" || a=="release" || a=="wait");
        if(a=="move") valid=valid && (c.direction=="up" || c.direction=="down" || c.direction=="left" || c.direction=="right");
        if(a=="press") valid=valid && scan_code(c.button)!=0;
        if(a=="release") valid=valid && (c.button.empty() || scan_code(c.button)!=0);
        if(!valid) { fail("invalid_command");return false; }
        if(c.pause!=pause || !std::isfinite(now) || !std::isfinite(expiry) || expiry<now) { fail("stale_command");return false; }
        if(a=="resume") { paused=false;error.clear(); }
        else if(paused) { fail("control_paused");return false; }
        held=a=="move"?c.direction:a=="confirm"?"ok":a=="cancel"?"cancel":a=="press"?c.button:"";
        remaining=c.frames;settle=2;capture_pending=false;consumed=held.empty();last_progress=now;observing=false;
        return true;
    }
    void observe(const std::string& button) {
        // DirectInput can be polled several times within one game-loop iteration.
        if(active && !paused && remaining>0 && !held.empty() && held==button) consumed=true;
    }
    void tick(unsigned native,double now,double expiry,const std::string& generation) {
        if(generation!=pause) { pause=generation;fail("manual_pause"); }
        if(!paused && (!std::isfinite(now) || !std::isfinite(expiry) || expiry<now)) fail("lease_expired");
        if(native<frame) { frame=native;fail("native_frame_rewound");return; }
        const auto advanced=static_cast<long long>(native)-frame;frame=native;
        if(!active) return;
        if(observing && now-capture_started>3.0) { fail("screenshot_timeout");return; }
        if(advanced>1) { fail("frame_boundary_missed");return; }
        if(!advanced) {
            if(!capture_pending && now-last_progress>1.0) fail("native_frame_stalled");
            return;
        }
        last_progress=now;
        if(capture_pending) { fail("capture_frame_changed");return; }
        if(remaining>0) { if(!--remaining) held.clear();return; }
        if(--settle>0) return;
        if(!consumed) { fail("input_not_observed");return; }
        capture_pending=true;
        if(!observing) { capture_started=now;observing=true; }
    }
    void captured(const std::string& path,double now) {
        if(!active || !capture_pending) return;
        if(!std::isfinite(now) || now-capture_started>3.0) { fail("screenshot_timeout");return; }
        // Encoding/capture time is not game execution; keep the first screenshot deadline.
        if(path.empty()) { last_progress=now;capture_pending=false;settle=30;return; }
        receipt.request=command;receipt.ready=true;receipt.ok=true;receipt.consumed=consumed;
        receipt.frame=frame;receipt.screenshot=path;held.clear();active=false;capture_pending=false;
    }
    void fail(const std::string& reason) {
        error=reason;paused=true;held.clear();capture_pending=false;
        if(active) {
            receipt=Receipt();receipt.request=command;receipt.ready=true;receipt.consumed=consumed;
            receipt.frame=frame;receipt.error=reason;
        }
        active=false;
    }
    const std::string& command_id() const { return command.id; }
    void acknowledge() { receipt=Receipt(); }
};
}
