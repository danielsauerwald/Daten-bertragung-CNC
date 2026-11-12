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
                 "kontur_btn",
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
    assert "\n\n\n\n" not in text and "G40 G00 X150 Z130\n\n;FERTIGDREHEN VCMT04\n" in text
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


def test_correction_sheet_in_dialog(app, tmp_path, monkeypatch):
    import cnc_dnc.setup_dialog as sd_mod
    from cnc_dnc import dialogs
    from cnc_dnc.setup_dialog import SetupSheetDialog
    from cnc_dnc.setupsheet import load_sheet

    root = str(tmp_path / "Emco-2")
    os.makedirs(os.path.join(root, "Metlog"))
    app.machine.folder = root
    app.apply_machine(connect=False)
    prog = os.path.join(root, "Metlog", "METLOG_300069.txt")
    with open(os.path.join(os.path.dirname(__file__), "data", "METLOG_300069.txt"), "rb") as a, \
            open(prog, "wb") as b:
        b.write(a.read())
    app.open_file(prog)
    monkeypatch.setattr(sd_mod, "open_external", lambda p: True)
    # Anschlag (T11) im Formular auf Seite 2 setzen
    monkeypatch.setattr(dialogs.FormDialog, "show",
                        lambda self: {"side": "Seite 2", "function": "ANSCHLAG", "tool": "ANSCHLAGBOLZEN",
                                      "remark": "auf S2 umgebaut"})

    def fake_show(self):
        s1 = self.korr_trees[1]
        tools = [s1.item(i, "values")[0] for i in s1.get_children()]
        assert tools[-2:] == ["T11", "T12"]  # Anschlag und Abstechen auf Seite 1
        iid = s1.get_children()[tools.index("T11")]
        s1.identify_row = lambda y: iid  # Antippen der Zeile T11 simulieren
        self._edit_korr(s1, type("Tap", (), {"y": 0})())
        s2 = self.korr_trees[2]
        assert [s2.item(i, "values")[0] for i in s2.get_children()][-1] == "T11"
        self.make_pdf("korr")
        return self.result

    monkeypatch.setattr(SetupSheetDialog, "show", fake_show)
    app.setup_sheet()
    assert os.path.exists(os.path.join(root, "Metlog", "METLOG_300069_Korrekturblatt.pdf"))
    # Funktion war schon "ANSCHLAG" (unverändert -> nicht gespeichert), Werkzeug neu eingetragen
    assert load_sheet(prog).korrektur["0|T11"] == {"side": 2, "tool": "ANSCHLAGBOLZEN", "remark": "auf S2 umgebaut"}


def test_merged_row_edit_moves_both_parts(app, tmp_path, monkeypatch):
    """Zusammengefasste Zeile (T12 aus KANAL1 + ABSTECH) wird komplett auf Seite 2 gesetzt."""
    from cnc_dnc import dialogs
    from cnc_dnc.setup_dialog import SetupSheetDialog
    from cnc_dnc.setupsheet import SheetData, extract_tools
    from cnc_dnc.setupsheet_pdf import SheetContext

    text = ("%_N_KANAL1_MPF\nT12D1 G96\nG00 X200\n;VOR\nT01D1\nM17\n%_N_KANAL2_MPF\n;PLAN\nT02D1\nM17\n"
            "%_N_ABSTECH_MPF\nT12D1\nM17\n")
    prog = tmp_path / "B.txt"
    prog.write_text(text)
    data = SheetData()
    d = SetupSheetDialog(app.root, app.fonts, SheetContext("B"), str(prog), extract_tools(text), [], data)
    s1 = d.korr_trees[1]
    tools = [s1.item(i, "values")[0] for i in s1.get_children()]
    assert tools == ["T1", "T12"]
    iid = s1.get_children()[1]
    s1.identify_row = lambda y: iid
    monkeypatch.setattr(dialogs.FormDialog, "show", lambda self: {"side": "Seite 2", "function": "ABSTECHEN",
                                                                    "tool": "", "remark": ""})
    d._edit_korr(s1, type("Tap", (), {"y": 0})())
    assert [s1.item(i, "values")[0] for i in s1.get_children()] == ["T1"]
    s2 = d.korr_trees[2]
    assert [s2.item(i, "values")[0] for i in s2.get_children()].count("T12") == 1
    d.destroy()


