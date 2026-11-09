@echo off
setlocal
if "%~2"=="" (
    echo Usage: build.cmd vcvars64.bat workspace
    exit /b 2
)
if not exist "%~2\source-baseline.json" (
    echo Prepare an isolated workspace first.
    exit /b 2
)
call "%~1" >nul
if errorlevel 1 exit /b 1
cd /d "%~2"
if errorlevel 1 exit /b 1
cl /nologo /EHsc /W4 /std:c++17 /utf-8 /LD "%~dp0Probe.cpp" /Fe:GameQaPs1Probe.dll
exit /b %errorlevel%
