"""Kleine Helfer für Windows."""

from __future__ import annotations

import os
import sys

IS_WIN = sys.platform.startswith("win")


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
