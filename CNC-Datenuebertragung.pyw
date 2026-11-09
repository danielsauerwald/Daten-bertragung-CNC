"""Doppelklick-Starter für Windows (startet ohne schwarzes Konsolenfenster).

Voraussetzung: Python 3.8 (für Windows 7) oder neuer und  pip install pyserial
Wird auch von PyInstaller als Startdatei für die .exe benutzt.
"""
from cnc_dnc.__main__ import run

if __name__ == "__main__":
    run()
