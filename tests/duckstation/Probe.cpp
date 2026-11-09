// Experimental SDL3 virtual joystick probe. Not a production control bridge.
#include <windows.h>
#include <atomic>
#include <cstdint>
#include <fstream>
#include <string>

// SDL 3.2+ ABI, verified against the installed 3.4.20 public header.
struct VirtualJoystickDesc {
    uint32_t version;
    uint16_t type, padding, vendor_id, product_id, naxes, nbuttons, nballs, nhats,
             ntouchpads, nsensors, padding2[2];
    uint32_t button_mask, axis_mask;
    const char* name;
    const void* touchpads;
    const void* sensors;
    void* userdata;
    void (*Update)(void*);
    void (*SetPlayerIndex)(void*, int);
    bool (*Rumble)(void*, uint16_t, uint16_t);
    bool (*RumbleTriggers)(void*, uint16_t, uint16_t);
    bool (*SetLED)(void*, uint8_t, uint8_t, uint8_t);
    bool (*SendEffect)(void*, const void*, int);
    bool (*SetSensorsEnabled)(void*, bool);
    void (*Cleanup)(void*);
};
static_assert(sizeof(VirtualJoystickDesc) == 136, "SDL virtual descriptor ABI mismatch");

static std::wstring root;
static std::atomic<void*> joystick{nullptr};
static bool (*set_button)(void*, int, bool) = nullptr;
static bool (*get_button)(void*, int) = nullptr;
static std::atomic<uint32_t> desired{0}, applied{0}, reported{0};
static std::atomic<uint64_t> expires{0}, callbacks{0}, consumed_seq{0}, requested_seq{0};

static void update(void*) {
    ++callbacks;
    void* device = joystick.load();
    if (!device) return;
    uint32_t mask = GetTickCount64() <= expires.load() ? desired.load() : 0;
    uint32_t old = applied.load();
    if (old != mask) {
        for (int button = 0; button < 21; ++button)
            if (((old ^ mask) & (1u << button)) != 0)
                set_button(device, button, (mask & (1u << button)) != 0);
        applied = mask;
    }
    consumed_seq = requested_seq.load();
}

static void state(const std::string& phase, uint32_t id = 0, int player = -1, const char* error = "") {
    std::wstring tmp = root + L"/probe-state.tmp";
    { std::ofstream out(tmp);
      out << "phase=" << phase << "\nid=" << id << "\nplayer=" << player
          << "\ncallbacks=" << callbacks.load() << "\napplied=" << applied.load()
          << "\nreported=" << reported.load()
          << "\nrequested=" << desired.load() << "\nconsumed_seq=" << consumed_seq.load()
          << "\nerror=" << error << "\n"; }
    MoveFileExW(tmp.c_str(), (root + L"/probe-state.txt").c_str(), MOVEFILE_REPLACE_EXISTING);
}

template<class T> static T symbol(HMODULE module, const char* name) {
    return reinterpret_cast<T>(GetProcAddress(module, name));
}

static DWORD WINAPI run(void*) {
    wchar_t buffer[32768];
    DWORD count = GetEnvironmentVariableW(L"GAMEQAKIT_PS1_DIR", buffer, 32768);
    if (!count || count >= 32768) return 1;
    root.assign(buffer, count);
    state("waiting-sdl");
    HMODULE sdl = nullptr;
    for (int i = 0; i < 1500 && !sdl; ++i) { sdl = GetModuleHandleW(L"SDL3.dll"); Sleep(10); }
    if (!sdl) { state("failed", 0, -1, "SDL3 module not loaded"); return 2; }
    auto initialized = symbol<uint32_t (*)(uint32_t)>(sdl, "SDL_WasInit");
    auto attach = symbol<uint32_t (*)(const VirtualJoystickDesc*)>(sdl, "SDL_AttachVirtualJoystick");
    auto open = symbol<void* (*)(uint32_t)>(sdl, "SDL_OpenJoystick");
    auto set_player = symbol<bool (*)(void*, int)>(sdl, "SDL_SetJoystickPlayerIndex");
    auto get_player = symbol<int (*)(void*)>(sdl, "SDL_GetJoystickPlayerIndex");
    auto error = symbol<const char* (*)()>(sdl, "SDL_GetError");
    set_button = symbol<bool (*)(void*, int, bool)>(sdl, "SDL_SetJoystickVirtualButton");
    get_button = symbol<bool (*)(void*, int)>(sdl, "SDL_GetJoystickButton");
    auto lock = symbol<void (*)()>(sdl, "SDL_LockJoysticks");
    auto unlock = symbol<void (*)()>(sdl, "SDL_UnlockJoysticks");
    if (!initialized || !attach || !open || !set_player || !get_player || !error || !set_button || !get_button || !lock || !unlock) {
        state("failed", 0, -1, "missing SDL symbol"); return 3;
    }
    for (int i = 0; i < 1500 && !(initialized(0x200) & 0x200); ++i) Sleep(10);
    VirtualJoystickDesc desc{};
    desc.version = sizeof(desc);
    desc.type = 1; // SDL_JOYSTICK_TYPE_GAMEPAD
    desc.nbuttons = 21;
    desc.button_mask = (1u << 21) - 1;
    desc.name = "GameQA Isolated Virtual Controller";
    desc.Update = update;
    lock();
    uint32_t id = attach(&desc);
    if (!id) { unlock(); state("failed", id, -1, error()); return 4; }
    void* device = open(id);
    if (!device) { unlock(); state("failed", id, -1, error()); return 5; }
    joystick = device;
    if (!set_player(device, 15)) { unlock(); state("failed", id, -1, error()); return 6; }
    unlock();
    while (true) {
        uint64_t seq = 0, deadline = 0;
        uint32_t mask = 0;
        {
            std::ifstream command(root + L"/probe-command.txt");
            if ((command >> seq >> deadline >> mask) && mask < (1u << 21)) {
                expires = deadline;
                desired = mask;
                requested_seq = seq;
            }
        }
        uint32_t observed = 0;
        for (int button = 0; button < 21; ++button)
            if (get_button(device, button)) observed |= 1u << button;
        reported = observed;
        state("running", id, get_player(device));
        Sleep(20);
    }
}

BOOL WINAPI DllMain(HINSTANCE module, DWORD reason, void*) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(module);
        HANDLE thread = CreateThread(nullptr, 0, run, nullptr, 0, nullptr);
        if (thread) CloseHandle(thread);
    }
    return TRUE;
}
