// Exercise production window wrappers on an isolated test desktop.
#include <windows.h>
#include <atomic>
#include <cstdint>
#include <cstring>
#include <stdexcept>
#include <utility>
#include <iostream>
#include <string>
namespace native_bridge { inline void hook(void*,void*){} }
#include "Isolation.hpp"
static int failures=0;
static void check(bool value,const char* label){std::cout<<(value?"PASS ":"FAIL ")<<label<<'\n';if(!value)++failures;}
static bool above(HWND a,HWND b){for(auto w=GetTopWindow(nullptr);w;w=GetWindow(w,GW_HWNDNEXT)){if(w==a)return true;if(w==b)return false;}return false;}
int main(){
    auto desktop=CreateDesktopW((L"GameQaWindowTest-"+std::to_wstring(GetCurrentProcessId())).c_str(),nullptr,nullptr,0,GENERIC_ALL,nullptr);
    if(!desktop||!SetThreadDesktop(desktop))return 2;
    auto sentinel=CreateWindowExW(WS_EX_NOACTIVATE,L"STATIC",L"Witness",WS_POPUP,30,30,300,200,nullptr,nullptr,nullptr,nullptr);
    ShowWindow(sentinel,SW_SHOWNOACTIVATE);
    auto game=native_bridge::create_window_w(0,L"STATIC",L"Game",WS_POPUP,30,30,300,200,nullptr,nullptr,nullptr,nullptr);
    SetWindowPos(game,HWND_BOTTOM,0,0,0,0,SWP_NOMOVE|SWP_NOSIZE|SWP_NOACTIVATE);
    native_bridge::show_without_activation(game,SW_SHOW);
    check(above(sentinel,game),"show keeps witness above game");
    native_bridge::position_without_activation(game,HWND_TOPMOST,0,0,0,0,SWP_NOMOVE|SWP_NOSIZE);
    check(!(GetWindowLongPtrW(game,GWL_EXSTYLE)&WS_EX_TOPMOST),"position cannot make game topmost");
    check(above(sentinel,game),"position keeps witness above game");
    auto popup=native_bridge::create_window_w(WS_EX_TOPMOST,L"STATIC",L"Popup",WS_POPUP|WS_VISIBLE,30,30,300,200,nullptr,nullptr,nullptr,nullptr);
    check(!(GetWindowLongPtrW(popup,GWL_EXSTYLE)&WS_EX_TOPMOST),"visible creation cannot make popup topmost");
    check(above(sentinel,popup),"visible popup creation keeps witness accessible");
    DestroyWindow(popup);DestroyWindow(game);DestroyWindow(sentinel);
    return failures?1:0;
}
