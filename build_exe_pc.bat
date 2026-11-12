@echo off
REM ---------------------------------------------------------------------------
REM  Baut dist\CNC-Datenuebertragung-PC.exe fuer Windows 10/11 (64 Bit)
REM  mit "Bilder lesen" (M-/G-Code-Listen, Zyklen) – braucht Python 3.12.
REM  Fuer das Toughbook mit Windows 7 weiterhin build_exe.bat benutzen.
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

set PY=python
py -3.12 --version >nul 2>&1 && set PY=py -3.12

echo Verwende: %PY%
%PY% --version || goto :error
%PY% -m pip install --upgrade pip
%PY% -m pip install -r requirements-pc.txt || goto :error
%PY% -m pytest -q tests\test_textutil.py tests\test_ki.py || goto :error
%PY% -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name CNC-Datenuebertragung-PC --icon assets\app.ico ^
    --collect-submodules anthropic ^
    CNC-Datenuebertragung.pyw || goto :error

echo.
echo Fertig: dist\CNC-Datenuebertragung-PC.exe
pause
exit /b 0

:error
echo.
echo FEHLER beim Bauen - siehe Meldungen oben.
pause
exit /b 1
