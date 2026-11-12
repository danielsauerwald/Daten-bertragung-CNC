"""Logik der Bildschirmtastatur mit nachgebautem Windows (user32) testen."""

import pytest

from cnc_dnc import system


class FakeUser32:
    def __init__(self):
        self.visible = False
        self.posted = []

    def FindWindowW(self, cls, _title):  # noqa: N802 (Windows-Name)
        return 42 if cls == "IPTip_Main_Window" else 0

    def IsWindowVisible(self, hwnd):  # noqa: N802
        return self.visible

    def GetWindowLongW(self, hwnd, idx):  # noqa: N802
        return 0

    def PostMessageW(self, hwnd, msg, wparam, lparam):  # noqa: N802
        self.posted.append((hwnd, msg, wparam, lparam))
        self.visible = False
        return 1


@pytest.fixture
def win(monkeypatch):
    fake = FakeUser32()
    opened = []

    def fake_open():
        opened.append(1)
        fake.visible = True
        return True

    monkeypatch.setattr(system, "IS_WIN", True)
    monkeypatch.setattr(system, "_user32", lambda: fake)
    monkeypatch.setattr(system, "open_touch_keyboard", fake_open)
    monkeypatch.setattr(system, "_state", {"opened_by_us": False, "last_show": 0.0})
    return fake, opened


def test_show_opens_once_and_not_when_visible(win):
    fake, opened = win
    assert system.show_touch_keyboard()
    assert opened == [1] and system.keyboard_visible()
    # sofort nochmal antippen: nicht ein zweites Mal starten
    assert system.show_touch_keyboard()
    assert opened == [1]
    # schon offen (auch nach Ablauf der Sperrzeit) -> nicht neu starten
    system._state["last_show"] = 0.0
    system.show_touch_keyboard()
    assert opened == [1]


def test_hide_only_if_opened_by_program(win):
    fake, opened = win
    fake.visible = True  # vom Benutzer selbst geöffnet
    system.hide_touch_keyboard()
    assert fake.posted == []  # nicht anfassen
    fake.visible = False
    system.show_touch_keyboard()
    system.hide_touch_keyboard()
    assert fake.posted == [(42, 0x0112, 0xF060, 0)]
    assert not system.keyboard_visible()


def test_toggle(win):
    fake, opened = win
    system.toggle_touch_keyboard()
    assert fake.visible and opened == [1]
    system.toggle_touch_keyboard()
    assert not fake.visible and len(fake.posted) == 1


def test_errors_never_crash(monkeypatch):
    def broken():
        raise OSError("kein user32")

    monkeypatch.setattr(system, "IS_WIN", True)
    monkeypatch.setattr(system, "_user32", broken)
    monkeypatch.setattr(system, "open_touch_keyboard", lambda: False)
    monkeypatch.setattr(system, "_state", {"opened_by_us": True, "last_show": 0.0})
    assert system.keyboard_visible() is False
    system.hide_touch_keyboard()
    assert system.show_touch_keyboard() is False


# --------------------------------------------------------------------------
# Fenster nicht unter die Taskleiste
# --------------------------------------------------------------------------


def test_fit_window_respects_taskbar_and_title_bar():
    from cnc_dnc.system import fit_window

    # Toughbook 1366x768, Taskleiste 40 px unten, Titelleiste+Rahmen 39 px
    area, deco = (0, 0, 1366, 728), (16, 39)
    w, h, x, y = fit_window(1600, 1000, area, deco)
    assert (w, h, x, y) == (1350, 689, 0, 0)
    assert y + h + deco[1] <= 728 and x + w + deco[0] <= 1366
    # Full HD mit 150 %: Taskleiste 72 px, Titelleiste+Rahmen 58 px
    w, h, x, y = fit_window(1500, 1050, (0, 0, 1920, 1008), (24, 58))
    assert h == 950 and y + h + 58 <= 1008
    # Taskleiste oben: Fenster beginnt darunter
    w, h, x, y = fit_window(800, 600, (0, 48, 1920, 1032), (16, 39), center_on=(0, 0, 1920, 200))
    assert y >= 48 and y + h + 39 <= 1080
    # kleines Fenster mittig über dem Hauptfenster, aber nie über den Rand hinaus
    w, h, x, y = fit_window(400, 300, (0, 0, 1366, 728), (16, 39), center_on=(1200, 600, 400, 300))
    assert (w, h) == (400, 300) and x + w + 16 <= 1366 and y + h + 39 <= 728


def test_work_area_fallback_without_windows():
    from cnc_dnc import system

    class Fake:
        def winfo_screenwidth(self):
            return 1366

        def winfo_screenheight(self):
            return 768

    x, y, w, h = system.work_area(Fake())
    assert (x, y, w) == (0, 0, 1366) and h <= 768
