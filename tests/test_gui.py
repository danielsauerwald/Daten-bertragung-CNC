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
    a.cancel_jobs()
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


def _customer_tree(root):
    for d in ("Metlog", "Maweco", "betzer"):
        os.makedirs(os.path.join(root, d))
    for rel in ("TAKEOVER.txt", "MAWECO_300210.txt", "Metlog/METLOG_300012.txt", "betzer/BETZER_300245.txt"):
        with open(os.path.join(root, rel), "w") as fh:
            fh.write("%\nO1\nM30\n%\n")


def test_file_list_shows_customer_folders_and_searches_all(app, tmp_path):
    root = str(tmp_path / "Emco-2")
    _customer_tree(root)
    app.machine.folder = root
    app.browse_dir = None
    app.refresh_files(rescan=True)
    tree = app.file_tree
    names = [tree.item(i, "text") for i in tree.get_children()]
    assert names[:3] == ["betzer", "Maweco", "Metlog"]  # Ordner zuerst, A–Z
    assert set(names[3:]) == {"TAKEOVER.txt", "MAWECO_300210.txt"}
    assert app.up_btn.instate(["disabled"])

    # Kundenordner öffnen und zurück
    metlog = [i for i in tree.get_children() if tree.item(i, "text") == "Metlog"][0]
    app.open_folder(app._dir_paths[metlog])
    assert app.crumb_label.cget("text") == "Emco-2 › Metlog"
    assert [tree.item(i, "text") for i in tree.get_children()] == ["METLOG_300012.txt"]
    app.set_sort("name")
    assert tree.heading("#0", "text") == "NAME ▴"
    app.set_sort("date")
    app.folder_up()
    assert app.crumb_label.cget("text") == "Emco-2"

    # Suche findet auch Dateien in Kundenordnern
    app._placeholder_state["active"] = False
    app.filter_var.set("300245")
    rows = [tree.item(i, "text").strip() for i in tree.get_children()]
    assert rows == ["betzer", "BETZER_300245.txt"]  # Treffer unter ihrem Kundenordner
    assert app.crumb_label.cget("text") == "1 Treffer"
    # Antippen des Ordnerkopfs öffnet den Ordner
    folder_row = tree.get_children()[0]
    assert app._dir_paths[folder_row].endswith("betzer")
    app.filter_var.set("")


def test_save_suggests_customer_folder(app, tmp_path, monkeypatch):
    from cnc_dnc import dialogs

    root = str(tmp_path / "Emco-2")
    _customer_tree(root)
    app.machine.folder = root
    app.apply_machine(connect=False)
    app.editor.set_text("%_N_TEIL_MPF\n;$PATH=/_N_WKS_DIR/_N_METLOG_300069_WPD\nM17\n")
    app.origin = "received"
    app.dirty = True
    app._reanalyze()
    seen = {}

    def fake_show(self):
        seen["folder_choice"] = self.folder_var.get()
        seen["reason"] = self.folder_label.cget("text")
        seen["choices"] = list(self.folder_cb.cget("values"))
        self.save()
        return self.result

    monkeypatch.setattr(dialogs.SaveDialog, "show", fake_show)
    assert app.save()
    assert seen["folder_choice"] == "Metlog"
    assert "Kundenordner" in seen["reason"]
    assert seen["choices"][0].endswith("(Hauptordner)") and "betzer" in seen["choices"]
    assert app.current_path == os.path.join(root, "Metlog", "METLOG_300069.nc")
    # Liste springt in den Ordner, in dem gespeichert wurde
    assert app.crumb_label.cget("text") == "Emco-2 › Metlog"


