#pragma once
#include <cmath>
#include <set>
#include <string>
namespace gameqa {
struct Command {
    int protocol=1, frames=1, x=0, y=0;
    long long after=0;
    std::string session,id,pause,action,direction,button;
};
struct Receipt {
    Command request;
    bool ready=false,ok=false,consumed=false;
    long long frame=0;
    std::string error,screenshot;
};
class Core {
    std::set<std::string> seen;
    int remaining=0,settle=0;
    bool consumed=false;
    static bool button(const std::string& s) {
        return s=="up" || s=="down" || s=="left" || s=="right" || s=="ok" ||
            s=="cancel" || s=="shift" || s=="pageup" || s=="pagedown";
    }
    static bool identifier(const std::string& s) {
        if(s.empty() || s.size()>64) return false;
        for(unsigned char c:s)
            if(!((c>='a' && c<='z') || (c>='A' && c<='Z') || (c>='0' && c<='9') || c=='_' || c=='-')) return false;
        return true;
    }
public:
    std::string session,pause,error,held;
    long long frame=0;
    bool paused=true,active=false;
    Command command;
    Receipt receipt;
    explicit Core(std::string value):session(value) {}
    void begin(double now,double expiry,const std::string& generation) {
        ++frame;
        if(generation!=pause) { pause=generation;fail("manual_pause"); }
        if(!paused && (!std::isfinite(now) || !std::isfinite(expiry) || expiry<now)) fail("lease_expired");
        if(active && remaining==0) held.clear();
    }
    bool accept(const Command& c,double now,double expiry) {
        if(c.protocol!=1 || c.session!=session || !identifier(c.id) || seen.count(c.id) || active || receipt.ready) return false;
        seen.insert(c.id);command=c;active=true;consumed=false;
        const auto& a=c.action;
        bool valid=c.frames>=1 && c.frames<=120 && c.after>=0 && c.after<=frame &&
            (a=="resume" || a=="move" || a=="confirm" || a=="cancel" || a=="press" || a=="release" || a=="wait" || a=="click");
        if(a=="move") valid=valid && (c.direction=="up" || c.direction=="down" || c.direction=="left" || c.direction=="right");
        if(a=="press") valid=valid && button(c.button);
        if(a=="release") valid=valid && (c.button.empty() || button(c.button));
        if(a=="click") valid=valid && c.x>=0 && c.y>=0;
        if(!valid) { fail("invalid_command");return false; }
        if(c.pause!=pause || !std::isfinite(now) || !std::isfinite(expiry) || expiry<now) { fail("stale_command");return false; }
        if(a=="resume") { paused=false;error.clear(); }
        else if(paused) { fail("control_paused");return false; }
        held=a=="move"?c.direction:a=="confirm"?"ok":a=="cancel"?"cancel":a=="press"?c.button:a=="click"?"mouse0":"";
        remaining=c.frames;settle=2;consumed=held.empty();
        return true;
    }
    void observe(const std::string& token) {
        if(active && !held.empty() && held==token) consumed=true;
    }
    bool end() {
        if(!active) return false;
        if(remaining>0) { --remaining;return false; }
        if(settle>0) --settle;
        if(settle) return false;
        if(!consumed) { fail("input_not_observed");return false; }
        return true;
    }
    void captured(const std::string& path) {
        if(!active) return;
        receipt.request=command;receipt.ready=true;receipt.ok=true;receipt.consumed=consumed;
        receipt.frame=frame;receipt.screenshot=path;held.clear();active=false;
    }
    void fail(const std::string& reason) {
        error=reason;paused=true;held.clear();
        if(active) {
            receipt=Receipt();receipt.request=command;receipt.ready=true;
            receipt.consumed=consumed;receipt.frame=frame;receipt.error=reason;
        }
        active=false;
    }
    void acknowledge() { receipt=Receipt(); }
};
}
