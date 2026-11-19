#include <cassert>
#include <cstring>
#include <iostream>
#include "BridgeState.h"

using namespace gameqa_gm;

int main() {
    Binding binding = {};
    std::memcpy(binding.magic, "GQBGM02", 8);
    std::memset(binding.token, 'a', 32);
    binding.pid = 123; binding.version = 2;
    auto command = [&](unsigned seq, unsigned kind, unsigned long long lease, unsigned long long hold=0) {
        Command c = {};
        std::memcpy(c.magic, "GQKGM02", 8); std::memcpy(c.token, binding.token, 32);
        c.pid = binding.pid; c.seq = seq; c.kind = kind; c.lease = lease; c.hold = hold;
        return c;
    };
    State state(binding);
    state.tick(nullptr, 1000); state.after(false);
    assert(state.paused && !state.held && !state.ack);
    auto resume = command(1,1,2500);
    state.tick(&resume,1000); assert(state.ack==0); state.after(false);
    assert(!state.paused && state.ack==1 && state.ack_kind==1);
    auto down = command(2,2,2500,1200);
    state.tick(&down,1000); assert(state.held && state.ack==1); state.after(true);
    assert(state.ack==2 && state.ack_kind==2);
    down.lease=2600; state.tick(&down,1100); state.after(true);
    assert(state.command.hold==1200);
    state.tick(nullptr,1200); state.after(false);
    assert(!state.held && !state.paused && state.reason==3);
    auto up=command(3,3,2700);
    state.tick(&up,1200); state.after(false); assert(state.ack_kind==3);
    auto pause=command(4,4,2700);
    state.tick(&pause,1200); state.after(false); assert(state.paused);
    auto illegal=command(5,2,2700,1300);
    state.tick(&illegal,1200); state.after(false); assert(state.paused && state.reason==1);
    resume=command(6,1,2700); state.tick(&resume,1200); state.after(false);
    down=command(7,2,2700,2200); state.tick(&down,1200); state.after(true);
    assert(state.held);
    state.tick(nullptr,2700); state.after(false); assert(state.paused && !state.held && state.reason==2);
    down.lease=4200; state.tick(&down,2700); state.after(false); assert(state.paused);
    resume=command(8,1,4200); state.tick(&resume,2700); state.after(false); assert(!state.paused);
    resume=command(9,1,4200); state.tick(&resume,2699); state.after(false);
    assert(state.paused && state.reason==5);

    unsigned mutations=0;
    for (unsigned which=0;which<9;++which) {
        State fresh(binding); auto r=command(1,1,2500);
        fresh.tick(&r,1000); fresh.after(false);
        auto c=command(2,2,2500,1200);
        switch(which) {
            case 0: c.token[0]='b'; break;
            case 1: c.pid++; break;
            case 2: c.magic[0]='x'; break;
            case 3: c.seq=0; break;
            case 4: c.kind=6; break;
            case 5: c.reserved=1; break;
            case 6: c.lease=2501; break;
            case 7: c.hold=2001; break;
            case 8: c.hold=1000; break;
        }
        fresh.tick(&c,1000); fresh.after(false);
        assert(fresh.paused && !fresh.held && fresh.reason==1); ++mutations;
    }
    State replay(binding); resume=command(1,1,2500);
    replay.tick(&resume,1000); replay.after(false);
    down=command(2,2,2500,1200); replay.tick(&down,1000); replay.after(true);
    auto altered=down; altered.hold=1300;
    replay.tick(&altered,1050); replay.after(false); assert(replay.paused && replay.reason==1);
    State mismatch(binding); mismatch.tick(&resume,1000); mismatch.after(false);
    mismatch.tick(&down,1000); mismatch.after(false); assert(mismatch.paused && !mismatch.held && mismatch.reason==4);
    State wide(binding); const auto now=1ULL<<40;
    resume=command(1,1,now+1500); wide.tick(&resume,now); wide.after(false);
    down=command(2,2,now+1500,now+1000); wide.tick(&down,now); wide.after(true); assert(wide.held);
    auto close=command(3,5,now+1500); wide.tick(&close,now+1); wide.after(false);
    assert(wide.closing && wide.paused && !wide.held);
    std::cout << "native state assertions passed; invalid mutations=" << mutations << "\n";
}
