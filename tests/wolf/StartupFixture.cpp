// Use the production loop, capture and shared-memory bridge in an owned test window.
#include "Bridge.cpp"
#include <thread>
#include <iostream>
std::wstring fixture_mode;
LRESULT CALLBACK fixture_window(HWND window,UINT message,WPARAM wp,LPARAM lp) {
    if(message==WM_PRINT || message==WM_PRINTCLIENT || message==WM_PAINT) {
        if(fixture_mode==L"blocked-capture" && completed.load()>0) Sleep(10000);
        PAINTSTRUCT paint{};
        HDC dc=message==WM_PAINT?BeginPaint(window,&paint):reinterpret_cast<HDC>(wp);
        RECT area{};GetClientRect(window,&area);
        FillRect(dc,&area,static_cast<HBRUSH>(GetStockObject(BLACK_BRUSH)));
        if(completed.load()>=2 && fixture_mode!=L"blank") {
            RECT marker{120,100,420,300};auto brush=CreateSolidBrush(RGB(20,150,210));
            FillRect(dc,&marker,brush);DeleteObject(brush);
        }
        if(message==WM_PAINT) EndPaint(window,&paint);
        return 0;
    }
    return DefWindowProcW(window,message,wp,lp);
}
HRESULT WINAPI empty_keys(void*,DWORD,void*) {return S_OK;}
void production_cycle() {
    unsigned audits=0;
#include "BridgeCycle.inc"
}
int wmain(int argc,wchar_t** argv) {
    if(argc!=5) return 2;
    directory=argv[1];session=utf8(argv[2]);fixture_mode=argv[3];
    wchar_t path[32768];GetModuleFileNameW(nullptr,path,32768);executable=path;executable_utf8=utf8(path);
    fs::create_directories(directory/L"results");fs::create_directories(directory/L"screenshots");
    core.reset(new gameqa::Core(session,std::wstring(argv[4])==L"enabled"));
    realtime.reset(new wolf::Realtime(GetCurrentProcessId(),session));
    Gdiplus::GdiplusStartupInput startup;
    if(Gdiplus::GdiplusStartup(&imaging,&startup,nullptr)!=Gdiplus::Ok) return 3;
    SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    WNDCLASSW wc{};wc.lpfnWndProc=fixture_window;wc.hInstance=GetModuleHandleW(nullptr);wc.lpszClassName=L"WolfStartupFixture";
    RegisterClassW(&wc);
    RECT size{0,0,640,480};AdjustWindowRect(&size,WS_OVERLAPPEDWINDOW,FALSE);
    auto window=CreateWindowW(wc.lpszClassName,L"Wolf startup test",WS_OVERLAPPEDWINDOW|WS_VISIBLE,
        0,0,size.right-size.left,size.bottom-size.top,nullptr,nullptr,wc.hInstance,nullptr);
    if(!window) return 4;
    game_thread=GetCurrentThreadId();ready=true;keyboard=reinterpret_cast<void*>(1);original_get_state=empty_keys;
    std::thread controller([] {for(;;) {control_cycle();Sleep(2);}});controller.detach();
    std::thread publisher([] {for(;;) {production_cycle();Sleep(10);}});publisher.detach();
    for(;;) {
        before_frame();
        if(fixture_mode==L"blocked-loop") Sleep(10000);
        if(fixture_mode==L"exit") ExitProcess(7);
        unsigned char buffer[256];get_state(keyboard,256,buffer);
        for(auto value:buffer) if(value) ExitProcess(8);
        if(fixture_mode==L"guard") ++wolf_guard::denied;
        after_frame();
    }
}