def test_correction_sheet_orientation_is_remembered(app, tmp_path, monkeypatch):
    import cnc_dnc.setup_dialog as sd_mod
    from cnc_dnc.setup_dialog import SetupSheetDialog

    prog = tmp_path / "TEIL.txt"
    prog.write_text("%_N_KANAL1_MPF\n;VORDREHEN VNMG08\nT01D1\nM17\n")
    app.open_file(str(prog))
    monkeypatch.setattr(sd_mod, "open_external", lambda p: True)
    assert app.cfg.korr_landscape is False

    def fake_show(self):
        s1 = self.korr_trees[1]
        assert s1.item(s1.get_children()[0], "values")[:3] == ("T1", "VORDREHEN", "VNMG08")
        self.orient_var.set("quer")
        self._orient_changed()
        self.make_pdf("korr")
        return self.result

    monkeypatch.setattr(SetupSheetDialog, "show", fake_show)
    app.setup_sheet()
    pdf = tmp_path / "TEIL_Korrekturblatt.pdf"
    assert b"/MediaBox [0 0 841.89 595.28]" in pdf.read_bytes()  # quer
    assert app.cfg.korr_landscape is True                         # gemerkt


# --------------------------------------------------------------------------
# Kontur-Assistent
# --------------------------------------------------------------------------


def _kontur_click(dlg, z, r):
    """In der Zeichnung auf die Stelle (z, r) tippen."""
    from types import SimpleNamespace

    x, y = dlg._P(z, r)
    dlg._click(SimpleNamespace(x=int(round(x)), y=int(round(y))))


def test_kontur_dialog_new_program_flow(app, monkeypatch):
    from cnc_dnc.kontur import Fraesung, neues_teil
    from cnc_dnc.kontur_dialog import KonturDialog

    app.machine.control = "sinumerik"  # Postprozessor = Vorlage zur Steuerung
    seen = {}

    def fake_show(self):
        self.deiconify()
        self.update()
        # erstes Öffnen: Beispielteil, damit man sieht wie es geht
        seen["start"] = self.teil.name
        self._replace_part(neues_teil(), keep_tools=True)
        self.fvars["name"].set("Welle 7")
        self.fvars["rohteil_d"].set("36")
        self.fvars["planaufmass"].set("0,5")  # Komma geht auch
        self.append_step(20, 15)
        self.append_step(30, 10)
        self.set_corner(1, "fase", 1.0)
        self.refresh()
        self.update()
        assert self.res.ok, self.res.errors
        # Zylinder Ø20 antippen -> Element 2, Auswahlleiste mit Drehen/Fräsen
        _kontur_click(self, -8, 10)
        seen["sel"] = self.sel
        seen["buttons"] = [w.cget("text") for w in self.sel_buttons.winfo_children() if w.winfo_class() == "TButton"]
        # Ecke 3 (Absatz Ø20 -> Ø30) antippen und entgraten
        _kontur_click(self, -15, 15)
        seen["corner"] = self.sel
        self.set_corner(3, "entgraten")
        # Fläche auf Ø20 bei 0° und 180° (6 mm Abstand zum Absatz, Fasenfräser Ø10 passt)
        self.teil.fraesungen.append(Fraesung("flaeche", [0, 180], 0, -9, 17, 0, 0, 5, True, 7, 0.3))
        self.refresh()
        self.update()
        _kontur_click(self, -4.5, 9)
        seen["feat"] = self.sel
        seen["msg"] = self.msg.cget("text")
        self.nb.select(4)
        self.update()
        seen["code_tab"] = self.code_text.get("1.0", "3.0")
        assert self.res.ok, self.res.errors  # sonst öffnet finish() eine Meldung und der Test hängt
        self.finish("neu")
        return self.result

    monkeypatch.setattr(KonturDialog, "show", fake_show)
    app.kontur()
    assert seen["start"] == "BEISPIEL"
    assert seen["sel"] == ("seg", 2)
    assert seen["buttons"][:2] == ["Drehen", "Nicht drehen"] and "Fläche" in seen["buttons"]
    assert seen["corner"] == ("corner", 3)
    assert seen["feat"] == ("feat", 0)
    assert seen["msg"].startswith("✓")
    assert seen["code_tab"].startswith("%_N_WELLE_7_MPF")
    text = app.editor.get_text()
    assert text.startswith("%_N_WELLE_7_MPF\n") and text.rstrip().endswith("M30")
    assert "T01D1 G95 G18" in text and "SPOS=DC(180)" in text and ";ENTGRATEN FLAECHE SW17" in text
    assert app.dirty and app.current_path is None
    assert "Kontur" in app.status_text.cget("text")
    # Eingaben bleiben für das nächste Mal gespeichert
    from cnc_dnc.kontur import load_teil

    again = load_teil(app._kontur_path())
    assert again.name == "Welle 7" and again.rohteil_d == 36 and again.planaufmass == 0.5
    assert [(p.x, p.z) for p in again.punkte] == [(0, 0), (20, 0), (20, -15), (30, -15), (30, -25)]
    assert again.punkte[3].ecke == "entgraten" and len(again.fraesungen) == 1


