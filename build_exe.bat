@echo off
REM ---------------------------------------------------------------------------
REM  Baut dist\CNC-Datenuebertragung.exe (eine einzelne Datei, kein Python noetig)
REM
REM  Fuer Windows 7 unbedingt mit Python 3.8 bauen (neuere Versionen laufen
REM  nicht mehr unter Windows 7). Die 32-Bit-Version laeuft auf 32- und 64-Bit.
REM  Download: https://www.python.org/downloads/release/python-3810/
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

set PY=python
py -3.8-32 --version >nul 2>&1 && set PY=py -3.8-32

echo Verwende: %PY%
%PY% --version || goto :error
%PY% -m pip install --upgrade pip
%PY% -m pip install -r requirements-dev.txt || goto :error
%PY% -m pytest -q tests\test_textutil.py || goto :error
%PY% -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name CNC-Datenuebertragung --icon assets\app.ico ^
    CNC-Datenuebertragung.pyw || goto :error

echo.
echo Fertig: dist\CNC-Datenuebertragung.exe
pause
exit /b 0

:error
echo.
echo FEHLER beim Bauen - siehe Meldungen oben.
pause
exit /b 1
