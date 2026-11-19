// SPDX-License-Identifier: GPL-3.0-or-later
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <cstdlib>
#include <memory>
#include <stdexcept>
#include "bridge_state.h"
#include "gameqa_input.h"
#include "player.h"
#include "scene.h"
#include "transition.h"

namespace GameQa {
namespace {
constexpr std::uint64_t magic = 0x4751414550320001ULL;
struct Shared {
    std::uint64_t magic, version, session, request_session, seq, kind, lease_until, expires;
    std::uint64_t ack_seq, ack_kind, frame, held, paused, pid, ready, reserved;
};
static_assert(sizeof(Shared) == 128, "Shared layout changed");

std::uint64_t EnvironmentNumber(const char* name) {
    auto text = std::getenv(name);
    if (!text || !*text || *text == '-') throw std::runtime_error("Missing managed bridge identity");
    char* end = nullptr;
    auto value = std::strtoull(text, &end, 10);
    if (!value || *end) throw std::runtime_error("Invalid managed bridge identity");
    return value;
}

class Mapping {
public:
    Mapping() : state(EnvironmentNumber("GAMEQAKIT_EASYRPG_TOKEN")) {
        mapping = reinterpret_cast<HANDLE>(EnvironmentNumber("GAMEQAKIT_EASYRPG_MAPPING"));
        mutex = reinterpret_cast<HANDLE>(EnvironmentNumber("GAMEQAKIT_EASYRPG_MUTEX"));
        shared = static_cast<Shared*>(MapViewOfFile(mapping, FILE_MAP_ALL_ACCESS, 0, 0, sizeof(Shared)));
        if (!shared || shared->magic != magic || shared->version != 1 ||
                shared->session != EnvironmentNumber("GAMEQAKIT_EASYRPG_TOKEN")) {
            throw std::runtime_error("Managed bridge layout or identity mismatch");
        }
    }
    ~Mapping() {
        if (shared) UnmapViewOfFile(shared);
        if (mapping) CloseHandle(mapping);
        if (mutex) CloseHandle(mutex);
    }
    bool Lock() {
        // Brief host snapshots must not turn normal mutex contention into a
        // sticky pause. The wait is bounded; timeout still releases all input.
        auto result = WaitForSingleObject(mutex, 10);
        if (result == WAIT_OBJECT_0) return true;
        if (result == WAIT_ABANDONED) ReleaseMutex(mutex);
        state.LoseContact();
        return false;
    }
    bool Poll() {
        if (!Lock()) return false;
        bool held = false;
        if (shared->magic == magic && shared->version == 1) {
            held = state.Poll(Command{shared->request_session, shared->seq,
                static_cast<Kind>(shared->kind), shared->lease_until, shared->expires}, GetTickCount64());
        } else state.LoseContact();
        ReleaseMutex(mutex);
        if (state.close) Player::exit_flag = true;
        return held;
    }
    void Commit() {
        state.Commit(Player::GetFrames());
        if (!Lock()) return;
        shared->ack_seq = state.ack_seq;
        shared->ack_kind = static_cast<std::uint64_t>(state.ack_kind);
        shared->frame = state.frame;
        shared->held = state.held;
        shared->paused = state.paused;
        shared->pid = GetCurrentProcessId();
        shared->ready = Scene::instance && Scene::instance->type == Scene::Map &&
                        !Transition::instance().IsActive() && !Transition::instance().IsErasedNotActive();
        ReleaseMutex(mutex);
    }
    State state;
private:
    HANDLE mapping = nullptr, mutex = nullptr;
    Shared* shared = nullptr;
};
std::unique_ptr<Mapping> bridge;

class ManagedSource final : public Input::Source {
public:
    using Input::Source::Source;
    void Update() override {
        pressed_buttons.reset();
        keystates.reset();
        if (bridge->Poll()) {
            pressed_buttons[Input::RIGHT] = true;
            keystates[Input::Keys::RIGHT] = true;
        }
        Record();
    }
    void UpdateSystem() override {
        // Physical refreshes must not consume a logical-frame command.
        pressed_buttons.reset();
        keystates.reset();
    }
};
}
std::unique_ptr<Input::Source> CreateManaged(const Game_ConfigInput& cfg, Input::DirectionMappingArray directions) {
    bridge = std::make_unique<Mapping>();
    return std::make_unique<ManagedSource>(cfg, std::move(directions));
}
void AfterScene() {
    if (bridge) bridge->Commit();
}
}
