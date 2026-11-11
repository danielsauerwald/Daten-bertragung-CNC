"""Hauptfenster der CNC-Datenübertragung."""

from __future__ import annotations

import datetime
import os
import queue
import time
import tkinter as tk
from tkinter import filedialog, ttk
from typing import Callable, Dict, Optional

from . import APP_NAME, APP_VERSION, dialogs
from .config import AppConfig, MachineProfile
from .files import count_files, is_inside, list_folder, rel_label, search, suggest_folder, walk_files
from .serial_link import SerialError, SerialLink
from .serial_link import serial as pyserial
from .system import hide_touch_keyboard, show_touch_keyboard, toggle_touch_keyboard
from .textutil import (
    DIALECT_NAMES,
    ProgramInfo,
    analyze_program,
    build_send_chunks,
    compact_program,
    count_lines,
    find_problem_chars,
    load_program_file,
    save_program_file,
)
from .theme import C, IS_WIN, Fonts, px, setup_style
from .widgets import ActionButton, CodeEditor, Led

FILE_TYPES = [
    ("CNC-Programme", "*.nc *.txt *.cnc *.h *.mpf *.spf *.tap *.iso *.prg *.ngc *.din *.eia *.ptp"),
    ("Alle Dateien", "*.*"),
]
POLL_MS = 40


def fmt_int(n: int) -> str:
    return "{:,}".format(n).replace(",", ".")


