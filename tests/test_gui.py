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
    if a._analyze_job is not None:
        root.after_cancel(a._analyze_job)
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


def test_receive_sinumerik_workpiece_shows_name_and_saves(app, monkeypatch):
    """Werkstück METLOG_300069 empfangen (Profil steht auf Fanuc/%-Erkennung!)."""
    from cnc_dnc import dialogs
    from cnc_dnc.textutil import load_program_file

    master = app._test_master
    app.machine.auto_save_received = False
    app.machine.end_mode = "percent"
    app.machine.idle_timeout_s = 1.5
    raw = open(os.path.join(os.path.dirname(__file__), "data", "METLOG_300069.txt"), "rb").read()

    app.receive()
    # erst die ersten beiden Programmköpfe, dann eine Pause wie an der echten Maschine
    first = raw.index(b"%_N_ENTLADEN")
    for i in range(0, first, 7):
        os.write(master, raw[i:min(i + 7, first)])
        app.root.update()
    t0 = time.time()
    while time.time() - t0 < 1.0:
        app.root.update()
        time.sleep(0.01)
    assert app.busy, "Empfang darf beim zweiten %_N_-Kopf nicht enden"
    assert app.doc_label.cget("text") == "METLOG_300069"
    for i in range(first, len(raw), 64):
        os.write(master, raw[i:i + 64])
        app.root.update()
    _pump(app, lambda: not app.busy)

    text = app.editor.get_text()
    assert text == load_program_file(os.path.join(os.path.dirname(__file__), "data", "METLOG_300069.txt"))
    assert app.doc_label.cget("text") == "METLOG_300069"
    assert "8 Programme" in app.progs_btn.cget("text")
    assert app.save_btn.sub_label.cget("text") == "als METLOG_300069.nc"
    assert app.save_btn.kind == "primary"

    # Sprung zum Unterprogramm KANAL2
    app.editor.goto_line(153, top=True)
    app.root.update()
    assert app.editor.text.index("@0,0").startswith("153.")

    # Speichern-Fenster: Name ist vorbelegt, Endung .txt wird gewählt und gemerkt
    seen = {}

    def fake_show(self):
        seen["name"] = self.name_var.get()
        seen["detected"] = self.hint.master.winfo_children()[1].cget("text")
        self.ext_var.set(".txt")
        self.save()
        return self.result

    monkeypatch.setattr(dialogs.SaveDialog, "show", fake_show)
    assert app.save()
    assert seen["name"] == "METLOG_300069"
    assert "METLOG_300069" in seen["detected"]
    assert os.path.basename(app.current_path) == "METLOG_300069.txt"
    assert app.machine.file_ext == ".txt"
    assert app.save_btn.kind == "neutral"


def test_save_dialog_overwrite_warning_and_date(app, tmp_path):
    from cnc_dnc.dialogs import SaveDialog

    (tmp_path / "TEIL.nc").write_text("x")
    d = SaveDialog(app.root, app.fonts, "TEIL", ".nc", str(tmp_path))
    assert d.save_btn.cget("text") == "Überschreiben"
    d._add_date()
    assert d.name_var.get().startswith("TEIL_20")
    assert d.save_btn.cget("text") == "Speichern"
    d.name_var.set("A/B")
    assert d.save_btn.instate(["disabled"])
    d.name_var.set("NEU.nc")  # Endung im Namen wird nicht verdoppelt
    assert d.path() == str(tmp_path / "NEU.nc")
    d.destroy()
