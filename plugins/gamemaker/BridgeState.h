#pragma once
#include <cstdint>
#include <cstring>

namespace gameqa_gm {
#pragma pack(push, 1)
struct Binding { char magic[8], token[32]; std::uint32_t pid, version; };
struct Command {
    char magic[8], token[32];
    std::uint32_t pid, seq, kind, reserved;
    std::uint64_t lease, hold;
};
struct Consumption {
    char magic[8], token[32];
    std::uint32_t pid, sample;
    std::uint64_t clock;
    std::uint32_t command, ack, kind, paused, held, reason;
};
#pragma pack(pop)
static_assert(sizeof(Binding)==48 && sizeof(Command)==72 && sizeof(Consumption)==80,
              "GameMaker protocol layout changed");

class State {
public:
    explicit State(const Binding& identity) : binding(identity) {}
    Binding binding;
    Command command = {};
    std::uint32_t sample=0, ack=0, ack_kind=0, reason=0;
    std::uint64_t clock=0;
    bool paused=true, held=false, closing=false;

    void fault(std::uint32_t why) { paused=true; held=false; reason=why; }

    void tick(const Command* input, std::uint64_t now) {
        held=false;
        if (now<clock) { fault(5); return; }
        clock=now;
        if (input) {
            const Command& c=*input;
            const bool newer=c.seq>command.seq;
            if (std::memcmp(c.magic,"GQKGM02",8) || std::memcmp(c.token,binding.token,32)
                || c.pid!=binding.pid || !c.seq || c.seq<command.seq || c.kind<1 || c.kind>5
                || c.reserved || (c.lease>now && c.lease-now>1500)
                || (c.kind!=2 && c.hold)
                || (!newer && (c.kind!=command.kind || c.hold!=command.hold))
                || (newer && c.kind==2 && (paused || c.hold<=now || c.hold-now>1000))) {
                fault(1); return;
            }
            command=c;
            if (newer && c.kind==1) { paused=false; reason=0; }
            if (newer && (c.kind==3 || c.kind==4 || c.kind==5)) reason=0;
            if (c.kind==4 || c.kind==5) paused=true;
            if (c.kind==5) closing=true;
        }
        if (!command.seq) return;
        if (command.lease<=now) { fault(2); return; }
        if (!paused && command.kind==2) {
            held=command.hold>now;
            if (!held) reason=3;
        }
    }

    void after(bool consumed) {
        // The caller passes the value produced by normal GML keyboard_check,
        // after the unmodified Step body has used it for movement.
        if (consumed!=held) { fault(4); return; }
        ack=command.seq; ack_kind=command.kind;
    }

    Consumption snapshot() {
        if (sample==UINT32_MAX) { fault(1); return {}; }
        Consumption result={};
        std::memcpy(result.magic,"GQSGM02",8); std::memcpy(result.token,binding.token,32);
        result.pid=binding.pid; result.sample=++sample; result.clock=clock;
        result.command=command.seq; result.ack=ack; result.kind=ack_kind;
        result.paused=paused; result.held=held; result.reason=reason;
        return result;
    }
};
}