def test_kontur_insert_at_cursor_and_errors(app, monkeypatch):
    from cnc_dnc.kontur import KPunkt
    from cnc_dnc.kontur_dialog import KonturDialog

    app.machine.control = "sinumerik"
    app.editor.set_text("%_N_KANAL1_MPF\nG54\nM17\n")
    app.editor.text.mark_set("insert", "2.1")
    seen = {}

    def fake_show(self):
        self.update()
        # Einstich -> Fehler, Tasten gesperrt
        self.teil.punkte.insert(3, KPunkt(14, -15))
        self.refresh()
        seen["err"] = self.msg.cget("text")
        seen["disabled"] = self.new_btn.instate(["disabled"])
        del self.teil.punkte[3]
        self.refresh()
        seen["enabled"] = not self.new_btn.instate(["disabled"])
        assert self.res.ok, self.res.errors
        self.finish("einfuegen")
        return self.result

    monkeypatch.setattr(KonturDialog, "show", fake_show)
    app.kontur()
    assert seen["err"].startswith("✕") and "Einstiche" in seen["err"] and seen["disabled"]
    assert seen["enabled"]
    lines = app.editor.get_text().split("\n")
    assert lines[:4] == ["%_N_KANAL1_MPF", "G54", ";KONTUR-ASSISTENT TEIL BEISPIEL", ";MASCHINE 78: EMCO - 2"]
    assert "M17" in lines and "M30" not in lines and app.dirty
    app.undo()
    app.root.update()
    assert app.editor.get_text() == "%_N_KANAL1_MPF\nG54\nM17\n"


def test_kontur_feature_dialog_parses_input(app):
    from cnc_dnc.kontur import Fraesung, beispiel_teil
    from cnc_dnc.kontur_dialog import FeatureDialog

    teil = beispiel_teil()
    dlg = FeatureDialog(app.root, app.fonts, teil, Fraesung("nut", [0], -16, -28, 10, 2, 0, 5), "Nut")
    dlg.vars["mass"].set("8,5")
    dlg.vars["winkel"].set("30")
    dlg._spread(3)
    assert dlg.vars["winkel"].get() == "30 150 270"
    # nur passende Werkzeuge (Fräser, radial) stehen zur Wahl
    assert dlg.vars["wz"].get() == "T5 FRAESER D8"
    dlg.deburr.set(True)
    dlg._toggle()
    dlg.ok()
    fr = dlg.result
    assert fr.mass == 8.5 and fr.winkel == [30, 150, 270] and fr.entgraten and fr.wz_entgraten == 7
    # Eingabefehler: Fenster bleibt offen und sagt warum
    dlg2 = FeatureDialog(app.root, app.fonts, teil, Fraesung("flaeche"), "Fläche")
    dlg2.vars["mass"].set("abc")
    dlg2.ok()
    assert dlg2.result is None and "Zahlen" in dlg2.msg.cget("text")
    dlg2.destroy()


def test_kontur_uses_postprocessor_of_chosen_machine(app, monkeypatch):
    """Fanuc-Maschine -> Fanuc-Code; Postprozessor im Assistenten ändern -> bei der Maschine gespeichert."""
    from cnc_dnc.config import AppConfig, MachineProfile
    from cnc_dnc.kontur_dialog import KonturDialog
    from cnc_dnc.postpro import fuer_maschine, vorlage

    emco = app.machine
    emco.control = "sinumerik"
    emco.post = vorlage("sin_s4").to_dict()
    doosan = MachineProfile(number="5", name="Doosan", control="fanuc", port="COM9")
    app.cfg.machines.append(doosan)
    seen = {}

    def fake_show(self):
        self.update()
        seen["start"] = (self.pp.awz_ein, "S4=3000 M4=3" in self.res.code)
        seen["info"] = self.pp_info.cget("text")
        # andere Maschine wählen -> Fanuc
        self.machine_cb.set(doosan.display_name)
        self._machine_selected()
        self.refresh()
        seen["fanuc"] = self.res.code
        seen["head"] = self.head_sub.cget("text")
        # Postprozessor anpassen (wie im Fenster „Postprozessor bearbeiten“)
        pp = self.pp.copy()
        pp.c_ein, pp.c_aus, pp.awz_ein, pp.awz_aus = "M35", "M34", "M23 S{S}", "M25"
        self.set_postpro(pp)
        self.refresh()
        self.finish("neu")
        return self.result

    monkeypatch.setattr(KonturDialog, "show", fake_show)
    app.kontur()
    assert seen["start"] == ("S4={S} M4=3", True)
    assert "S4=3000 M4=3" in seen["info"] and "SPOS=DC(90)" in seen["info"]
    assert "(VORDREHEN VNMG08)\nT0101" in seen["fanuc"] and "G00 C180." in seen["fanuc"]
    assert "Doosan" in seen["head"] and "Fanuc" in seen["head"]
    text = app.editor.get_text()
    assert text.startswith("%\nO1000 (BEISPIEL)\n") and "M35\nG00 C0.\nM23 S3000" in text and "M25" in text
    # bei der Maschine gespeichert, Emco unverändert
    saved = AppConfig.load(app.cfg.path)
    assert fuer_maschine(saved.machine_by_id(doosan.id)).awz_ein == "M23 S{S}"
    assert fuer_maschine(saved.machine_by_id(emco.id)).awz_ein == "S4={S} M4=3"