def test_all_buttons_visible_on_tablet_screen(app):
    """1366x768 minus Taskleiste, Titelleiste und Rand – nichts darf abgeschnitten sein.

    660 Pixel sind am Foto vom echten Toughbook abgeglichen (dort fehlten in 2.1 die
    Tasten, die hier bei 660 Pixel Höhe ebenfalls nicht mehr hineinpassen).
    """
    app.root.geometry("1366x660+0+0")
    for _ in range(5):
        app.root.update()
        time.sleep(0.05)
    bottom = app.root.winfo_rooty() + app.root.winfo_height()
    right = app.root.winfo_rootx() + app.root.winfo_width()
    for name in ("send_btn", "recv_btn", "open_btn", "new_btn", "save_btn", "saveas_btn", "compact_btn", "number_btn", "setup_btn",
                 "find_btn", "zoom_out_btn", "zoom_in_btn", "machines_btn", "connect_btn", "machine_btn",
                 "hide_files_btn", "undo_btn"):
        w = getattr(app, name)
        assert w.winfo_ismapped(), name
        assert w.winfo_rooty() + w.winfo_height() <= bottom - 50, name  # Statuszeile darunter
        assert w.winfo_rootx() + w.winfo_width() <= right, name
    # die Ordner-Schaltfläche unter der Dateiliste
    btn = app.file_panel.grid_slaves(row=5, column=0)[0]
    assert btn.winfo_rooty() + btn.winfo_height() <= bottom - 50


def test_touch_keyboard_opens_when_input_is_tapped(app, monkeypatch):
    import cnc_dnc.app as app_mod
    from cnc_dnc.dialogs import SaveDialog

    calls = []
    monkeypatch.setattr(app_mod, "show_touch_keyboard", lambda: calls.append(1) or True)

    def tap(w, dy=0):
        w.event_generate("<ButtonPress-1>", x=6, y=6)
        w.event_generate("<ButtonRelease-1>", x=6, y=6 + dy)
        app.root.update()
        time.sleep(0.15)
        app.root.update()
        n = len(calls)
        del calls[:]
        return n

    app.root.update()
    assert tap(app.search_entry) == 1            # Suchfeld antippen -> Tastatur
    assert tap(app.search_entry, dy=40) == 0     # Wischen ist kein Antippen
    assert tap(app.editor.text) == 0             # Editor: standardmäßig aus
    app.cfg.touch_keyboard_editor = True
    assert tap(app.editor.text) == 1
    app.editor.set_readonly(True)                # während einer Übertragung
    assert tap(app.editor.text) == 0
    app.editor.set_readonly(False)

    d = SaveDialog(app.root, app.fonts, "TEIL", ".nc", os.getcwd())
    d.deiconify()
    app.root.update()
    assert tap(d.entry) == 1                     # Dateiname eingeben
    assert tap(d.folder_cb) == 0                 # reine Auswahlliste -> keine Tastatur
    d.destroy()

    app.cfg.touch_keyboard = False               # Einstellung aus
    assert tap(app.search_entry) == 0


def test_compact_button_and_undo(app):
    from cnc_dnc.textutil import load_program_file

    original = load_program_file(os.path.join(os.path.dirname(__file__), "data", "METLOG_300069.txt"))
    app.editor.set_text(original)
    app.dirty = False
    app.compact()
    app.root.update()
    text = app.editor.get_text()
    assert "\n\n\n\n" not in text and "G40 G00 X150 Z130\n\n\n;FERTIGDREHEN VCMT04\n" in text
    assert app.dirty
    assert "Zusammengerückt" in app.status_text.cget("text")
    # ein Tipp auf "Rückgängig" holt den alten Stand komplett zurück
    app.undo()
    app.root.update()
    assert app.editor.get_text() == original
    # nochmal zusammenrücken -> gleiches Ergebnis; zweites Mal ändert nichts
    app.compact()
    app.compact()
    assert "schon zusammengerückt" in app.status_text.cget("text")


def test_file_list_can_be_hidden_and_shown_again(app):
    app.root.update()
    app.toggle_file_panel()
    app.root.update()
    assert not app.file_panel.winfo_ismapped() and app.show_files_btn.winfo_ismapped()
    app.toggle_file_panel()
    app.root.update()
    assert app.file_panel.winfo_ismapped() and not app.show_files_btn.winfo_ismapped()


