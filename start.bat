@echo off
chcp 65001 >nul 2>&1
title game-qa-kit v0.1
python "%~dp0launch.py"
if errorlevel 1 pause
