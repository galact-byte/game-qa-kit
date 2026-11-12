#pragma once
#include "CapturePixels.hpp"
namespace native_bridge {
std::string capture(const std::string& id) {
    HWND capture_window=native_bridge::window.load();
    if(!capture_window || IsIconic(capture_window)) throw std::runtime_error("capture_window_unavailable");
    struct DpiScope {
        DPI_AWARENESS_CONTEXT previous;
        explicit DpiScope(HWND target):previous(SetThreadDpiAwarenessContext(GetWindowDpiAwarenessContext(target))) {
            if(!previous) throw std::runtime_error("capture_dpi_context_failed");
        }
        ~DpiScope() { SetThreadDpiAwarenessContext(previous); }
    } dpi(capture_window);
    RECT area{};
    if(!GetClientRect(capture_window,&area)) throw std::runtime_error("capture_window_unavailable");
    const int width=area.right,height=area.bottom;
    if(width<=0 || height<=0 || static_cast<uint64_t>(width)*height>16777216) throw std::runtime_error("capture_dimensions_invalid");
    HDC screen=GetDC(capture_window),memory=CreateCompatibleDC(screen);
    if(!screen || !memory) { if(memory) DeleteDC(memory);if(screen) ReleaseDC(capture_window,screen);throw std::runtime_error("capture_dc_failed"); }
    BITMAPINFO info{};info.bmiHeader.biSize=sizeof(BITMAPINFOHEADER);info.bmiHeader.biWidth=width;
    info.bmiHeader.biHeight=-height;info.bmiHeader.biPlanes=1;info.bmiHeader.biBitCount=32;info.bmiHeader.biCompression=BI_RGB;
    auto bitmap=CreateCompatibleBitmap(screen,width,height);
    if(!bitmap) { DeleteDC(memory);ReleaseDC(capture_window,screen);throw std::runtime_error("capture_bitmap_failed"); }
    auto old=SelectObject(memory,bitmap);
    const bool rendered=PrintWindow(capture_window,memory,3) || PrintWindow(capture_window,memory,1);
    SelectObject(memory,old);
    const int stride=width*4;std::vector<BYTE> pixels(static_cast<size_t>(stride)*height);
    const bool copied=rendered && GetDIBits(memory,bitmap,0,height,pixels.data(),&info,DIB_RGB_COLORS)==static_cast<int>(height);
    const bool usable=copied && native_input::usable_pixels(pixels.data(),width,height);
    std::string name="screenshots/"+id+".png";bool saved=false;
    if(copied) {
        Gdiplus::Bitmap image(width,height,stride,PixelFormat32bppRGB,pixels.data());
        const CLSID png={0x557cf406,0x1a04,0x11d3,{0x9a,0x73,0x00,0x00,0xf8,0x1e,0xf3,0x2e}};
        auto path=directory/(usable?name:"screenshots/rejected-"+id+".png");auto tmp=path;tmp+=L".tmp";
        saved=image.Save(tmp.c_str(),&png,nullptr)==Gdiplus::Ok &&
            MoveFileExW(tmp.c_str(),path.c_str(),MOVEFILE_REPLACE_EXISTING|MOVEFILE_WRITE_THROUGH)!=FALSE;
    }
    SelectObject(memory,old);DeleteObject(bitmap);DeleteDC(memory);ReleaseDC(capture_window,screen);
    if(!rendered || !copied) throw std::runtime_error("capture_failed");
    if(usable && !saved) throw std::runtime_error("capture_save_failed");
    return usable?name:"";
}
}