def fmt_duration(seconds: float) -> str:
    s = int(seconds)
    return "{}:{:02d}".format(s // 60, s % 60)


class App:
    def __init__(self, root: tk.Tk, config: AppConfig) -> None:
        self.root = root
        self.cfg = config
        self.fonts = Fonts(root, config.editor_font_size)
        setup_style(root, self.fonts)

        self.events: "queue.Queue" = queue.Queue()
        self.link = SerialLink(self.events)
        self.current_path: Optional[str] = None
        self.origin = "new"  # "new" | "file" | "received"
        self.dirty = False
        self.doc_info = ProgramInfo()
        self._analyze_job: Optional[str] = None
        self._last_analyze = 0.0
        self.transfer: Optional[Dict] = None
        self.tx_until = 0.0
        self.rx_until = 0.0
        self._last_led_poll = 0.0
        self._file_paths: Dict[str, str] = {}
        self._dir_paths: Dict[str, str] = {}
        self._tree_drag: Optional[Dict] = None
        self.browse_dir: Optional[str] = None   # gerade angezeigter (Kunden-)Ordner
        self.sort_by = "date"                    # "date" | "name"
        self._walk_cache: Optional[tuple] = None  # (Ordner, Zeitpunkt, Dateien) für die Suche

        root.title(APP_NAME)
        root.minsize(px(root, 960), px(root, 600))
        self._set_icon()
        self._build()
        self._bind_keys()
        self._install_touch_keyboard()
        self.apply_machine(connect=True)
        self._update_title()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._poll_job = root.after(POLL_MS, self._poll)
        if pyserial is None:
            root.after(300, lambda: dialogs.error(
                root, self.fonts, "pyserial fehlt",
                "Das Modul „pyserial“ ist nicht installiert. Senden und Empfangen sind nicht möglich.",
                detail="Installation: pip install pyserial"))

    @property
    def machine(self) -> MachineProfile:
        return self.cfg.active_machine

    @property
    def busy(self) -> bool:
        """Läuft gerade eine Übertragung (aus Sicht der Oberfläche)?"""
        return self.transfer is not None

    # ======================================================================
    # Aufbau der Oberfläche
    # ======================================================================

    def _build(self) -> None:
        r = self.root
        r.grid_rowconfigure(1, weight=1)
        r.grid_columnconfigure(0, weight=1)
        self._build_header()
        body = tk.Frame(r, bg=C["bg"])
        body.grid(row=1, column=0, sticky="nsew", padx=px(r, 12), pady=(px(r, 12), 0))
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)
        self.body = body
        self._build_file_panel(body)
        self._build_editor(body)
        self._build_actions(body)
        self._build_statusbar()
        if not self.cfg.show_file_panel:
            self.file_panel.grid_remove()

    def _build_header(self) -> None:
        r = self.root
        f = self.fonts
        h = tk.Frame(r, bg=C["header"])
        h.grid(row=0, column=0, sticky="ew")
        self.header = h

        self.machine_btn = ActionButton(h, f, text="", subtitle="", kind="header", command=self.choose_machine)
        self.machine_btn.title_label.configure(font=f.title)
        self.machine_btn.pack(side="left", padx=(px(r, 6), 0), pady=px(r, 4))

        sep = tk.Frame(h, bg=C["header_hover"], width=1)
        sep.pack(side="left", fill="y", pady=px(r, 12), padx=px(r, 12))

        conn = tk.Frame(h, bg=C["header"])
        conn.pack(side="left", pady=px(r, 6))
        self.serial_label = tk.Label(conn, text="", bg=C["header"], fg=C["header_text"], font=f.ui_bold, anchor="w")
        self.serial_label.pack(anchor="w")
        self.conn_label = tk.Label(conn, text="", bg=C["header"], fg=C["header_muted"], font=f.small, anchor="w")
        self.conn_label.pack(anchor="w")

        self.connect_btn = ActionButton(h, f, text="Verbinden", kind="header", compact=True,
                                        command=self.toggle_connection, outline=C["header_muted"])
        self.connect_btn.pack(side="left", padx=px(r, 12))

        right = tk.Frame(h, bg=C["header"])
        right.pack(side="right", padx=px(r, 8))
        if IS_WIN:
            kb = ActionButton(right, f, text="⌨", kind="header", compact=True, command=self._keyboard)
            kb.title_label.configure(font=f.big)
            kb.pack(side="right", padx=(px(r, 6), 0))
        self.machines_btn = ActionButton(right, f, text="⚙  Maschinen", kind="header", compact=True,
                                         command=self.choose_machine, outline=C["header_muted"])
        self.machines_btn.pack(side="right", padx=(px(r, 16), 0))
        leds = tk.Frame(right, bg=C["header"])
        leds.pack(side="right")
        self.leds: Dict[str, Led] = {}
        for name, color in (("CTS", C["led_on"]), ("DSR", C["led_on"]), ("CD", C["led_on"]), ("RI", C["led_on"]),
                            ("TX", C["led_tx"]), ("RX", C["led_rx"])):
            led = Led(leds, f, name, on_color=color)
            led.pack(side="left", padx=px(r, 5))
            self.leds[name] = led

    def _build_file_panel(self, body: tk.Frame) -> None:
        r = self.root
        f = self.fonts
        panel = tk.Frame(body, bg=C["bg"], width=px(r, 320))
        panel.grid(row=0, column=0, sticky="nsew", padx=(0, px(r, 12)))
        panel.grid_propagate(False)
        panel.grid_rowconfigure(4, weight=1)
        panel.grid_columnconfigure(0, weight=1)
        self.file_panel = panel

        top = tk.Frame(panel, bg=C["bg"])
        top.grid(row=0, column=0, sticky="ew")
        tk.Label(top, text="Programme", bg=C["bg"], fg=C["text"], font=f.heading).pack(side="left")
        ttk.Button(top, text="↻", style="Small.TButton", width=3,
                   command=lambda: self.refresh_files(rescan=True)).pack(side="right")

        self.folder_label = tk.Label(panel, text="", bg=C["bg"], fg=C["muted"], font=f.small, anchor="w",
                                     justify="left", wraplength=px(r, 310))
        self.folder_label.grid(row=1, column=0, sticky="ew", pady=(px(r, 2), px(r, 6)))

        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *a: self.refresh_files())
        self.search_entry = ttk.Entry(panel, textvariable=self.filter_var, font=f.ui)
        self.search_entry.grid(row=2, column=0, sticky="ew", pady=(0, px(r, 6)))
        self._placeholder(self.search_entry, self.filter_var, "Suchen in allen Ordnern …")

        # Wo bin ich? "‹ Zurück   Emco-2 › Metlog"
        crumb = tk.Frame(panel, bg=C["bg"])
        crumb.grid(row=3, column=0, sticky="ew", pady=(0, px(r, 6)))
        self.up_btn = ttk.Button(crumb, text="‹ Zurück", style="Small.TButton", command=self.folder_up)
        self.up_btn.pack(side="left")
        self.crumb_label = tk.Label(crumb, text="", bg=C["bg"], fg=C["text"], font=f.ui_bold, anchor="w")
        self.crumb_label.pack(side="left", fill="x", expand=True, padx=(px(r, 8), 0))

        box = tk.Frame(panel, bg=C["surface"], highlightthickness=1, highlightbackground=C["border"])
        box.grid(row=4, column=0, sticky="nsew")
        box.grid_rowconfigure(0, weight=1)
        box.grid_columnconfigure(0, weight=1)
        style = ttk.Style(r)
        style.configure("Files.Treeview", indent=0)
        style.layout("Files.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        style.layout("Files.Treeview.Item", [("Treeitem.padding", {"sticky": "nswe", "children": [
            ("Treeitem.image", {"side": "left", "sticky": ""}),
            ("Treeitem.text", {"side": "left", "sticky": ""})]})])
        self.file_tree = ttk.Treeview(box, columns=("date",), show="tree headings", selectmode="browse",
                                      style="Files.Treeview")
        self.file_tree.heading("#0", text="NAME", anchor="w", command=lambda: self.set_sort("name"))
        self.file_tree.heading("date", text="DATUM ▾", anchor="w", command=lambda: self.set_sort("date"))
        self.file_tree.column("#0", width=px(r, 196), anchor="w", stretch=True)
        self.file_tree.column("date", width=px(r, 82), anchor="w", stretch=False)
        self.file_tree.tag_configure("hint", foreground=C["muted"])
        self.file_tree.tag_configure("current", font=f.ui_bold)
        self.file_tree.tag_configure("folder", foreground=C["text"])
        self.file_tree.tag_configure("count", foreground=C["muted"])
        try:
            from .icon import FILE_PNG_BASE64, FOLDER_PNG_BASE64

            self._folder_icon = tk.PhotoImage(master=r, data=FOLDER_PNG_BASE64)
            self._file_icon = tk.PhotoImage(master=r, data=FILE_PNG_BASE64)
        except tk.TclError:
            self._folder_icon = self._file_icon = ""
        sb = ttk.Scrollbar(box, orient="vertical", command=self.file_tree.yview)
        self.file_tree.configure(yscrollcommand=sb.set)
        self.file_tree.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        self.file_tree.bind("<ButtonPress-1>", self._tree_press)
        self.file_tree.bind("<B1-Motion>", self._tree_motion)
        self.file_tree.bind("<ButtonRelease-1>", self._tree_release)
        self.file_tree.bind("<Return>", lambda e: self._open_selected_file())
        self.file_tree.bind("<BackSpace>", lambda e: self.folder_up())

        ttk.Button(panel, text="Programmordner ändern …", style="Small.TButton", command=self.change_folder).grid(
            row=5, column=0, sticky="ew", pady=(px(r, 8), 0))

    def _build_editor(self, body: tk.Frame) -> None:
        r = self.root
        f = self.fonts
        center = tk.Frame(body, bg=C["bg"])
        center.grid(row=0, column=1, sticky="nsew")
        center.grid_rowconfigure(2, weight=1)
        center.grid_columnconfigure(0, weight=1)

        info = tk.Frame(center, bg=C["bg"])
        info.grid(row=0, column=0, sticky="ew", pady=(0, px(r, 8)))
        # Rechte Seite zuerst packen, damit sie bei langen Namen nicht zusammengedrückt wird
        self.cursor_label = tk.Label(info, text="", bg=C["bg"], fg=C["muted"], font=f.ui)
        self.cursor_label.pack(side="right")
        self.undo_btn = ttk.Button(info, text="↶ Rückgängig", style="Small.TButton", command=self.undo)
        self.undo_btn.pack(side="right", padx=(px(r, 10), px(r, 14)))
        left = tk.Frame(info, bg=C["bg"])
        left.pack(side="left", fill="x", expand=True)
        self._info_left = left
        title_row = tk.Frame(left, bg=C["bg"])
        title_row.pack(anchor="w")
        self.doc_label = tk.Label(title_row, text="", bg=C["bg"], fg=C["text"], font=f.heading, anchor="w")
        self.doc_label.pack(side="left")
        # zweite Zeile: "● nicht gespeichert   Sinumerik · Empfangen von …"
        sub_row = tk.Frame(left, bg=C["bg"])
        sub_row.pack(anchor="w")
        self.dirty_label = tk.Label(sub_row, text="", bg=C["bg"], fg=C["warn"], font=f.small_bold)
        self.dirty_label.pack(side="left")
        self.doc_sub = tk.Label(sub_row, text="", bg=C["bg"], fg=C["muted"], font=f.small, anchor="w")
        self.doc_sub.pack(side="left")
        # Sprungliste, wenn mehrere (Unter-)Programme in einer Datei stehen
        self.progs_btn = ttk.Button(info, text="", style="Small.TButton", command=self._show_program_menu)

        # Suchleiste (ausgeblendet, bis "Suchen" gedrückt wird)
        self.find_bar = tk.Frame(center, bg=C["bg"])
        self.find_var = tk.StringVar()
        self.find_entry = ttk.Entry(self.find_bar, textvariable=self.find_var, font=f.ui, width=24)
        self.find_entry.pack(side="left")
        self.find_entry.bind("<Return>", lambda e: self.find_next())
        self.find_entry.bind("<Shift-Return>", lambda e: self.find_next(backwards=True))
        self.find_entry.bind("<Escape>", lambda e: self.toggle_find(False))
        self.find_var.trace_add("write", lambda *a: self._find_changed())
        ttk.Button(self.find_bar, text="▲", style="Small.TButton", width=3,
                   command=lambda: self.find_next(backwards=True)).pack(side="left", padx=(px(r, 6), 0))
        ttk.Button(self.find_bar, text="▼", style="Small.TButton", width=3, command=self.find_next).pack(
            side="left", padx=(px(r, 4), 0))
        self.find_count = tk.Label(self.find_bar, text="", bg=C["bg"], fg=C["muted"], font=f.ui)
        self.find_count.pack(side="left", padx=px(r, 10))
        ttk.Button(self.find_bar, text="Schließen", style="Small.TButton",
                   command=lambda: self.toggle_find(False)).pack(side="right")

        self.editor = CodeEditor(center, f, on_change=self._on_editor_change, on_cursor=self._on_cursor)
        self.editor.grid(row=2, column=0, sticky="nsew")

    def _build_actions(self, body: tk.Frame) -> None:
        """Rechte Spalte. Muss auch auf 1366x768 mit Taskleiste komplett sichtbar sein."""
        r = self.root
        f = self.fonts
        side = tk.Frame(body, bg=C["bg"], width=px(r, 236))
        side.grid(row=0, column=2, sticky="nsew", padx=(px(r, 12), 0))
        side.pack_propagate(False)

        def add(widget, gap=6):
            widget.pack(fill="x", pady=(0, px(r, gap)))

        def row(gap=6):
            fr = tk.Frame(side, bg=C["bg"])
            fr.pack(fill="x", pady=(0, px(r, gap)))
            return fr

        def place_row(fr, buttons, weights=None):
            for i, b in enumerate(buttons):
                w = weights[i] if weights else 1
                fr.grid_columnconfigure(i, weight=w, uniform="btn")
                b.grid(row=0, column=i, sticky="ew",
                       padx=(0 if i == 0 else px(r, 3), 0 if i == len(buttons) - 1 else px(r, 3)))

        self.send_btn = ActionButton(side, f, "Senden", subtitle="PC  →  Maschine", icon="▶", kind="primary",
                                     big=True, command=self.send)
        add(self.send_btn)
        self.recv_btn = ActionButton(side, f, "Empfangen", subtitle="Maschine  →  PC", icon="◀", kind="success",
                                     big=True, command=self.receive)
        add(self.recv_btn, gap=14)

        add(self._section(side, "DATEI"), gap=4)
        fr = row()
        self.open_btn = ActionButton(fr, f, "Öffnen …", kind="neutral", compact=True, anchor="center",
                                     command=self.open_file)
        self.new_btn = ActionButton(fr, f, "Neu", kind="neutral", compact=True, anchor="center",
                                    command=self.new_document)
        place_row(fr, [self.open_btn, self.new_btn])
        self.save_btn = ActionButton(side, f, "Speichern", subtitle="", kind="neutral", compact=True,
                                     command=self.save)
        add(self.save_btn)
        self.saveas_btn = ActionButton(side, f, "Speichern unter …", kind="neutral", compact=True,
                                       command=self.save_as)
        add(self.saveas_btn, gap=14)

        add(self._section(side, "BEARBEITEN"), gap=4)
        self.compact_btn = ActionButton(side, f, "Zusammenrücken", subtitle="Leerzeilen raus · 2 vor Werkzeug",
                                        kind="neutral", compact=True, command=self.compact)
        add(self.compact_btn, gap=14)

        add(self._section(side, "ANSICHT"), gap=4)
        fr = row()
        self.find_btn = ActionButton(fr, f, "Suchen", kind="neutral", compact=True, anchor="center",
                                     command=lambda: self.toggle_find())
        self.zoom_out_btn = ActionButton(fr, f, "A−", kind="neutral", compact=True, anchor="center",
                                         command=lambda: self.zoom(-1))
        self.zoom_in_btn = ActionButton(fr, f, "A+", kind="neutral", compact=True, anchor="center",
                                        command=lambda: self.zoom(1))
        place_row(fr, [self.find_btn, self.zoom_out_btn, self.zoom_in_btn], weights=(2, 1, 1))
        self.panel_btn = ActionButton(side, f, "Dateiliste ein/aus", kind="neutral", compact=True,
                                      anchor="center", command=self.toggle_file_panel)
        add(self.panel_btn, gap=0)

    def _section(self, parent, text: str) -> tk.Label:
        return tk.Label(parent, text=text, bg=C["bg"], fg=C["faint"], font=self.fonts.small_bold, anchor="w")

    def _build_statusbar(self) -> None:
        r = self.root
        f = self.fonts
        bar = tk.Frame(r, bg=C["bg"])
        bar.grid(row=2, column=0, sticky="ew", padx=px(r, 12), pady=px(r, 10))
        bar.grid_columnconfigure(0, weight=1)
        self.statusbar = bar

        # Normale Statuszeile
        self.status_frame = tk.Frame(bar, bg=C["surface"], highlightthickness=1, highlightbackground=C["border"])
        self.status_frame.grid(row=0, column=0, sticky="ew")
        self.status_icon = tk.Label(self.status_frame, text="●", bg=C["surface"], fg=C["faint"], font=f.ui_bold)
        self.status_icon.pack(side="left", padx=(px(r, 12), px(r, 6)), pady=px(r, 10))
        # Versionsnummer zuerst packen, damit lange Meldungen sie nicht überdecken
        tk.Label(self.status_frame, text="Version " + APP_VERSION, bg=C["surface"], fg=C["faint"],
                 font=f.small).pack(side="right", padx=px(r, 12))
        self.status_text = tk.Label(self.status_frame, text="Bereit.", bg=C["surface"], fg=C["text"], font=f.ui,
                                    anchor="w")
        self.status_text.pack(side="left", fill="x", expand=True)

        # Übertragungsanzeige
        tf = tk.Frame(bar, bg=C["surface"], highlightthickness=1, highlightbackground=C["border"])
        self.transfer_frame = tf
        tf.grid_columnconfigure(0, weight=1)
        left = tk.Frame(tf, bg=C["surface"])
        left.grid(row=0, column=0, sticky="ew", padx=px(r, 14), pady=px(r, 10))
        left.grid_columnconfigure(0, weight=1)
        self.transfer_title = tk.Label(left, text="", bg=C["surface"], fg=C["text"], font=f.heading, anchor="w")
        self.transfer_title.grid(row=0, column=0, sticky="w")
        self.transfer_pct = tk.Label(left, text="", bg=C["surface"], fg=C["text"], font=f.heading, anchor="e")
        self.transfer_pct.grid(row=0, column=1, sticky="e")
        self.progress = ttk.Progressbar(left, style="Transfer.Horizontal.TProgressbar", maximum=100.0)
        self.progress.grid(row=1, column=0, columnspan=2, sticky="ew", pady=px(r, 6))
        self.transfer_detail = tk.Label(left, text="", bg=C["surface"], fg=C["muted"], font=f.ui, anchor="w")
        self.transfer_detail.grid(row=2, column=0, columnspan=2, sticky="w")
        self.transfer_btn = ActionButton(tf, f, "Abbrechen", icon="■", kind="danger", command=self.cancel_transfer)
        self.transfer_btn.grid(row=0, column=1, sticky="nse", padx=(0, px(r, 10)), pady=px(r, 10))

    def _placeholder(self, entry: ttk.Entry, var: tk.StringVar, text: str) -> None:
        """Grauer Hinweistext in einem leeren Eingabefeld."""
        style_name = "Placeholder.TEntry"
        ttk.Style(self.root).configure(style_name, foreground=C["faint"])
        state = {"active": False}

        def show(_e=None):
            if not var.get():
                state["active"] = True
                entry.configure(style=style_name)
                entry.insert(0, text)

        def hide(_e=None):
            if state["active"]:
                state["active"] = False
                entry.delete(0, "end")
                entry.configure(style="TEntry")

        entry.bind("<FocusIn>", hide, add="+")
        entry.bind("<FocusOut>", show, add="+")
        self._placeholder_state = state
        show()

    def _filter_text(self) -> str:
        if getattr(self, "_placeholder_state", {}).get("active"):
            return ""
        return self.filter_var.get().strip().lower()

    def _bind_keys(self) -> None:
        def bind(seqs, fn: Callable[[], object]):
            for seq in seqs:
                for w in (self.root, self.editor.text):
                    w.bind(seq, lambda e: (fn(), "break")[1])

        bind(("<Control-o>", "<Control-O>"), self.open_file)
        bind(("<Control-s>",), self.save)
        bind(("<Control-S>",), self.save_as)
        bind(("<Control-n>", "<Control-N>"), self.new_document)
        bind(("<Control-f>", "<Control-F>"), lambda: self.toggle_find(True))
        bind(("<Control-plus>", "<Control-equal>", "<Control-KP_Add>"), lambda: self.zoom(1))
        bind(("<Control-minus>", "<Control-KP_Subtract>"), lambda: self.zoom(-1))
        bind(("<F5>",), self.send)
        bind(("<F6>",), self.receive)

    # ======================================================================
    # Maschine & Verbindung
    # ======================================================================

    def apply_machine(self, connect: bool = True) -> None:
        m = self.machine
        self.machine_btn.set_text(m.display_name + "  ▾", m.control_name + "  ·  antippen zum Wechseln")
        self.serial_label.configure(text=m.serial_summary)
        if self.link.is_open:
            self.link.close()
        if connect:
            self._connect(show_error=False)
        self._update_connection()
        self.browse_dir = None
        self._walk_cache = None
        self.refresh_files()
        self._update_title()

    def choose_machine(self) -> None:
        if self.busy:
            return
        before = self.machine.id

        def changed():
            # Maschine wurde im Dialog bearbeitet/angelegt/gelöscht
            self.apply_machine(connect=True)

        res = dialogs.MachineListDialog(self.root, self.fonts, self.cfg, on_changed=changed).show()
        if res and res != before:
            self.cfg.active_machine_id = res
            self._save_config()
            self.apply_machine(connect=True)
            self.set_status("Maschine gewechselt: " + self.machine.display_name, "ok")

    def _connect(self, show_error: bool) -> bool:
        try:
            self.link.ensure_open(self.machine)
            ok = True
        except SerialError as exc:
            ok = False
            if show_error:
                dialogs.error(self.root, self.fonts, "Verbindung nicht möglich", str(exc))
            else:
                self.set_status(str(exc), "warn")
        self._update_connection()
        return ok

    def toggle_connection(self) -> None:
        if self.busy:
            return
        if self.link.is_open:
            self.link.close()
            self.set_status("Verbindung zu {} getrennt.".format(self.machine.port))
        elif self._connect(show_error=True):
            self.set_status("{} geöffnet.".format(self.machine.port), "ok")
        self._update_connection()

    def _update_connection(self) -> None:
        if self.link.is_open:
            self.conn_label.configure(text="●  Verbunden", fg=C["led_on"])
            self.connect_btn.set_text("Trennen")
        else:
            self.conn_label.configure(text="○  Nicht verbunden", fg=C["header_muted"])
            self.connect_btn.set_text("Verbinden")
            for led in self.leds.values():
                led.set(False)

    def _keyboard(self) -> None:
        if not toggle_touch_keyboard():
            self.set_status("Bildschirmtastatur konnte nicht gestartet werden.", "warn")

    # -- Bildschirmtastatur automatisch öffnen --------------------------------
    #
    # Windows öffnet den Tablet-Eingabebereich nur bei seinen eigenen Eingabefeldern
    # von selbst. Deshalb öffnen wir ihn, sobald ein Eingabefeld *angetippt* wird
    # (nicht beim bloßen Fokus, sonst ginge sie bei jedem Dialog auf).

    INPUT_CLASSES = ("TEntry", "TCombobox", "TSpinbox", "Entry", "Spinbox", "Text")

    def _install_touch_keyboard(self) -> None:
        self._tap_start: Optional[tuple] = None
        for cls in self.INPUT_CLASSES:
            # Doppel-/Dreifach-Tipp sind in Tk eigene Ereignisse – die zählen auch
            for seq in ("<ButtonPress-1>", "<Double-ButtonPress-1>", "<Triple-ButtonPress-1>"):
                self.root.bind_class(cls, seq, self._input_press, add="+")
            self.root.bind_class(cls, "<ButtonRelease-1>", self._input_tap, add="+")

    def _input_press(self, event) -> None:
        self._tap_start = (event.widget, event.x_root, event.y_root)

    def _input_tap(self, event) -> None:
        start, self._tap_start = self._tap_start, None
        if not self.cfg.touch_keyboard or start is None or start[0] is not event.widget:
            return
        if abs(event.x_root - start[1]) > 12 or abs(event.y_root - start[2]) > 12:
            return  # Wischen/Markieren, kein Antippen
        w = event.widget
        try:
            state = str(w.cget("state"))
        except (tk.TclError, AttributeError):
            state = "normal"
        if state in ("disabled", "readonly"):
            return  # z. B. Auswahlliste ohne Texteingabe
        if w.winfo_class() == "Text" and not self.cfg.touch_keyboard_editor:
            return
        try:
            if "arrow" in str(w.identify(event.x, event.y)):
                return  # Pfeil einer Auswahlliste/eines Zahlenfelds angetippt
        except (tk.TclError, AttributeError):
            pass
        self.root.after(80, show_touch_keyboard)

    # ======================================================================
    # Senden / Empfangen
    # ======================================================================

    def send(self) -> None:
        if self.busy:
            return
        text = self.editor.get_text()
        if not text.strip():
            dialogs.info(self.root, self.fonts, "Nichts zu senden",
                         "Es ist kein Programm geladen. Bitte zuerst eine Datei öffnen.")
            return
        m = self.machine
        problems = find_problem_chars(text)
        if problems:
            lines = ", ".join(str(n) for n, _ in problems[:8]) + (" …" if len(problems) > 8 else "")
            chars = " ".join(sorted({c if c.isprintable() else "U+{:04X}".format(ord(c)) for _, c in problems}))
            if not dialogs.confirm(
                    self.root, self.fonts, "Sonderzeichen im Programm",
                    "In Zeile {} stehen Zeichen, die viele Steuerungen nicht kennen:  {}\n\n"
                    "Die Steuerung meldet dann evtl. einen Alarm. Trotzdem senden?".format(lines, chars),
                    yes="Trotzdem senden", icon="warning"):
                return
        chunks = build_send_chunks(
            text, eol=m.send_eol, frame_percent=m.frame_percent, strip_trailing_spaces=m.strip_trailing_spaces,
            skip_empty_lines=m.skip_empty_lines, leader_nuls=m.leader_nuls, trailer_nuls=m.trailer_nuls)
        if not self._connect(show_error=True):
            return
        try:
            self.link.start_send(chunks, m)
        except SerialError as exc:
            dialogs.error(self.root, self.fonts, "Senden nicht möglich", str(exc))
            return
        total = sum(len(c) for c in chunks)
        self._begin_transfer("send", total=total, lines=len(chunks))

    def receive(self) -> None:
        if self.busy:
            return
        if not self._confirm_discard():
            return
        if not self._connect(show_error=True):
            return
        m = self.machine
        try:
            self.link.start_receive(m)
        except SerialError as exc:
            dialogs.error(self.root, self.fonts, "Empfang nicht möglich", str(exc))
            return
        self.editor.set_text("")
        self.current_path = None
        self.origin = "received"
        self.dirty = False
        self._begin_transfer("receive")
        self._reanalyze()

    def cancel_transfer(self) -> None:
        if not self.transfer:
            return
        self.transfer["cancelled"] = True
        self.transfer_btn.set_enabled(False)
        self.transfer_detail.configure(text="Wird beendet …", fg=C["muted"])
        self.link.cancel()

    def _begin_transfer(self, kind: str, total: int = 0, lines: int = 0) -> None:
        hide_touch_keyboard()  # Platz für die Fortschrittsanzeige
        m = self.machine
        self.transfer = {"kind": kind, "start": time.monotonic(), "sent": 0, "total": total, "line": 0,
                         "lines": lines, "cancelled": False, "rx_bytes": 0, "text_buf": []}
        self._set_busy(True)
        self.status_frame.grid_remove()
        self.transfer_frame.grid(row=0, column=0, sticky="ew")
        self.transfer_btn.set_enabled(True)
        if kind == "send":
            self.transfer_title.configure(text="Sende an {} …".format(m.display_name))
            self.transfer_btn.set_text("Abbrechen", icon="■")
            self.transfer_btn.set_kind("danger")
            self.progress.configure(style="Transfer.Horizontal.TProgressbar", mode="determinate", value=0)
            self.transfer_pct.configure(text="0 %")
        else:
            self.transfer_title.configure(text="Empfange von {} …".format(m.display_name))
            self.transfer_btn.set_text("Empfang beenden", icon="■")
            self.transfer_btn.set_kind("primary")
            self.progress.configure(style="Receive.Horizontal.TProgressbar", mode="indeterminate", value=0)
            self.progress.start(20)
            self.transfer_pct.configure(text="")
        self._update_transfer_detail()

    def _end_transfer(self) -> None:
        self.progress.stop()
        self.transfer_frame.grid_remove()
        self.status_frame.grid(row=0, column=0, sticky="ew")
        self.transfer = None
        self._set_busy(False)

    def _set_busy(self, busy: bool) -> None:
        for b in (self.send_btn, self.recv_btn, self.open_btn, self.save_btn, self.saveas_btn, self.new_btn,
                  self.compact_btn, self.machine_btn, self.machines_btn, self.connect_btn):
            b.set_enabled(not busy)
        self.undo_btn.state(["disabled"] if busy else ["!disabled"])
        self.editor.set_readonly(busy)

    def _update_transfer_detail(self) -> None:
        t = self.transfer
        if not t or t.get("cancelled"):
            return
        now = time.monotonic()
        elapsed = fmt_duration(now - t["start"])
        idle = now - self.link.last_activity
        if t["kind"] == "send":
            pct = 100.0 * t["sent"] / t["total"] if t["total"] else 0.0
            self.progress.configure(value=pct)
            self.transfer_pct.configure(text="{} %".format(int(pct)))
            detail = "Zeile {} von {}  ·  {} von {} Bytes  ·  {}".format(
                fmt_int(t["line"]), fmt_int(t["lines"]), fmt_int(t["sent"]), fmt_int(t["total"]), elapsed)
            color = C["muted"]
            if idle > 2.5 and t["sent"] < t["total"]:
                lines = self.link.modem_lines() or {}
                why = ""
                if self.machine.flow == "rtscts" and not lines.get("CTS", True):
                    why = " (CTS aus)"
                elif self.machine.flow == "dsrdtr" and not lines.get("DSR", True):
                    why = " (DSR aus)"
                detail = "Warte auf Maschine{} – ist die Steuerung auf Einlesen (READ) gestellt?  ·  {}".format(
                    why, elapsed)
                color = C["warn"]
            self.transfer_detail.configure(text=detail, fg=color)
        else:
            if t["rx_bytes"] == 0:
                self.transfer_detail.configure(
                    text="Bereit – jetzt an der Steuerung die Ausgabe starten (PUNCH).  ·  {}".format(elapsed),
                    fg=C["primary"])
            else:
                if now - self._last_analyze > 1.0:
                    self._reanalyze()
                lines = self.editor.line_count()
                detail = "{} Zeilen  ·  {} Bytes  ·  {}".format(fmt_int(lines), fmt_int(t["rx_bytes"]), elapsed)
                timeout = self.machine.idle_timeout_s
                if timeout > 0 and idle > 1.0:
                    detail += "  ·  Ende in {} s, falls nichts mehr kommt".format(max(0, int(timeout - idle + 0.99)))
                self.transfer_detail.configure(text=detail, fg=C["muted"])

    def _on_transfer_done(self, kind: str, ok: bool, message: str) -> None:
        t = self.transfer or {}
        cancelled = bool(t.get("cancelled"))
        self._flush_rx_text()
        self._end_transfer()
        if kind == "send":
            if ok:
                self.set_status("✓  " + message, "ok")
            elif cancelled:
                self.set_status(message, "warn")
            else:
                self.set_status(message, "error")
                dialogs.error(self.root, self.fonts, "Senden fehlgeschlagen", message)
                self._update_connection_after_error()
            return

        # Empfang
        has_text = not self.editor.is_empty()
        self.editor.clear_undo()
        if has_text:
            self.dirty = True
        self._reanalyze()
        if ok and has_text:
            if self.machine.auto_save_received:
                if self.save(silent=True):
                    self.set_status("✓  {}  Automatisch gespeichert: {}".format(
                        message, os.path.basename(self.current_path or "")), "ok")
                return
            found = "  Erkannt: {}.".format(self.doc_info.name) if self.doc_info.name else ""
            self.set_status("✓  {}{}  Jetzt „Speichern“ antippen.".format(message, found), "ok")
        elif not has_text:
            self.set_status(message, "warn")
        else:
            self.set_status(message, "error")
            dialogs.error(self.root, self.fonts, "Empfang unterbrochen", message,
                          detail="Die bis dahin empfangenen Daten stehen im Editor.")
            self._update_connection_after_error()

    def _update_connection_after_error(self) -> None:
        # Nach einem Fehler (z. B. USB-Adapter gezogen) Port schließen, damit er neu geöffnet wird
        self.link.close()
        self._update_connection()

    def _flush_rx_text(self) -> None:
        t = self.transfer
        if t and t["text_buf"]:
            self.editor.append("".join(t["text_buf"]))
            t["text_buf"] = []
            # Name sofort zeigen, sobald der Programmkopf da ist (solange der Text noch kurz ist)
            if self.doc_info.name is None and t["rx_bytes"] < 20000:
                self._reanalyze()

    # ======================================================================
    # Ereignisschleife
    # ======================================================================

    def _poll(self) -> None:
        now = time.monotonic()
        try:
            for _ in range(1000):
                ev = self.events.get_nowait()
                self._handle_event(ev, now)
        except queue.Empty:
            pass
        self._flush_rx_text()
        if self.transfer:
            self._update_transfer_detail()
        self.leds["TX"].set(now < self.tx_until)
        self.leds["RX"].set(now < self.rx_until)
        if now - self._last_led_poll > 0.25:
            self._last_led_poll = now
            lines = self.link.modem_lines()
            if lines is not None:
                for name in ("CTS", "DSR", "CD", "RI"):
                    self.leds[name].set(lines[name])
        self._poll_job = self.root.after(POLL_MS, self._poll)

    def _handle_event(self, ev, now: float) -> None:
        kind = ev[0]
        t = self.transfer
        if kind == "text" and t is not None:
            t["text_buf"].append(ev[1])
        elif kind == "rx":
            self.rx_until = now + 0.15
            if t is not None:
                t["rx_bytes"] += ev[1]
        elif kind == "tx":
            self.tx_until = now + 0.15
        elif kind == "progress" and t is not None:
            t["sent"], t["total"], t["line"], t["lines"] = ev[1], ev[2], ev[3], ev[4]
        elif kind == "done":
            self._on_transfer_done(ev[1], ev[2], ev[3])

    # ======================================================================
    # Dateien
    # ======================================================================

    def refresh_files(self, rescan: bool = False) -> None:
        """Dateiliste neu aufbauen: Kundenordner oben, darunter die Programme."""
        if not hasattr(self, "file_tree"):
            return
        if rescan:
            self._walk_cache = None
        root = self.machine.program_folder()
        if (self.browse_dir is None or not is_inside(root, self.browse_dir)
                or not os.path.isdir(self.browse_dir)):
            self.browse_dir = root
        self.folder_label.configure(text=root)
        tree = self.file_tree
        tree.delete(*tree.get_children())
        self._file_paths = {}
        self._dir_paths = {}
        cur = os.path.normcase(os.path.abspath(self.current_path)) if self.current_path else None
        flt = self._filter_text()

        if flt:
            # Suche über alle Kundenordner – Treffer nach Ordner gruppiert
            self.up_btn.state(["disabled"])
            hits = search(self._all_files(root), flt)
            self.crumb_label.configure(text="{} Treffer".format(fmt_int(len(hits))))
            if not hits:
                tree.insert("", "end", text="Keine Treffer", values=("",), tags=("hint",))
                return
            groups: Dict[str, list] = {}
            for h in hits[:2000]:
                groups.setdefault(h.folder, []).append(h)
            for folder in sorted(groups, key=lambda g: (g != "", g.lower())):
                items = groups[folder]
                label = folder or "Hauptordner"
                fid = tree.insert("", "end", text=label, image=self._folder_icon,
                                  values=("{}  ›".format(len(items)),), tags=("folder",))
                self._dir_paths[fid] = os.path.dirname(items[0].path)
                items.sort(key=lambda h: h.name.lower() if self.sort_by == "name" else -h.mtime)
                for h in items:
                    self._insert_file("  " + h.name, h.path, self._fmt_date(h.mtime), cur)
            return

        # normaler Ordner
        at_root = os.path.normcase(os.path.abspath(self.browse_dir)) == os.path.normcase(os.path.abspath(root))
        self.up_btn.state(["disabled"] if at_root else ["!disabled"])
        crumb = os.path.basename(root.rstrip("\\/")) or root
        sub = rel_label(root, self.browse_dir)
        self.crumb_label.configure(text=crumb + (" › " + sub if sub else ""))
        if not os.path.isdir(self.browse_dir):
            tree.insert("", "end", text="Noch keine Programme", values=("",), tags=("hint",))
            return
        dirs, files = list_folder(self.browse_dir)
        for d in dirs:
            iid = tree.insert("", "end", text=d.name, image=self._folder_icon,
                              values=("{}  ›".format(count_files(d.path)),), tags=("folder",))
            self._dir_paths[iid] = d.path
        if self.sort_by == "name":
            files.sort(key=lambda e: e.name.lower())
        else:
            files.sort(key=lambda e: -e.mtime)
        for e in files[:3000]:
            self._insert_file(e.name, e.path, self._fmt_date(e.mtime), cur)
        if not dirs and not files:
            tree.insert("", "end", text="Noch keine Programme", values=("",), tags=("hint",))

    def _insert_file(self, name: str, path: str, second: str, cur: Optional[str]) -> None:
        is_cur = cur is not None and os.path.normcase(os.path.abspath(path)) == cur
        iid = self.file_tree.insert("", "end", text=name, image=self._file_icon, values=(second,),
                                    tags=("current",) if is_cur else ())
        self._file_paths[iid] = path
        if is_cur:
            self.file_tree.selection_set(iid)
            self.file_tree.see(iid)

    @staticmethod
    def _fmt_date(mtime: float) -> str:
        dt = datetime.datetime.fromtimestamp(mtime)
        today = datetime.date.today()
        if dt.date() == today:
            return dt.strftime("%H:%M")
        if dt.year == today.year:
            return dt.strftime("%d.%m.")
        return dt.strftime("%d.%m.%y")

    def _all_files(self, root: str):
        """Alle Dateien für die Suche – kurz zwischengespeichert, damit das Tippen flüssig bleibt."""
        now = time.monotonic()
        c = self._walk_cache
        if c is None or c[0] != root or now - c[1] > 30:
            c = (root, now, walk_files(root))
            self._walk_cache = c
        return c[2]

    def set_sort(self, by: str) -> None:
        """Überschrift antippen: nach Name (A–Z) oder Datum (neueste zuerst) sortieren."""
        self.sort_by = by
        self.file_tree.heading("#0", text="NAME ▴" if by == "name" else "NAME")
        self.file_tree.heading("date", text="DATUM ▾" if by == "date" else "DATUM")
        self.refresh_files()

    def open_folder(self, path: str) -> None:
        self.browse_dir = path
        self.refresh_files()
        self.file_tree.yview_moveto(0)

    def folder_up(self) -> None:
        root = self.machine.program_folder()
        if self.browse_dir and os.path.normcase(os.path.abspath(self.browse_dir)) != \
                os.path.normcase(os.path.abspath(root)):
            self.open_folder(os.path.dirname(os.path.abspath(self.browse_dir)))

    def change_folder(self) -> None:
        if self.busy:
            return
        m = self.machine
        start = m.program_folder()
        if not os.path.isdir(start):
            start = os.path.expanduser("~")
        path = filedialog.askdirectory(parent=self.root, initialdir=start,
                                       title="Programmordner für {}".format(m.display_name))
        if path:
            m.folder = os.path.normpath(path)
            self.browse_dir = m.folder
            self._save_config()
            self.refresh_files(rescan=True)
            self._update_title()

    def _tree_press(self, event) -> None:
        self._tree_drag = {"y": event.y, "start": event.y, "acc": 0.0, "moved": False}

    def _tree_motion(self, event) -> str:
        d = self._tree_drag
        if d is None:
            return "break"
        if abs(event.y - d["start"]) > px(self.root, 10):
            d["moved"] = True
        if d["moved"]:
            d["acc"] += event.y - d["y"]
            row_h = px(self.root, 40)
            steps = int(d["acc"] / row_h)
            if steps:
                self.file_tree.yview_scroll(-steps, "units")
                d["acc"] -= steps * row_h
        d["y"] = event.y
        return "break"

    def _tree_release(self, event) -> None:
        d = self._tree_drag
        self._tree_drag = None
        if d is None or d["moved"]:
            return
        iid = self.file_tree.identify_row(event.y)
        if iid and iid in self._dir_paths:
            path = self._dir_paths[iid]
            self.root.after_idle(lambda: self.open_folder(path))
        elif iid and iid in self._file_paths:
            self.file_tree.selection_set(iid)
            self.root.after_idle(lambda: self.open_file(self._file_paths.get(iid)))

    def _open_selected_file(self) -> None:
        sel = self.file_tree.selection()
        if sel and sel[0] in self._dir_paths:
            self.open_folder(self._dir_paths[sel[0]])
        elif sel and sel[0] in self._file_paths:
            self.open_file(self._file_paths[sel[0]])

    def open_file(self, path: Optional[str] = None) -> None:
        if self.busy:
            return
        if path is not None and self.current_path and os.path.abspath(path) == os.path.abspath(self.current_path):
            return
        if not self._confirm_discard():
            self.refresh_files()
            return
        if path is None:
            folder = self.browse_dir or self.machine.program_folder()
            path = filedialog.askopenfilename(parent=self.root, filetypes=FILE_TYPES,
                                              initialdir=folder if os.path.isdir(folder) else os.path.expanduser("~"),
                                              title="Programm öffnen")
            if not path:
                return
        try:
            text = load_program_file(path)
        except OSError as exc:
            dialogs.error(self.root, self.fonts, "Datei kann nicht geöffnet werden", str(exc))
            return
        self.editor.set_text(text)
        self.current_path = os.path.normpath(path)
        self.origin = "file"
        self.dirty = False
        self._reanalyze()
        self.refresh_files()
        self.set_status("Geöffnet: {}  ({} Zeilen)".format(os.path.basename(path), fmt_int(count_lines(text))))

    def save(self, silent: bool = False) -> bool:
        """Bekannte Datei überschreiben; sonst Speichern-Fenster mit erkanntem Namen.

        ``silent`` (automatisches Speichern nach dem Empfang): ohne Fenster, mit
        erkanntem Namen; eine vorhandene Datei wird dabei nie überschrieben.
        """
        if self.busy:
            return False
        text = self.editor.get_text()
        if not text.strip():
            if not silent:
                dialogs.info(self.root, self.fonts, "Nichts zu speichern", "Der Editor ist leer.")
            return False
        if self.current_path:
            return self._write(self.current_path)
        if not silent:
            return self._save_with_dialog()
        folder, _why = suggest_folder(self.machine.program_folder(), analyze_program(text).name, self.browse_dir)
        return self._write(self._auto_path(folder, text))

    def save_as(self) -> bool:
        if self.busy:
            return False
        return self._save_with_dialog()

    def _suggested_name(self) -> str:
        return self.doc_info.name or "Programm_" + datetime.datetime.now().strftime("%Y-%m-%d_%H%M")

    def _auto_path(self, folder: str, text: str) -> str:
        ext = self.machine.file_ext or ""
        stamp = datetime.datetime.now()
        base = analyze_program(text).name or "Programm_" + stamp.strftime("%Y-%m-%d_%H%M")
        path = os.path.join(folder, base + ext)
        if os.path.exists(path):
            path = os.path.join(folder, "{}_{}{}".format(base, stamp.strftime("%Y-%m-%d_%H%M%S"), ext))
        return path

    def _save_with_dialog(self) -> bool:
        text = self.editor.get_text()
        if not text.strip():
            dialogs.info(self.root, self.fonts, "Nichts zu speichern", "Der Editor ist leer.")
            return False
        self._reanalyze()
        m = self.machine
        if self.current_path:
            folder = os.path.dirname(self.current_path)
            base, ext = os.path.splitext(os.path.basename(self.current_path))
            title = "Speichern unter"
            why = ""
        else:
            base, ext = self._suggested_name(), m.file_ext
            folder, why = suggest_folder(m.program_folder(), self.doc_info.name, self.browse_dir)
            title = "Programm speichern"
        detected = self.doc_info.summary() if self.doc_info.name else ""
        path = dialogs.SaveDialog(self.root, self.fonts, base, ext, folder, detected=detected, title=title,
                                  root=m.program_folder(), folder_reason=why).show()
        if not path:
            return False
        new_ext = os.path.splitext(path)[1]
        if not self.current_path and new_ext and new_ext.lower() != (m.file_ext or "").lower():
            m.file_ext = new_ext  # gewählte Endung für diese Maschine merken
            self._save_config()
        return self._write(path)

    def _write(self, path: str) -> bool:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            save_program_file(path, self.editor.get_text())
        except OSError as exc:
            dialogs.error(self.root, self.fonts, "Speichern fehlgeschlagen", str(exc))
            return False
        self.current_path = os.path.normpath(path)
        self.origin = "file"
        self.dirty = False
        self._update_title()
        # Liste zeigt den Ordner, in dem gespeichert wurde
        if is_inside(self.machine.program_folder(), path):
            self.browse_dir = os.path.dirname(os.path.abspath(path))
        self.refresh_files(rescan=True)
        self.set_status("✓  Gespeichert: " + path, "ok")
        return True

    def new_document(self) -> None:
        if self.busy or not self._confirm_discard():
            return
        self.editor.set_text("")
        self.current_path = None
        self.origin = "new"
        self.dirty = False
        self._reanalyze()
        self.refresh_files()
        self.editor.focus_editor()

    def _confirm_discard(self) -> bool:
        if not self.dirty or self.editor.is_empty():
            return True
        res = dialogs.ask(self.root, self.fonts, "Nicht gespeichert",
                          "Das aktuelle Programm wurde noch nicht gespeichert. Was soll damit passieren?",
                          [("cancel", "Abbrechen", "neutral"), ("discard", "Verwerfen", "danger"),
                           ("save", "Speichern", "primary")], icon="warning")
        if res == "save":
            return self.save()
        return res == "discard"

    # ======================================================================
    # Editor / Ansicht
    # ======================================================================

    def _on_editor_change(self) -> None:
        if not self.dirty and not self.busy:
            self.dirty = True
            self._update_title()
        # Programmname/Inhalt neu bestimmen, sobald eine Weile nicht getippt wird
        if self._analyze_job is not None:
            self.root.after_cancel(self._analyze_job)
        self._analyze_job = self.root.after(700, self._reanalyze)

    def compact(self) -> None:
        """Leerzeilen entfernen, vor jedem Werkzeugaufruf zwei Leerzeilen (rückgängig machbar)."""
        if self.busy:
            return
        text = self.editor.get_text()
        if not text.strip():
            dialogs.info(self.root, self.fonts, "Nichts zu bearbeiten", "Der Editor ist leer.")
            return
        dialect = analyze_program(text).dialect
        r = compact_program(text, dialect=dialect)
        if r.text == text:
            self.set_status("Das Programm ist schon zusammengerückt.", "info")
            return
        self.editor.replace_all(r.text)
        self.dirty = True
        self._reanalyze()
        if dialect == "heidenhain":
            msg = "✓  {} Leerzeilen entfernt. Bei Heidenhain sind keine Leerzeilen erlaubt, deshalb kein Abstand " \
                  "vor Werkzeugen.".format(fmt_int(r.removed))
        else:
            msg = "✓  Zusammengerückt: {} Leerzeilen entfernt, Abstand vor {} Werkzeugen".format(
                fmt_int(r.removed), fmt_int(r.tool_gaps))
            if r.program_gaps:
                msg += " und {} Programmen".format(fmt_int(r.program_gaps))
            msg += "."
        self.set_status(msg + "  Zurück mit „↶ Rückgängig“.", "ok")

    def undo(self) -> None:
        if self.busy:
            return
        try:
            self.editor.text.edit_undo()
        except tk.TclError:
            self.set_status("Es gibt nichts mehr rückgängig zu machen.", "info")
            return
        self.editor.schedule_refresh()
        self._on_editor_change()

    def _reanalyze(self) -> None:
        """Name, Steuerung und enthaltene Programme ermitteln und anzeigen."""
        self._analyze_job = None
        self._last_analyze = time.monotonic()
        self.doc_info = analyze_program(self.editor.get_text())
        # Bei Sinumerik/Heidenhain sind Klammern keine Kommentare
        self.editor.set_paren_comments(self.doc_info.dialect not in ("sinumerik", "heidenhain"))
        n = len(self.doc_info.programs)
        if n > 1:
            self.progs_btn.configure(text="≡  {} Programme  ▾".format(n))
            if not self.progs_btn.winfo_manager():
                self.progs_btn.pack(side="right", padx=(px(self.root, 10), px(self.root, 14)),
                                    before=self._info_left)
        elif self.progs_btn.winfo_manager():
            self.progs_btn.pack_forget()
        self._update_title()

    def _show_program_menu(self) -> None:
        progs = self.doc_info.programs
        if not progs:
            return
        menu = tk.Menu(self.root, tearoff=0, font=self.fonts.list, bg=C["surface"], fg=C["text"],
                       activebackground=C["select"], activeforeground=C["text"], bd=1)
        for line, name, typ in progs:
            label = "{}{}    ·  Zeile {}".format(name, "  (" + typ + ")" if typ else "", fmt_int(line))
            menu.add_command(label=label, command=lambda ln=line: self.editor.goto_line(ln, top=True))
        b = self.progs_btn
        try:
            menu.tk_popup(b.winfo_rootx(), b.winfo_rooty() + b.winfo_height())
        finally:
            menu.grab_release()

    def _on_cursor(self, line: int, col: int, total: int) -> None:
        total = max(total, line)
        self.cursor_label.configure(text="Zeile {} / {}  ·  Sp. {}".format(fmt_int(line), fmt_int(total), col))

    def _update_title(self) -> None:
        info = self.doc_info
        unsaved_receive = self.origin == "received" and not self.current_path
        if self.current_path:
            name = os.path.basename(self.current_path)
        elif unsaved_receive:
            name = info.name or "Empfangenes Programm"
        else:
            name = info.name or "Neues Programm"
        has_text = not self.editor.is_empty()
        self.doc_label.configure(text=name)
        self.dirty_label.configure(text="●  nicht gespeichert    " if self.dirty and has_text else "")
        parts = []
        if unsaved_receive:
            parts.append("Empfangen von " + self.machine.display_name)
        if info.dialect:
            parts.append(DIALECT_NAMES[info.dialect])
        self.doc_sub.configure(text="  ·  ".join(parts))
        self.root.title("{}{} – {}".format(name, " *" if self.dirty else "", APP_NAME))

        if self.current_path:
            self.save_btn.set_text(subtitle="Datei überschreiben")
        elif has_text:
            target = self._suggested_name() + (self.machine.file_ext or "")
            if len(target) > 26:
                target = target[:25] + "…"
            self.save_btn.set_text(subtitle="als " + target)
        else:
            self.save_btn.set_text(subtitle="mit Namen aus dem Programm")
        # Nach dem Empfang auffällig machen: hier geht es weiter
        self.save_btn.set_kind("primary" if unsaved_receive and self.dirty and has_text and not self.busy
                               else "neutral")

    def toggle_find(self, show: Optional[bool] = None) -> None:
        visible = bool(self.find_bar.winfo_manager())
        show = (not visible) if show is None else show
        if show:
            self.find_bar.grid(row=1, column=0, sticky="ew", pady=(0, px(self.root, 8)))
            self.find_entry.focus_set()
            self.find_entry.select_range(0, "end")
            self._find_changed()
        else:
            self.find_bar.grid_remove()
            self.editor.highlight_all("")
            self.editor.focus_editor()

    def _find_changed(self) -> None:
        term = self.find_var.get()
        n = self.editor.highlight_all(term)
        self.find_count.configure(text=("{} Treffer".format(fmt_int(n)) if term else ""),
                                  fg=C["muted"] if n or not term else C["error"])

    def find_next(self, backwards: bool = False) -> None:
        self.editor.find_next(self.find_var.get(), backwards=backwards)

    def zoom(self, delta: int) -> None:
        size = max(8, min(32, self.cfg.editor_font_size + delta))
        if size != self.cfg.editor_font_size:
            self.cfg.editor_font_size = size
            self.fonts.set_editor_size(size)
            self.editor.schedule_refresh()
            self._save_config()

    def toggle_file_panel(self) -> None:
        if self.file_panel.winfo_manager():
            self.file_panel.grid_remove()
            self.cfg.show_file_panel = False
        else:
            self.file_panel.grid()
            self.cfg.show_file_panel = True
            self.refresh_files()
        self._save_config()

    # ======================================================================
    # Sonstiges
    # ======================================================================

    def set_status(self, message: str, level: str = "info") -> None:
        icon, color = {"info": ("●", C["faint"]), "ok": ("●", C["ok"]), "warn": ("●", C["warn"]),
                       "error": ("●", C["error"])}.get(level, ("●", C["faint"]))
        self.status_icon.configure(text=icon, fg=color)
        self.status_text.configure(text=message, fg=C["error"] if level == "error" else C["text"])

    def _set_icon(self) -> None:
        try:
            from .icon import ICON_PNG_BASE64

            self._icon = tk.PhotoImage(master=self.root, data="".join(ICON_PNG_BASE64))
            self.root.iconphoto(True, self._icon)
        except tk.TclError:
            pass

    def _save_config(self) -> None:
        try:
            self.cfg.save()
        except OSError as exc:
            self.set_status("Einstellungen konnten nicht gespeichert werden: {}".format(exc), "error")

    def on_close(self) -> None:
        if self.busy:
            if not dialogs.confirm(self.root, self.fonts, "Übertragung läuft",
                                   "Die Übertragung abbrechen und das Programm beenden?", yes="Beenden",
                                   icon="warning", danger=True):
                return
            self.link.cancel()
        if not self._confirm_discard():
            return
        try:
            if self.root.state() == "normal":
                self.cfg.geometry = self.root.geometry()
        except tk.TclError:
            pass
        self._save_config()
        self.cancel_jobs()
        self.link.close()
        self.root.destroy()

    def cancel_jobs(self) -> None:
        """Geplante Hintergrund-Aufgaben stoppen (vor dem Schließen des Fensters)."""
        for job in (self._analyze_job, getattr(self, "_poll_job", None)):
            if job is not None:
                try:
                    self.root.after_cancel(job)
                except tk.TclError:
                    pass
        self._analyze_job = None
        self.editor.cancel_jobs()