def test_postpro_dialog_preview_presets_and_check(app, monkeypatch):
    import cnc_dnc.postpro_dialog as pd
    from cnc_dnc.postpro import vorlage
    from cnc_dnc.postpro_dialog import PostproDialog

    dlg = PostproDialog(app.root, app.fonts, "Doosan", vorlage("fanuc"))
    dlg.update()
    text = dlg.preview.get("1.0", "end")
    assert "O1000 (BEISPIEL)" in text and "T0101" in text and "G00 C180." in text and "M13 S3000" in text
    assert "C-Achse ein" in dlg.msg.cget("text")  # Hinweis: M-Befehl fehlt noch
    # Vorlage Siemens S4 übernehmen
    monkeypatch.setattr(pd.dialogs, "confirm", lambda *a, **k: True)
    dlg.preset.set("Siemens Sinumerik – angetr. Werkzeug S4= / M4=")
    dlg.load_preset()
    dlg.update_view()
    text = dlg.preview.get("1.0", "end")
    assert "T01D1 G95 G18 S2300 M3 M8" in text and "S4=3000 M4=3" in text and "M4=5" in text
    # fehlender Platzhalter -> Fehler, Speichern gesperrt
    dlg.vars["c_pos"].set("SPOS=0")
    dlg.update_view()
    assert dlg.ok_btn.instate(["disabled"]) and "{W}" in dlg.msg.cget("text")
    dlg.ok()
    assert dlg.result is None
    dlg.vars["c_pos"].set("SPOS[1]=DC({W})")
    dlg.vars["wechsel_x"].set("200")
    dlg.ok()
    pp = dlg.result
    assert pp.c_pos == "SPOS[1]=DC({W})" and pp.wechsel_x == 200 and pp.awz_ein == "S4={S} M4=3"


def test_machine_edit_dialog_has_postprocessor_tab(app, monkeypatch):
    from cnc_dnc.dialogs import MachineEditDialog
    from cnc_dnc.postpro import fuer_maschine, vorlage
    from cnc_dnc.postpro_dialog import PostproDialog

    m = app.machine
    m.control = "sinumerik"
    dlg = MachineEditDialog(app.root, app.fonts, m)
    assert "Vorlage zur Steuerung" in dlg.post_info.cget("text") and "S3=3000 M3=3" in dlg.post_info.cget("text")
    # Steuerung umstellen -> Anzeige folgt der Vorlage
    dlg.v["control"].set("Fanuc / ISO")
    dlg._apply_control_preset()
    assert "T0101" in dlg.post_info.cget("text")
    dlg.v["control"].set("Siemens Sinumerik")
    dlg._apply_control_preset()

    def fake_show(self):
        assert self.pp.art == "sinumerik"
        return vorlage("sin_s4")

    monkeypatch.setattr(PostproDialog, "show", fake_show)
    dlg._edit_post()
    assert "eigene Einstellung" in dlg.post_info.cget("text") and "S4=3000 M4=3" in dlg.post_info.cget("text")
    dlg.save()
    prof = dlg.result
    assert fuer_maschine(prof).awz_ein == "S4={S} M4=3" and prof.id == m.id


def _inside(widget, win):
    """Liegt das Widget vollständig im Fenster (nicht abgeschnitten)?"""
    win.update()
    return (widget.winfo_ismapped() and widget.winfo_rooty() + widget.winfo_height() <= win.winfo_rooty()
            + win.winfo_height() and widget.winfo_rootx() + widget.winfo_width() <= win.winfo_rootx()
            + win.winfo_width())