def test_setup_sheet_flow(app, tmp_path, monkeypatch):
    import cnc_dnc.app as app_mod
    import cnc_dnc.setup_dialog as sd_mod
    from cnc_dnc.setup_dialog import SetupSheetDialog

    root = str(tmp_path / "Emco-2")
    os.makedirs(os.path.join(root, "Metlog"))
    app.machine.folder = root
    app.apply_machine(connect=False)
    src = os.path.join(os.path.dirname(__file__), "data", "METLOG_300069.txt")
    prog = os.path.join(root, "Metlog", "METLOG_300069.txt")
    with open(src, "rb") as a, open(prog, "wb") as b:
        b.write(a.read())
    app.open_file(prog)
    opened = []
    monkeypatch.setattr(sd_mod, "open_external", lambda p: opened.append(p) or True)

    def fake_show(self):
        assert self.ctx.program == "METLOG_300069" and self.ctx.customer == "Metlog"
        seiten = [t.heading("desc", "text") for t in self.trees]
        assert len(seiten) == 3  # Seite 1, Seite 2, weitere Programme
        first = self.trees[0].get_children()[0]
        assert self.trees[0].item(first, "values")[:2] == ("T1", "VORDREHEN VNMG08")
        self.data.set_tool(self._rows[str(self.trees[0]) + first], "VORDREHEN VNMG08", "Platte neu")
        self.field_vars["hsp_druck"].set("25")
        self.notes.insert("1.0", "Erstes Teil messen")
        self.make_pdf()
        return self.result

    monkeypatch.setattr(SetupSheetDialog, "show", fake_show)
    app.setup_sheet()
    pdf = os.path.join(root, "Metlog", "METLOG_300069_Einrichteblatt.pdf")
    assert opened == [pdf] and os.path.exists(pdf)
    assert os.path.exists(os.path.join(root, "Metlog", "METLOG_300069.einrichteblatt.json"))
    # Liste zeigt das PDF, aber nicht die Begleitdatei
    names = [app.file_tree.item(i, "text") for i in app.file_tree.get_children()]
    assert "METLOG_300069_Einrichteblatt.pdf" in names
    assert not any(n.endswith(".json") for n in names)
    # PDF in der Liste antippen -> PDF-Programm, nicht der Editor
    ext = []
    monkeypatch.setattr(app_mod, "open_external", lambda p: ext.append(p) or True)
    app.open_file(pdf)
    assert ext == [pdf] and app.current_path == prog
    # Eingaben bleiben gespeichert
    from cnc_dnc.setupsheet import load_sheet

    data = load_sheet(prog)
    assert data.fields["hsp_druck"] == "25" and data.notes == "Erstes Teil messen"


def test_renumber_button_flow(app, monkeypatch):
    from cnc_dnc.numbering_dialog import RenumberDialog
    from cnc_dnc.textutil import load_program_file

    original = load_program_file(os.path.join(os.path.dirname(__file__), "data", "METLOG_300069.txt"))
    app.editor.set_text(original)
    seen = {}

    def fake_show(self):
        seen["preview"] = self.preview.get("1.0", "end")
        seen["msg"] = self.message.cget("text")
        self.scheme_var.set("flat")
        self._update()
        seen["flat_preview"] = self.preview.get("1.0", "end")
        self.ok()
        return self.result

    monkeypatch.setattr(RenumberDialog, "show", fake_show)
    app.renumber()
    assert "N1000 T12D1" in seen["preview"] and "Sätze bekommen eine Nummer" in seen["msg"]
    assert "N10 T12D1" in seen["flat_preview"]
    text = app.editor.get_text()
    assert "\nN10 T12D1 G96" in text and app.dirty
    assert app.cfg.renumber_scheme == "flat"
    assert "nummeriert" in app.status_text.cget("text")
    app.undo()
    app.root.update()
    assert app.editor.get_text() == original
