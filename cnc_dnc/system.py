"""Kleine Helfer für Windows."""

from __future__ import annotations

import os
import sys
import time

IS_WIN = sys.platform.startswith("win")

# Fensterklassen der Bildschirmtastaturen: Tablet-Eingabebereich (TabTip) und OSK
_KEYBOARD_CLASSES = ("IPTip_Main_Window", "OSKMainClass")
_state = {"opened_by_us": False, "last_show": 0.0}


def open_touch_keyboard() -> bool:
    """Bildschirmtastatur von Windows öffnen (Tablet-Eingabebereich oder OSK)."""
    if not IS_WIN:
        return False
    candidates = []
    for base in (os.environ.get("CommonProgramW6432"), os.environ.get("CommonProgramFiles")):
        if base:
            candidates.append(os.path.join(base, "microsoft shared", "ink", "TabTip.exe"))
    windir = os.environ.get("WINDIR", r"C:\Windows")
    candidates += [os.path.join(windir, "Sysnative", "osk.exe"), os.path.join(windir, "System32", "osk.exe")]
    for path in candidates:
        if os.path.exists(path):
            try:
                os.startfile(path)  # type: ignore[attr-defined]
                return True
            except OSError:
                continue
    return False


def _user32():
    import ctypes

    return ctypes.windll.user32  # type: ignore[attr-defined]


def keyboard_visible() -> bool:
    """Ist gerade eine Bildschirmtastatur sichtbar?"""
    if not IS_WIN:
        return False
    try:
        u = _user32()
        for cls in _KEYBOARD_CLASSES:
            hwnd = u.FindWindowW(cls, None)
            if hwnd and u.IsWindowVisible(hwnd):
                style = u.GetWindowLongW(hwnd, -16)  # GWL_STYLE
                if not style & 0x08000000:  # WS_DISABLED = versteckt (Windows 8/10)
                    return True
    except Exception:
        pass
    return False


def show_touch_keyboard() -> bool:
    """Tastatur einblenden, falls sie nicht schon offen ist (für das automatische Öffnen)."""
    if not IS_WIN:
        return False
    now = time.monotonic()
    if now - _state["last_show"] < 1.0 or keyboard_visible():
        return True  # nicht doppelt starten – das würde sie bei manchen Versionen wieder schließen
    _state["last_show"] = now
    ok = open_touch_keyboard()
    if ok:
        _state["opened_by_us"] = True
    return ok


def hide_touch_keyboard(only_if_ours: bool = True) -> None:
    """Tastatur wieder ausblenden (z. B. wenn ein Dialog geschlossen wird)."""
    if not IS_WIN or (only_if_ours and not _state["opened_by_us"]):
        return
    _state["opened_by_us"] = False
    try:
        u = _user32()
        for cls in _KEYBOARD_CLASSES:
            hwnd = u.FindWindowW(cls, None)
            if hwnd and u.IsWindowVisible(hwnd):
                u.PostMessageW(hwnd, 0x0112, 0xF060, 0)  # WM_SYSCOMMAND, SC_CLOSE
    except Exception:
        pass


def toggle_touch_keyboard() -> bool:
    """Für die Taste ⌨: offen → schließen, geschlossen → öffnen."""
    if keyboard_visible():
        hide_touch_keyboard(only_if_ours=False)
        return True
    _state["last_show"] = 0.0
    return show_touch_keyboard()


def open_external(path: str) -> bool:
    """Datei mit dem zugehörigen Programm öffnen (z. B. PDF-Anzeige)."""
    try:
        if IS_WIN:
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            import subprocess

            subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except (OSError, ValueError):
        return False
