"""Übertragungstests über ein virtuelles Schnittstellenpaar (pty, nur Linux/macOS)."""

import os
import queue
import sys
import threading
import time

import pytest

pytest.importorskip("serial")

from cnc_dnc.config import MachineProfile  # noqa: E402
from cnc_dnc.serial_link import SerialLink  # noqa: E402
from cnc_dnc.textutil import build_send_chunks  # noqa: E402

pytestmark = pytest.mark.skipif(sys.platform.startswith("win"), reason="benötigt pty")


@pytest.fixture
def pty_pair():
    import tty

    master, slave = os.openpty()
    tty.setraw(master)
    name = os.ttyname(slave)
    yield master, name
    os.close(master)
    os.close(slave)


def _profile(port, **kw):
    p = MachineProfile(port=port, flow="none", **kw)
    return p


def _wait_done(q, timeout=10.0):
    events = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            ev = q.get(timeout=0.1)
        except queue.Empty:
            continue
        events.append(ev)
        if ev[0] == "done":
            return events
    raise AssertionError("keine done-Meldung: {}".format(events[-5:]))


def _read_all(fd, until, timeout=10.0):
    buf = b""
    deadline = time.time() + timeout
    os.set_blocking(fd, False)
    while time.time() < deadline and not buf.endswith(until):
        try:
            buf += os.read(fd, 4096)
        except BlockingIOError:
            time.sleep(0.01)
    return buf


def test_send_program(pty_pair):
    master, port = pty_pair
    q = queue.Queue()
    link = SerialLink(q)
    prof = _profile(port)
    link.open(prof)
    text = "O1234\n" + "".join("N{} G1 X{}.5 Y-{}.25 F200\n".format(i * 10, i, i) for i in range(1, 400)) + "M30"
    chunks = build_send_chunks(text, eol="CRLF", frame_percent=True)
    expected = b"".join(chunks)

    received = {}
    t = threading.Thread(target=lambda: received.setdefault("data", _read_all(master, b"M30\r\n%\r\n")))
    t.start()
    link.start_send(chunks, prof)
    events = _wait_done(q)
    t.join()
    link.close()

    assert events[-1][:3] == ("done", "send", True)
    assert received["data"] == expected
    progress = [e for e in events if e[0] == "progress"]
    assert progress[-1][1] == progress[-1][2] == len(expected)


def test_send_with_line_delay_and_cancel(pty_pair):
    master, port = pty_pair
    q = queue.Queue()
    link = SerialLink(q)
    prof = _profile(port, line_delay_ms=200)
    link.open(prof)
    chunks = build_send_chunks("\n".join("N{}".format(i) for i in range(100)))
    link.start_send(chunks, prof)
    time.sleep(0.5)
    link.cancel()
    events = _wait_done(q)
    assert events[-1][:3] == ("done", "send", False)
    assert "abgebrochen" in events[-1][3]
    # danach muss ein neuer Sendevorgang normal funktionieren
    link.start_send(build_send_chunks("A\nB"), _profile(port))
    events = _wait_done(q)
    assert events[-1][:3] == ("done", "send", True)
    link.close()


def test_receive_fanuc_lf_cr_cr_in_small_chunks(pty_pair):
    """Fanuc sendet LF CR CR – das hat im alten Programm die Zeilen verschoben."""
    master, port = pty_pair
    q = queue.Queue()
    link = SerialLink(q)
    prof = _profile(port, end_mode="percent", idle_timeout_s=5)
    link.open(prof)
    link.start_receive(prof)
    lines = ["%", "O0815 (WELLE)", "N10 G90 G54", "N20 (VORSCHUB 50%)", "N30 G1 X10. F100", "N40 M30", "%"]
    raw = b"\x00" * 10 + b"\n\r\r".join(ln.encode() for ln in lines) + b"\n\r\r"
    time.sleep(0.2)
    for i in range(0, len(raw), 3):  # in krummen 3-Byte-Häppchen
        os.write(master, raw[i:i + 3])
        time.sleep(0.002)
    events = _wait_done(q)
    link.close()

    text = "".join(e[1] for e in events if e[0] == "text")
    assert text == "\n".join(lines) + "\n"
    assert events[-1][:3] == ("done", "receive", True)
    assert "Programmende" in events[-1][3]


def test_receive_idle_timeout_and_manual_stop(pty_pair):
    master, port = pty_pair
    q = queue.Queue()
    link = SerialLink(q)
    prof = _profile(port, end_mode="none", idle_timeout_s=0.5)
    link.open(prof)
    link.start_receive(prof)
    time.sleep(0.2)
    os.write(master, b"G0 X1\r\nG0 X2\r\n")
    events = _wait_done(q, timeout=5)
    assert "".join(e[1] for e in events if e[0] == "text") == "G0 X1\nG0 X2\n"
    assert "keine weiteren Daten" in events[-1][3]

    # Ohne Daten wartet der Empfang, bis von Hand beendet wird
    link.start_receive(prof)
    time.sleep(1.0)
    assert link.busy
    link.cancel()
    events = _wait_done(q, timeout=5)
    assert events[-1][:3] == ("done", "receive", False)
    link.close()


def test_open_missing_port_gives_friendly_message():
    from cnc_dnc.serial_link import SerialError

    link = SerialLink(queue.Queue())
    with pytest.raises(SerialError) as info:
        link.open(_profile("/dev/does-not-exist-123"))
    assert "nicht gefunden" in str(info.value)
