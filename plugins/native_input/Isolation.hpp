#pragma once
namespace native_bridge {
inline std::atomic<HWND> window{nullptr};
inline WNDPROC original_window=nullptr;
inline LRESULT CALLBACK filtered_window(HWND hwnd,UINT message,WPARAM w,LPARAM l){
    if((message>=WM_KEYFIRST&&message<=WM_KEYLAST)||(message>=WM_MOUSEFIRST&&message<=WM_MOUSELAST)||message==WM_INPUT)return 0;
    if(message==WM_KILLFOCUS||(message==WM_ACTIVATEAPP&&!w)||(message==WM_ACTIVATE&&LOWORD(w)==WA_INACTIVE))return 0;
    return CallWindowProcW(original_window,hwnd,message,w,l);
}
inline HWND WINAPI focus_window(){return window.load();}
inline BOOL WINAPI no_foreground(HWND){return TRUE;}
inline HWND WINAPI no_focus(HWND){return window.load();}
inline BOOL WINAPI no_cursor_position(int,int){return TRUE;}
inline BOOL WINAPI virtual_cursor(LPPOINT point){if(!point)return FALSE;point->x=point->y=0;auto hwnd=window.load();if(hwnd)ClientToScreen(hwnd,point);return TRUE;}
inline BOOL WINAPI no_clip(const RECT*){return TRUE;}
inline int WINAPI visible_cursor(BOOL){return 1;}
inline BOOL WINAPI no_capture_release(){return TRUE;}
inline SHORT WINAPI no_key(int){return 0;}
inline BOOL WINAPI no_keyboard(PBYTE data){if(!data)return FALSE;std::memset(data,0,256);return TRUE;}
inline BOOL WINAPI no_raw(PCRAWINPUTDEVICE,UINT,UINT){return TRUE;}
inline BOOL WINAPI show_without_activation(HWND hwnd,int command){
    const auto original=reinterpret_cast<BOOL(WINAPI*)(HWND,int)>(GetProcAddress(GetModuleHandleW(L"user32.dll"),"ShowWindow"));
    return original(hwnd,command==SW_HIDE?SW_HIDE:SW_SHOWNOACTIVATE);
}
inline BOOL WINAPI position_without_activation(HWND hwnd,HWND after,int x,int y,int cx,int cy,UINT flags){
    const auto original=reinterpret_cast<BOOL(WINAPI*)(HWND,HWND,int,int,int,int,UINT)>(GetProcAddress(GetModuleHandleW(L"user32.dll"),"SetWindowPos"));
    return original(hwnd,after,x,y,cx,cy,flags|SWP_NOACTIVATE);
}
inline HWND WINAPI create_window_a(DWORD ex,LPCSTR cls,LPCSTR title,DWORD style,int x,int y,int width,int height,HWND parent,HMENU menu,HINSTANCE instance,LPVOID parameter){
    auto original=reinterpret_cast<decltype(&CreateWindowExA)>(GetProcAddress(GetModuleHandleW(L"user32.dll"),"CreateWindowExA"));
    return original(ex|WS_EX_NOACTIVATE,cls,title,style,x,y,width,height,parent,menu,instance,parameter);
}
inline HWND WINAPI create_window_w(DWORD ex,LPCWSTR cls,LPCWSTR title,DWORD style,int x,int y,int width,int height,HWND parent,HMENU menu,HINSTANCE instance,LPVOID parameter){
    auto original=reinterpret_cast<decltype(&CreateWindowExW)>(GetProcAddress(GetModuleHandleW(L"user32.dll"),"CreateWindowExW"));
    return original(ex|WS_EX_NOACTIVATE,cls,title,style,x,y,width,height,parent,menu,instance,parameter);
}
inline void install_isolation(){
    auto module=GetModuleHandleW(L"user32.dll");
    const std::pair<const char*,void*> functions[]={
        {"GetForegroundWindow",reinterpret_cast<void*>(focus_window)},{"GetActiveWindow",reinterpret_cast<void*>(focus_window)},
        {"GetFocus",reinterpret_cast<void*>(focus_window)},{"SetForegroundWindow",reinterpret_cast<void*>(no_foreground)},
        {"SetActiveWindow",reinterpret_cast<void*>(no_focus)},{"SetFocus",reinterpret_cast<void*>(no_focus)},
        {"SetCapture",reinterpret_cast<void*>(no_focus)},{"GetCapture",reinterpret_cast<void*>(focus_window)},
        {"ReleaseCapture",reinterpret_cast<void*>(no_capture_release)},{"SetCursorPos",reinterpret_cast<void*>(no_cursor_position)},
        {"GetCursorPos",reinterpret_cast<void*>(virtual_cursor)},{"ClipCursor",reinterpret_cast<void*>(no_clip)},
        {"ShowCursor",reinterpret_cast<void*>(visible_cursor)},{"GetKeyState",reinterpret_cast<void*>(no_key)},
        {"GetAsyncKeyState",reinterpret_cast<void*>(no_key)},{"GetKeyboardState",reinterpret_cast<void*>(no_keyboard)},
        {"RegisterRawInputDevices",reinterpret_cast<void*>(no_raw)},{"ShowWindow",reinterpret_cast<void*>(show_without_activation)},
        {"SetWindowPos",reinterpret_cast<void*>(position_without_activation)},{"CreateWindowExA",reinterpret_cast<void*>(create_window_a)},
        {"CreateWindowExW",reinterpret_cast<void*>(create_window_w)}};
    for(const auto& function:functions)hook(reinterpret_cast<void*>(GetProcAddress(module,function.first)),function.second);
}
inline BOOL CALLBACK collect_window(HWND hwnd,LPARAM data){
    DWORD pid=0;GetWindowThreadProcessId(hwnd,&pid);RECT rect{},previous{};auto selected=reinterpret_cast<HWND*>(data);
    if(pid==GetCurrentProcessId()&&IsWindowVisible(hwnd)&&GetClientRect(hwnd,&rect)&&rect.right>100&&rect.bottom>100){
        if(*selected)GetClientRect(*selected,&previous);
        if(static_cast<int64_t>(rect.right)*rect.bottom>static_cast<int64_t>(previous.right)*previous.bottom)*selected=hwnd;
    }
    return TRUE;
}
inline bool bind_window(){
    if(window.load())return IsWindow(window.load())!=FALSE;
    HWND selected=nullptr;EnumWindows(collect_window,reinterpret_cast<LPARAM>(&selected));if(!selected)return false;
    SetLastError(0);auto previous=SetWindowLongPtrW(selected,GWLP_WNDPROC,reinterpret_cast<LONG_PTR>(filtered_window));
    if(!previous)throw std::runtime_error("window_subclass_failed");
    original_window=reinterpret_cast<WNDPROC>(previous);window=selected;return true;
}
}
