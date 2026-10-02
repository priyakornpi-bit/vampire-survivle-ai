@echo off
call "%ProgramFiles%\Microsoft Visual Studio\18\Community\Common7\Tools\VsDevCmd.bat" -arch=x64
if errorlevel 1 exit /b %errorlevel%
cd /d "%~dp0"
cl /nologo /std:c++17 /EHsc .\VP_AI_SEVER.cpp ws2_32.lib /Fe:VP_AI_SEVER.exe