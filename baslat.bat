@echo off
chcp 65001 >nul
title Otoklav Kontrol Paneli Baslatici
cd /d "%~dp0"

echo ==============================================================
echo   Otoklav Kontrol Paneli ve MQTT Baslatiliyor...
echo ==============================================================

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"

