#define DIRECTINPUT_VERSION 0x0800
#include <windows.h>
#include <dinput.h>
#include <wincrypt.h>
#include <filesystem>
#include <fstream>
#include <string>
namespace fs=std::filesystem;
IDirectInput8W* input=nullptr;IDirectInputDevice8W* keyboard=nullptr;
fs::path root;int position=100;unsigned presses=0,releases=0,paints=0,prints=0,last_code=0;bool down=false;ULONGLONG visible_after=0;
std::wstring env(const wchar_t* key){wchar_t value[32768]{};GetEnvironmentVariableW(key,value,32768);return value;}
void observe(){
    std::ofstream out(root/L"target-state.tmp");out<<"{\"position\":"<<position<<",\"paints\":"<<paints<<",\"prints\":"<<prints<<",\"presses\":"<<presses<<",\"releases\":"<<releases<<",\"last_scan_code\":"<<last_code<<",\"down\":"<<(down?"true":"false")<<"}";out.close();
    MoveFileExW((root/L"target-state.tmp").c_str(),(root/L"target-state.json").c_str(),MOVEFILE_REPLACE_EXISTING);
}
LRESULT CALLBACK window_proc(HWND window,UINT message,WPARAM w,LPARAM l){
    if(message==WM_TIMER){
        if(fs::exists(root/L"stop-poll"))return 0;
        if(fs::exists(root/L"stall-window")){Sleep(6000);return 0;}
        DIDEVICEOBJECTDATA events[32]{};DWORD count=32;
        if(keyboard&&SUCCEEDED(keyboard->GetDeviceData(sizeof(events[0]),events,&count,0)))for(DWORD i=0;i<count;++i){
            if(events[i].dwData&0x80){last_code=events[i].dwOfs;++presses;down=true;position+=events[i].dwOfs==DIK_LEFT?-20:20;}
            else{++releases;down=false;}
        }
        observe();InvalidateRect(window,nullptr,FALSE);return 0;
    }
    if(message==WM_PAINT||message==WM_PRINTCLIENT){
        if(message==WM_PAINT)++paints;else ++prints;
        PAINTSTRUCT paint{};auto dc=message==WM_PAINT?BeginPaint(window,&paint):reinterpret_cast<HDC>(w);
        const bool blank=fs::exists(root/L"black-frame")||GetTickCount64()<visible_after;
        RECT area{};GetClientRect(window,&area);auto background=CreateSolidBrush(blank?RGB(0,0,0):RGB(28,48,70));FillRect(dc,&area,background);DeleteObject(background);
        if(blank){if(message==WM_PAINT)EndPaint(window,&paint);return 0;}
        RECT block{position,100,position+48,148};auto brush=CreateSolidBrush(RGB(240,176,40));FillRect(dc,&block,brush);DeleteObject(brush);
        SetTextColor(dc,RGB(245,245,245));SetBkMode(dc,TRANSPARENT);const wchar_t* label=L"Native input owned fixture";
        TextOutW(dc,24,30,label,lstrlenW(label));if(message==WM_PAINT)EndPaint(window,&paint);return 0;
    }
    if(message==WM_CLOSE){DestroyWindow(window);return 0;}
    if(message==WM_DESTROY){PostQuitMessage(0);return 0;}
    return DefWindowProcW(window,message,w,l);
}
int WINAPI wWinMain(HINSTANCE instance,HINSTANCE,LPWSTR,int){
    root=env(L"GAMEQAKIT_NATIVE_ROOT");
    const auto outside=env(L"GAMEQAKIT_DENIED_FILE");
    auto file=CreateFileW(outside.c_str(),GENERIC_WRITE,0,nullptr,CREATE_NEW,0,nullptr);
    const bool file_denied=file==INVALID_HANDLE_VALUE;if(!file_denied)CloseHandle(file);
    HKEY key=nullptr;const auto reg=RegCreateKeyExW(HKEY_CURRENT_USER,L"Software\\GameQaNativeDisposableProbe",0,nullptr,0,KEY_WRITE,nullptr,&key,nullptr);
    if(reg==ERROR_SUCCESS)RegCloseKey(key);
    HCRYPTPROV provider=0;HCRYPTHASH hash=0;
    bool crypto=CryptAcquireContextW(&provider,nullptr,nullptr,PROV_RSA_AES,CRYPT_VERIFYCONTEXT)!=FALSE;
    if(crypto)crypto=CryptCreateHash(provider,CALG_SHA_256,0,0,&hash)!=FALSE;
    const BYTE data[]={1,2,3};if(crypto)crypto=CryptHashData(hash,data,sizeof(data),0)!=FALSE;
    if(hash)CryptDestroyHash(hash);if(provider)CryptReleaseContext(provider,0);
    {std::ofstream out(root/L"target-guard.json");out<<"{\"file_denied\":"<<(file_denied?"true":"false")<<",\"registry_denied\":"<<(reg==ERROR_ACCESS_DENIED?"true":"false")<<",\"crypto_ok\":"<<(crypto?"true":"false")<<"}";}
    if(!file_denied||reg!=ERROR_ACCESS_DENIED||!crypto)return 3;
    WNDCLASSW cls{};cls.hInstance=instance;cls.lpfnWndProc=window_proc;cls.lpszClassName=L"GameQaNativeFixture";RegisterClassW(&cls);
    auto window=CreateWindowExW(WS_EX_NOACTIVATE,cls.lpszClassName,L"GameQA Native Fixture",WS_OVERLAPPEDWINDOW,30,30,480,320,nullptr,nullptr,instance,nullptr);
    if(!window)return 4;
    if(FAILED(DirectInput8Create(instance,DIRECTINPUT_VERSION,IID_IDirectInput8W,reinterpret_cast<void**>(&input),nullptr))||
       FAILED(input->CreateDevice(GUID_SysKeyboard,&keyboard,nullptr))||FAILED(keyboard->SetDataFormat(&c_dfDIKeyboard)))return 5;
    DIPROPDWORD buffer{};buffer.diph.dwSize=sizeof(buffer);buffer.diph.dwHeaderSize=sizeof(buffer.diph);buffer.diph.dwHow=DIPH_DEVICE;buffer.dwData=32;
    if(FAILED(keyboard->SetProperty(DIPROP_BUFFERSIZE,&buffer.diph))||FAILED(keyboard->SetCooperativeLevel(window,DISCL_BACKGROUND|DISCL_NONEXCLUSIVE))||FAILED(keyboard->Acquire()))return 6;
    visible_after=fs::exists(root/L"delay-first-paint")?GetTickCount64()+750:0;
    ShowWindow(window,SW_SHOWNOACTIVATE);SetTimer(window,1,20,nullptr);
    MSG message;while(GetMessageW(&message,nullptr,0,0)>0){TranslateMessage(&message);DispatchMessageW(&message);}
    keyboard->Release();input->Release();return 0;
}
