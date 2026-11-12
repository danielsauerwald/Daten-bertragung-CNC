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


# --------------------------------------------------------------------------
# Fenster in den freien Bereich (ohne Taskleiste) legen
# --------------------------------------------------------------------------


def work_area(widget) -> tuple:
    """Freier Bildschirmbereich (x, y, Breite, Höhe) – unter Windows ohne Taskleiste.

    Das Programm ist DPI-bewusst (enable_dpi_awareness), daher liefern Windows und Tk
    dieselben (physischen) Pixel – auch bei 125 % oder 150 % Skalierung.
    """
    if IS_WIN:
        try:
            import ctypes
            from ctypes import wintypes

            rect = wintypes.RECT()
            if _user32().SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):  # SPI_GETWORKAREA
                return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
        except Exception:
            pass
    sw, sh = widget.winfo_screenwidth(), widget.winfo_screenheight()
    return 0, 0, sw, sh - (48 if IS_WIN else 0)


def window_decoration() -> tuple:
    """(Breite, Höhe), die Windows um den Fensterinhalt legt: Rahmen und Titelleiste."""
    if IS_WIN:
        try:
            u = _user32()
            caption = u.GetSystemMetrics(4)  # SM_CYCAPTION
            fx, fy = u.GetSystemMetrics(32), u.GetSystemMetrics(33)  # SM_CXSIZEFRAME, SM_CYSIZEFRAME
            pad = u.GetSystemMetrics(92)  # SM_CXPADDEDBORDER
            if caption > 0:
                return 2 * (fx + pad), caption + 2 * (fy + pad)
        except Exception:
            pass
        return 16, 39
    return 0, 0


def fit_window(want_w: int, want_h: int, area: tuple, deco: tuple, center_on=None) -> tuple:
    """Größe und Lage (w, h, x, y) so, dass das ganze Fenster samt Titelleiste im freien
    Bereich liegt. ``center_on``: (x, y, w, h) eines Fensters, über dem es mittig stehen soll."""
    ax, ay, aw, ah = area
    dw, dh = deco
    w = max(200, min(int(want_w), aw - dw))
    h = max(150, min(int(want_h), ah - dh))
    if center_on:
        cx, cy, cw, ch = center_on
        x = cx + (cw - w) // 2
        y = cy + (ch - h) // 3
    else:
        x = ax + (aw - w - dw) // 2
        y = ay + (ah - h - dh) // 2
    x = max(ax, min(x, ax + aw - w - dw))
    y = max(ay, min(y, ay + ah - h - dh))
    return w, h, x, y
