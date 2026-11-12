#pragma once
#include <array>
#include <cstddef>
#include <cstdint>
namespace native_input {
struct KeyEvent { unsigned code=0;bool down=false;uint64_t action=0;uint32_t sequence=0; };
class DeviceQueue {
    std::array<KeyEvent,128> events_{};
    std::array<bool,256> state_{};
    size_t first_=0,size_=0;
    uint32_t sequence_=0;
    void append(unsigned code,bool down,uint64_t action){
        events_[(first_+size_)%events_.size()]={code,down,action,++sequence_};++size_;
    }
public:
    size_t size()const{return size_;}
    bool state(unsigned code)const{return code<state_.size()&&state_[code];}
    bool set(unsigned code,bool down,uint64_t action){
        if(code>=state_.size())return false;
        if(state_[code]==down)return true;
        if(size_==events_.size()){release_all();return false;}
        state_[code]=down;append(code,down,action);return true;
    }
    size_t read(KeyEvent* out,size_t capacity,bool peek){
        const auto count=capacity<size_?capacity:size_;
        for(size_t i=0;i<count;++i)out[i]=events_[(first_+i)%events_.size()];
        if(!peek){first_=(first_+count)%events_.size();size_-=count;}
        return count;
    }
    void release_all(){
        // Preserve pending releases across repeated pauses; discard every pending press.
        std::array<bool,256> releases=state_;
        for(size_t i=0;i<size_;++i){const auto& event=events_[(first_+i)%events_.size()];if(!event.down)releases[event.code]=true;}
        first_=size_=0;
        state_.fill(false);
        for(unsigned code=0;code<releases.size();++code)if(releases[code]){
            if(size_<events_.size())append(code,false,0);
        }
    }
};
struct ReleaseTracker {
    std::array<bool,256> observed{};
    uint64_t deadline=0;
    void record(unsigned code,bool down){if(code<observed.size())observed[code]=down;}
    bool expired(unsigned held_code,uint64_t now){
        bool pending=false;
        for(unsigned code=0;code<observed.size();++code)if(observed[code]&&code!=held_code){pending=true;break;}
        if(!pending){deadline=0;return false;}
        if(!deadline)deadline=now+1000;
        return now>=deadline;
    }
};
struct DeviceSlot { uintptr_t identity=0;bool mouse=false;DeviceQueue queue; };
class DeviceSlots {
    std::array<DeviceSlot,16> slots_{};
public:
    DeviceSlot* find(uintptr_t identity){for(auto& slot:slots_)if(slot.identity==identity&&identity)return &slot;return nullptr;}
    DeviceSlot* add(uintptr_t identity){
        if(!identity)return nullptr;
        if(auto slot=find(identity))return slot;
        for(auto& slot:slots_)if(!slot.identity){slot=DeviceSlot{};slot.identity=identity;return &slot;}
        return nullptr;
    }
    void remove(uintptr_t identity){if(auto slot=find(identity))*slot=DeviceSlot{};}
    size_t count()const{size_t total=0;for(const auto& slot:slots_)if(slot.identity)++total;return total;}
    template<class Callback>void each(Callback callback){for(auto& slot:slots_)if(slot.identity)callback(slot);}
};
}
