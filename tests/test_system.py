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
