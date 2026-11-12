#pragma once
#include <cstdint>
#include <set>
#include <string>
namespace native_input {
// The caller serializes these memory-only transitions; no I/O or window callbacks run here.
struct Core {
    bool paused=true,capture_pending=false,active=false,press_consumed=false,release_consumed=false;
    std::string held,error,pause_id,id,action;
    uint64_t started=0,deadline=0,lease_expiry=0,last_query=0,capture_deadline=0,action_sequence=0;
    std::set<std::string> seen;
    bool keyed()const{return action=="move"||action=="confirm"||action=="cancel";}
    void fail(const std::string& reason){held.clear();paused=true;capture_pending=false;active=false;error=reason;}
    void pause(const std::string& generation){pause_id=generation;fail("manual_pause");}
    bool begin(const std::string& identifier,const std::string& operation,const std::string& button,
               uint32_t duration,uint64_t now,uint64_t expiry,const std::string& generation){
        if(active||identifier.empty()||seen.count(identifier)||seen.size()>=4096||generation!=pause_id||
           expiry<=now||expiry-now>1500)return false;
        if(operation!="resume"&&(paused||duration<1||duration>1000))return false;
        if(operation=="resume"){if(duration||!button.empty())return false;}
        else if(operation=="move"){
            if(button!="right"&&button!="left"&&button!="up"&&button!="down")return false;
        }else if(operation=="confirm"||operation=="cancel"){
            if(button!=(operation=="confirm"?"ok":"cancel"))return false;
        }else if(operation!="wait"||!button.empty())return false;
        ++action_sequence;seen.insert(identifier);id=identifier;action=operation;error.clear();paused=false;active=true;
        press_consumed=false;release_consumed=false;capture_pending=operation=="resume";
        started=now;deadline=now+duration;lease_expiry=expiry;last_query=now;capture_deadline=now+3000;
        held=keyed()?button:"";return true;
    }
    void query(uint64_t now,bool down){
        tick(now);
        last_query=now;
        if(active&&keyed()){
            if(!held.empty()&&down)press_consumed=true;
            if(held.empty()&&!down&&press_consumed&&!release_consumed){release_consumed=true;capture_pending=true;capture_deadline=now+3000;}
        }
    }
    void consume(uint64_t now,bool down,uint64_t event_generation){
        tick(now);last_query=now;
        if(event_generation==action_sequence)query(now,down);
    }
    void renew(uint64_t now,uint64_t expiry){
        if(paused)return;
        if(now>=lease_expiry){fail("lease_expired");return;}
        if(expiry>now&&expiry-now<=1500)lease_expiry=expiry;
    }
    void tick(uint64_t now){
        if(paused)return;
        if(now>=lease_expiry){fail("lease_expired");return;}
        if(!active)return;
        if(keyed()&&now-last_query>=1000){fail("input_not_queried");return;}
        if(!held.empty()&&now>=deadline)held.clear();
        if(action=="wait"&&now>=deadline&&!capture_pending){capture_pending=true;capture_deadline=now+3000;}
        if(keyed()&&now>=deadline+1000&&!release_consumed){fail("input_release_unconfirmed");return;}
        if(capture_pending&&now>=capture_deadline)fail("capture_timeout");
    }
    void finish(){active=false;capture_pending=false;held.clear();}
};
}