def test_kontur_dialog_fits_above_windows_taskbar(app, monkeypatch):
    """Toughbook unter Windows: 1366x768, Taskleiste 40 px, Titelleiste 39 px -> Fenster 1350x689."""
    import cnc_dnc.dialogs as dlgmod
    from cnc_dnc.kontur import beispiel_teil
    from cnc_dnc.kontur_dialog import KonturDialog
    from cnc_dnc.postpro import vorlage
    from cnc_dnc.postpro_dialog import PostproDialog

    monkeypatch.setattr(dlgmod, "work_area", lambda w: (0, 0, 1366, 728))
    monkeypatch.setattr(dlgmod, "window_decoration", lambda: (16, 39))
    dlg = KonturDialog(app.root, app.fonts, beispiel_teil(), machines=app.cfg.machines, machine_id=app.machine.id)
    w, h, x, y = dlg.place_on_screen(*dlg._size)
    assert (w, h, x, y) == (1350, 689, 0, 0)
    dlg.geometry("{}x{}+0+0".format(w, h))
    dlg.deiconify()
    for _ in range(3):
        dlg.update()
    for widget in (dlg.new_btn, dlg.insert_btn, dlg.cb_fine, dlg.cb_rough):
        assert _inside(widget, dlg), widget
    assert all(_inside(b, dlg) for b in dlg.sel_buttons.winfo_children())

    def buttons(w):
        for c in w.winfo_children():
            if c.winfo_class() == "TButton":
                yield c
            yield from buttons(c)

    for tab in range(5):  # alle Reiter: Tasten und Felder vollständig sichtbar
        dlg.nb.select(tab)
        dlg.update()
        frame = dlg.nb.nametowidget(dlg.nb.select())
        found = [b for b in buttons(frame) if b.winfo_ismapped()]
        assert (found or tab == 4) and all(_inside(b, dlg) for b in found), tab
    dlg.destroy()
    pp = PostproDialog(app.root, app.fonts, "Emco", vorlage("sin_s3"))
    w, h, x, y = pp.place_on_screen(*pp._size)
    assert y + h + 39 <= 728
    pp.geometry("{}x{}+0+0".format(w, h))
    pp.deiconify()
    assert _inside(pp.ok_btn, pp)
    pp.destroy()


def test_tall_dialog_is_shrunk_but_keeps_its_buttons(app, monkeypatch):
    import cnc_dnc.dialogs as dlgmod
    from cnc_dnc.kontur import Fraesung, beispiel_teil
    from cnc_dnc.kontur_dialog import FeatureDialog

    monkeypatch.setattr(dlgmod, "work_area", lambda w: (0, 0, 1366, 520))  # sehr wenig Platz
    monkeypatch.setattr(dlgmod, "window_decoration", lambda: (16, 39))
    dlg = FeatureDialog(app.root, app.fonts, beispiel_teil(), Fraesung("nut", entgraten=True, wz=5, wz_entgraten=7),
                        "Nut")
    dlg.update_idletasks()
    req = dlg.winfo_reqheight()
    w, h, x, y = dlg.place_on_screen(dlg.winfo_reqwidth(), req)
    assert h < req and y + h + 39 <= 520
    dlg.deiconify()
    dlg.update()
    ok = [b for bar in dlg.pack_slaves() for b in bar.winfo_children()
          if b.winfo_class() == "TButton" and b.cget("text") == "Übernehmen"][0]
    assert _inside(ok, dlg)
    dlg.destroy()


def test_drawing_shows_length_dimensions(app):
    from cnc_dnc.kontur import beispiel_teil
    from cnc_dnc.kontur_dialog import KonturDialog

    dlg = KonturDialog(app.root, app.fonts, beispiel_teil(), machines=app.cfg.machines, machine_id=app.machine.id)
    dlg.geometry("1350x689+0+0")
    dlg.deiconify()
    dlg.update()
    dlg.redraw()
    texts = [dlg.cv.itemcget(i, "text") for i in dlg.cv.find_all() if dlg.cv.type(i) == "text"]
    for t in ("10", "15", "40", "Rohteil 45", "Ø20", "Ø26"):
        assert t in texts, t
    assert texts.count("15") == 2
    # Spalte Länge in der Punktliste
    assert dlg.pt_tree.item("p2", "values")[3] == "15" and dlg.pt_tree.item("p1", "values")[3] == ""
    # Stange: kein Rohteilmaß, Hinweis in der Zeichnung
    dlg.fvars["rohteil_l"].set("0")
    dlg.refresh()
    texts = [dlg.cv.itemcget(i, "text") for i in dlg.cv.find_all() if dlg.cv.type(i) == "text"]
    assert "Stange" in texts and not any(t.startswith("Rohteil ") for t in texts)
    dlg.destroy()
