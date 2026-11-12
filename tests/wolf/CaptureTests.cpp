// Exercise the production capture function on real windows in an owned desktop.
#include <windows.h>
#include <objidl.h>
#include <gdiplus.h>
#include <algorithm>
#include <cstdint>
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>
namespace fs=std::filesystem;
fs::path directory;
DWORD game_thread=0;
std::wstring capture_mode;
BOOL fixture_print(HWND window,HDC dc,UINT flags) {
    if(capture_mode==L"black" || capture_mode==L"white" ||
       (capture_mode==L"blank-full-content" && flags==3)) {
        RECT area{};GetClientRect(window,&area);
        PatBlt(dc,0,0,area.right,area.bottom,capture_mode==L"white"?WHITENESS:BLACKNESS);
        return TRUE;
    }
    return PrintWindow(window,dc,flags);
}
#define PrintWindow fixture_print
#include "BridgeCapture.inc"
#undef PrintWindow

LRESULT CALLBACK paint(HWND window,UINT message,WPARAM wparam,LPARAM lparam) {
    if(message==WM_PRINTCLIENT || message==WM_PRINT || message==WM_PAINT) {
        PAINTSTRUCT state{};
        auto dc=message==WM_PAINT?BeginPaint(window,&state):reinterpret_cast<HDC>(wparam);
        RECT area{};GetClientRect(window,&area);
        FillRect(dc,&area,static_cast<HBRUSH>(GetStockObject(GRAY_BRUSH)));
        RECT corner{area.right-20,area.bottom-20,area.right,area.bottom};
        auto brush=CreateSolidBrush(RGB(255,0,0));FillRect(dc,&corner,brush);DeleteObject(brush);
        if(message==WM_PAINT) EndPaint(window,&state);
        return 0;
    }
    return DefWindowProcW(window,message,wparam,lparam);
}
int wmain(int argc,wchar_t** argv) {
    if(argc!=4) return 2;
    capture_mode=argv[3];
    directory=argv[1];fs::create_directories(directory/L"screenshots");
    const bool aware=std::wstring(argv[2])==L"aware";
    const auto context=aware?DPI_AWARENESS_CONTEXT_SYSTEM_AWARE:DPI_AWARENESS_CONTEXT_UNAWARE;
    if(!SetThreadDpiAwarenessContext(context)) return 3;
    Gdiplus::GdiplusStartupInput startup;ULONG_PTR token=0;
    if(Gdiplus::GdiplusStartup(&token,&startup,nullptr)!=Gdiplus::Ok) return 4;
    WNDCLASSW cls{};cls.lpfnWndProc=paint;cls.hInstance=GetModuleHandleW(nullptr);cls.lpszClassName=L"WolfCaptureFixture";
    if(!RegisterClassW(&cls)) return 5;
    const int width=aware?1024:512,height=aware?576:384;
    RECT bounds{0,0,width,height};AdjustWindowRect(&bounds,WS_OVERLAPPEDWINDOW,FALSE);
    auto window=CreateWindowW(cls.lpszClassName,L"capture-fixture",WS_OVERLAPPEDWINDOW|WS_VISIBLE,
                              0,0,bounds.right-bounds.left,bounds.bottom-bounds.top,nullptr,nullptr,cls.hInstance,nullptr);
    if(!window) return 6;
    UpdateWindow(window);
    game_thread=GetCurrentThreadId();
    int result=1;
    std::thread worker([&] {
        SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_UNAWARE);
        const auto previous=GetThreadDpiAwarenessContext();
        try {
            const auto name=capture("full-frame");
            if(capture_mode==L"black" || capture_mode==L"white") {
                if(!name.empty() || fs::exists(directory/L"screenshots/full-frame.png"))
                    throw std::runtime_error("blank_capture_was_accepted");
            } else {
                if(name.empty()) throw std::runtime_error("empty_capture");
                Gdiplus::Bitmap image((directory/name).c_str());
                std::cout<<"capture "<<image.GetWidth()<<"x"<<image.GetHeight()<<" expected "<<width<<"x"<<height<<std::endl;
                if(image.GetWidth()!=static_cast<UINT>(width) || image.GetHeight()!=static_cast<UINT>(height))
                    throw std::runtime_error("capture_cropped_client_area");
                Gdiplus::Color corner;
                if(image.GetPixel(width-5,height-5,&corner)!=Gdiplus::Ok || corner.GetR()!=255 || corner.GetG()!=0 || corner.GetB()!=0)
                    throw std::runtime_error("bottom_right_marker_missing");
            }
            if(!AreDpiAwarenessContextsEqual(previous,GetThreadDpiAwarenessContext()))
                throw std::runtime_error("capture_changed_caller_dpi_context");
            result=0;
        } catch(const std::exception& error) { std::cerr<<error.what()<<std::endl; }
        PostThreadMessageW(game_thread,WM_QUIT,0,0);
    });
    MSG message{};while(GetMessageW(&message,nullptr,0,0)>0) {TranslateMessage(&message);DispatchMessageW(&message);}
    worker.join();DestroyWindow(window);Gdiplus::GdiplusShutdown(token);return result;
}
