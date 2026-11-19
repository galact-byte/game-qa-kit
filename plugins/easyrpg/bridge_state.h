// SPDX-License-Identifier: GPL-3.0-or-later
#ifndef GAMEQA_EASYRPG_BRIDGE_STATE_H
#define GAMEQA_EASYRPG_BRIDGE_STATE_H
#include <cstdint>

namespace GameQa {
enum class Kind : std::uint64_t { None, Resume, Down, Up, Pause, Close };
struct Command {
    std::uint64_t session, seq;
    Kind kind;
    std::uint64_t lease_until, expires;
};

class State {
public:
    explicit State(std::uint64_t session) : session_(session) {}

    bool Poll(const Command& command, std::uint64_t now) {
        if (command.session != session_ || command.lease_until <= now ||
                command.lease_until - now > 2000) {
            LoseContact();
            return false;
        }
        if (command.seq <= last_seen_) return held;
        last_seen_ = command.seq;
        if (command.expires <= now || command.expires - now > 1500) {
            LoseContact();
            return false;
        }
        switch (command.kind) {
        case Kind::Resume: paused = false; held = false; break;
        case Kind::Down:
            if (paused) return false;
            held = true;
            break;
        case Kind::Up: held = false; break;
        case Kind::Pause: LoseContact(); break;
        case Kind::Close: LoseContact(); close = true; break;
        default: LoseContact(); return false;
        }
        pending_ = command.seq;
        pending_kind_ = command.kind;
        return held;
    }

    // Called only after the normal input and scene update have returned.
    void Commit(std::uint64_t frame) {
        if (pending_) {
            ack_seq = pending_;
            ack_kind = pending_kind_;
            pending_ = 0;
        }
        this->frame = frame;
    }

    void LoseContact() { held = false; paused = true; pending_ = 0; }
    bool paused = true, held = false, close = false;
    std::uint64_t ack_seq = 0, frame = 0;
    Kind ack_kind = Kind::None;
private:
    std::uint64_t session_, last_seen_ = 0, pending_ = 0;
    Kind pending_kind_ = Kind::None;
};
}
#endif
