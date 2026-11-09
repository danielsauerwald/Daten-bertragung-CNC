"""Ende-zu-Ende-Test der Oberfläche über ein virtuelles Kabel (pty).

Läuft nur, wenn ein Bildschirm (oder Xvfb) vorhanden ist:
    xvfb-run -a python -m pytest tests/test_gui.py
"""

import os
import sys
import time

import pytest

tk = pytest.importorskip("tkinter")
pytest.importorskip("serial")

pytestmark = pytest.mark.skipif(sys.platform.startswith("win"), reason="benötigt pty")


@pytest.fixture
def app(tmp_path):
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("kein Bildschirm verfügbar")
    import tty

    from cnc_dnc.app import App
    from cnc_dnc.config import AppConfig

    master, slave = os.openpty()
    tty.setraw(master)
    os.set_blocking(master, False)
    cfg = AppConfig()
    cfg.path = str(tmp_path / "config.json")
    m = cfg.machines[0]
    m.port = os.ttyname(slave)
    m.flow = "none"
    m.folder = str(tmp_path / "programme")
    m.idle_timeout_s = 3
    m.auto_save_received = True
    a = App(root, cfg)
    a._test_master = master
    yield a
    a.link.close()
    root.destroy()
    os.close(master)
    os.close(slave)


def _pump(app, until, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.root.update()
        if until():
            return
        time.sleep(0.01)
    raise AssertionError("Zeitüberschreitung")


def test_receive_then_send_roundtrip(app):
    master = app._test_master
    assert app.link.is_open, "Port sollte beim Start automatisch geöffnet werden"

    # --- Empfangen: Fanuc-Ausgabe mit LF CR CR, in kleinen Häppchen ---
    app.receive()
    assert app.busy
    lines = ["%", "O4711 (TESTTEIL)", "N10 G90 G54", "N20 G0 X10. Y-5.", "N30 M30", "%"]
    raw = b"\x00" * 20 + b"\n\r\r".join(ln.encode() for ln in lines) + b"\n\r\r"
    for i in range(0, len(raw), 5):
        os.write(master, raw[i:i + 5])
        app.root.update()
        time.sleep(0.003)
    _pump(app, lambda: not app.busy)

    assert app.editor.get_text() == "\n".join(lines) + "\n"
    # automatisch gespeichert, Name aus dem Programmkopf
    assert app.current_path is not None
    assert os.path.basename(app.current_path) == "O4711_TESTTEIL.nc"
    with open(app.current_path, "rb") as fh:
        assert fh.read() == "\r\n".join(lines).encode() + b"\r\n"
    assert not app.dirty
    assert any(p.endswith("O4711_TESTTEIL.nc") for p in app._file_paths.values())

    # --- Bearbeiten und wieder senden ---
    app.editor.text.insert("3.0", "(GEAENDERT)\n")
    app.root.update()
    assert app.dirty
    app.send()
    assert app.busy
    got = b""

    def collect():
        nonlocal got
        try:
            got += os.read(master, 4096)
        except BlockingIOError:
            pass
        return not app.busy

    _pump(app, collect)
    collect()
    expected = ["%", "O4711 (TESTTEIL)", "(GEAENDERT)", "N10 G90 G54", "N20 G0 X10. Y-5.", "N30 M30", "%"]
    assert got == "\r\n".join(expected).encode() + b"\r\n"
    assert "gesendet" in app.status_text.cget("text")


def test_save_never_overwrites_other_program(app, tmp_path):
    app.editor.set_text("%\nO100\nM30\n%\n")
    app.dirty = True
    assert app.save(silent=True)
    first = app.current_path
    app.new_document()
    app.editor.set_text("%\nO100\nG0 X1\nM30\n%\n")
    app.dirty = True
    assert app.save(silent=True)
    assert app.current_path != first
    assert os.path.basename(app.current_path).startswith("O100_")
    assert len(os.listdir(os.path.dirname(first))) == 2
