#pragma once
#include <windows.h>
// EasyHook 的旧头文件重定义 SDK 版本宏；仅在该头文件内部隔离这些定义。
#pragma push_macro("NTDDI_VERSION")
#pragma push_macro("_WIN32_WINNT")
#undef NTDDI_VERSION
#undef _WIN32_WINNT
#include <easyhook.h>
#pragma pop_macro("_WIN32_WINNT")
#pragma pop_macro("NTDDI_VERSION")
