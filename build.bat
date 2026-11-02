@echo off
rem Builds dist\LyricsTool.exe. First time: python -m venv .venv, then pip install -r requirements.txt inside it.
cd /d "%~dp0"
.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests || exit /b 1
.venv\Scripts\python.exe make_icon.py || exit /b 1
.venv\Scripts\python.exe make_version.py || exit /b 1
.venv\Scripts\pyinstaller.exe --noconfirm --clean --onefile --windowed --name LyricsTool --icon assets\icon.ico --version-file assets\version.txt ^
  --add-data "assets\icon.ico;assets" --collect-data opencc lyricstool_main.py || exit /b 1
echo Built dist\LyricsTool.exe
