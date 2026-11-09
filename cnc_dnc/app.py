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
from .serial_link import SerialError, SerialLink
from .serial_link import serial as pyserial
from .textutil import (
    build_send_chunks,
    count_lines,
    detect_program_name,
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
        self.received_name: Optional[str] = None
        self.transfer: Optional[Dict] = None
        self.tx_until = 0.0
        self.rx_until = 0.0
        self._last_led_poll = 0.0
        self._file_paths: Dict[str, str] = {}
        self._tree_drag: Optional[Dict] = None

        root.title(APP_NAME)
        root.minsize(px(root, 960), px(root, 600))
        self._set_icon()
        self._build()
        self._bind_keys()
        self.apply_machine(connect=True)
        self._update_title()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(POLL_MS, self._poll)
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
            kb.pack(side="right", padx=(px(r, 10), 0))
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
        panel.grid_rowconfigure(3, weight=1)
        panel.grid_columnconfigure(0, weight=1)
        self.file_panel = panel

        top = tk.Frame(panel, bg=C["bg"])
        top.grid(row=0, column=0, sticky="ew")
        tk.Label(top, text="Programme", bg=C["bg"], fg=C["text"], font=f.heading).pack(side="left")
        ttk.Button(top, text="↻", style="Small.TButton", width=3, command=self.refresh_files).pack(side="right")

        self.folder_label = tk.Label(panel, text="", bg=C["bg"], fg=C["muted"], font=f.small, anchor="w",
                                     justify="left", wraplength=px(r, 310))
        self.folder_label.grid(row=1, column=0, sticky="ew", pady=(px(r, 2), px(r, 6)))

        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *a: self.refresh_files())
        search = ttk.Entry(panel, textvariable=self.filter_var, font=f.ui)
        search.grid(row=2, column=0, sticky="ew", pady=(0, px(r, 8)))
        self._placeholder(search, self.filter_var, "Suchen …")

        box = tk.Frame(panel, bg=C["surface"], highlightthickness=1, highlightbackground=C["border"])
        box.grid(row=3, column=0, sticky="nsew")
        box.grid_rowconfigure(0, weight=1)
        box.grid_columnconfigure(0, weight=1)
        self.file_tree = ttk.Treeview(box, columns=("name", "date"), show="headings", selectmode="browse")
        self.file_tree.heading("name", text="DATEI", anchor="w")
        self.file_tree.heading("date", text="DATUM", anchor="w")
        self.file_tree.column("name", width=px(r, 200), anchor="w", stretch=True)
        self.file_tree.column("date", width=px(r, 76), anchor="w", stretch=False)
        self.file_tree.tag_configure("hint", foreground=C["muted"])
        self.file_tree.tag_configure("current", font=f.ui_bold)
        sb = ttk.Scrollbar(box, orient="vertical", command=self.file_tree.yview)
        self.file_tree.configure(yscrollcommand=sb.set)
        self.file_tree.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        self.file_tree.bind("<ButtonPress-1>", self._tree_press)
        self.file_tree.bind("<B1-Motion>", self._tree_motion)
        self.file_tree.bind("<ButtonRelease-1>", self._tree_release)
        self.file_tree.bind("<Return>", lambda e: self._open_selected_file())

        ttk.Button(panel, text="Ordner ändern …", style="Small.TButton", command=self.change_folder).grid(
            row=4, column=0, sticky="ew", pady=(px(r, 8), 0))

    def _build_editor(self, body: tk.Frame) -> None:
        r = self.root
        f = self.fonts
        center = tk.Frame(body, bg=C["bg"])
        center.grid(row=0, column=1, sticky="nsew")
        center.grid_rowconfigure(2, weight=1)
        center.grid_columnconfigure(0, weight=1)

        info = tk.Frame(center, bg=C["bg"])
        info.grid(row=0, column=0, sticky="ew", pady=(0, px(r, 8)))
        self.doc_label = tk.Label(info, text="", bg=C["bg"], fg=C["text"], font=f.heading, anchor="w")
        self.doc_label.pack(side="left")
        self.dirty_label = tk.Label(info, text="", bg=C["bg"], fg=C["warn"], font=f.small_bold)
        self.dirty_label.pack(side="left", padx=(px(r, 10), 0))
        self.cursor_label = tk.Label(info, text="", bg=C["bg"], fg=C["muted"], font=f.ui)
        self.cursor_label.pack(side="right")

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
        r = self.root
        f = self.fonts
        side = tk.Frame(body, bg=C["bg"], width=px(r, 236))
        side.grid(row=0, column=2, sticky="nsew", padx=(px(r, 12), 0))
        side.grid_propagate(False)
        side.grid_columnconfigure(0, weight=1)
        side.grid_columnconfigure(1, weight=1)
        side.grid_columnconfigure(2, weight=1)
        gap = px(r, 8)
        row = 0

        def place(widget, columnspan=3, column=0, pady=(0, gap), padx=0):
            nonlocal row
            widget.grid(row=row, column=column, columnspan=columnspan, sticky="ew", pady=pady, padx=padx)
            if column + columnspan >= 3:
                row += 1

        self.send_btn = ActionButton(side, f, "Senden", subtitle="PC  →  Maschine", icon="▶", kind="primary",
                                     big=True, command=self.send)
        place(self.send_btn)
        self.recv_btn = ActionButton(side, f, "Empfangen", subtitle="Maschine  →  PC", icon="◀", kind="success",
                                     big=True, command=self.receive)
        place(self.recv_btn, pady=(0, px(r, 18)))

        place(self._section(side, "DATEI"))
        self.open_btn = ActionButton(side, f, "Öffnen …", kind="neutral", compact=True, command=self.open_file)
        place(self.open_btn)
        self.save_btn = ActionButton(side, f, "Speichern", subtitle="", kind="neutral", compact=True,
                                     command=self.save)
        place(self.save_btn)
        self.saveas_btn = ActionButton(side, f, "Speichern unter …", kind="neutral", compact=True,
                                       command=self.save_as)
        place(self.saveas_btn)
        self.new_btn = ActionButton(side, f, "Neu / Leeren", kind="neutral", compact=True, command=self.new_document)
        place(self.new_btn, pady=(0, px(r, 18)))

        place(self._section(side, "ANSICHT"))
        self.find_btn = ActionButton(side, f, "Suchen", kind="neutral", compact=True, anchor="center",
                                     command=lambda: self.toggle_find())
        place(self.find_btn, columnspan=1, column=0, padx=(0, px(r, 4)))
        self.zoom_out_btn = ActionButton(side, f, "A−", kind="neutral", compact=True, anchor="center",
                                         command=lambda: self.zoom(-1))
        place(self.zoom_out_btn, columnspan=1, column=1, padx=px(r, 2))
        self.zoom_in_btn = ActionButton(side, f, "A+", kind="neutral", compact=True, anchor="center",
                                        command=lambda: self.zoom(1))
        place(self.zoom_in_btn, columnspan=1, column=2, padx=(px(r, 4), 0))
        self.panel_btn = ActionButton(side, f, "Dateiliste ein/aus", kind="neutral", compact=True,
                                      command=self.toggle_file_panel)
        place(self.panel_btn)

        place(tk.Frame(side, bg=C["bg"]), pady=0)
        side.grid_rowconfigure(row - 1, weight=1)
        self.machines_btn = ActionButton(side, f, "Maschinen …", subtitle="Liste & Schnittstellen-Einstellungen",
                                         kind="neutral", compact=True, command=self.choose_machine)
        place(self.machines_btn, pady=(0, 0))

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
        self.status_text = tk.Label(self.status_frame, text="Bereit.", bg=C["surface"], fg=C["text"], font=f.ui,
                                    anchor="w")
        self.status_text.pack(side="left", fill="x", expand=True)
        tk.Label(self.status_frame, text="Version " + APP_VERSION, bg=C["surface"], fg=C["faint"],
                 font=f.small).pack(side="right", padx=px(r, 12))

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
        if not open_touch_keyboard():
            self.set_status("Bildschirmtastatur konnte nicht gestartet werden.", "warn")

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
        self.received_name = None
        self.origin = "received"
        self.dirty = False
        self._update_title()
        self._begin_transfer("receive")

    def cancel_transfer(self) -> None:
        if not self.transfer:
            return
        self.transfer["cancelled"] = True
        self.transfer_btn.set_enabled(False)
        self.transfer_detail.configure(text="Wird beendet …", fg=C["muted"])
        self.link.cancel()

    def _begin_transfer(self, kind: str, total: int = 0, lines: int = 0) -> None:
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
                  self.machine_btn, self.machines_btn, self.connect_btn):
            b.set_enabled(not busy)
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
            name = detect_program_name(self.editor.get_text())
            self.received_name = name
        self._update_title()
        if ok and has_text:
            if self.machine.auto_save_received:
                if self.save(silent=True):
                    self.set_status("✓  {}  Automatisch gespeichert: {}".format(
                        message, os.path.basename(self.current_path or "")), "ok")
                return
            self.set_status("✓  {}  Jetzt „Speichern“ antippen.".format(message), "ok")
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
        self.root.after(POLL_MS, self._poll)

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

    def refresh_files(self) -> None:
        if not hasattr(self, "file_tree"):
            return
        folder = self.machine.program_folder()
        self.folder_label.configure(text=folder)
        tree = self.file_tree
        tree.delete(*tree.get_children())
        self._file_paths = {}
        entries = []
        try:
            for name in os.listdir(folder):
                path = os.path.join(folder, name)
                if os.path.isfile(path) and not name.startswith("."):
                    entries.append((os.path.getmtime(path), name, path))
        except OSError:
            tree.insert("", "end", values=("Noch keine Programme", ""), tags=("hint",))
            return
        flt = self._filter_text()
        if flt:
            entries = [e for e in entries if flt in e[1].lower()]
        entries.sort(reverse=True)
        if not entries:
            tree.insert("", "end", values=("Keine Treffer" if flt else "Noch keine Programme", ""), tags=("hint",))
            return
        today = datetime.date.today()
        cur = os.path.normcase(os.path.abspath(self.current_path)) if self.current_path else None
        for mtime, name, path in entries[:3000]:
            dt = datetime.datetime.fromtimestamp(mtime)
            if dt.date() == today:
                date = dt.strftime("%H:%M")
            elif dt.year == today.year:
                date = dt.strftime("%d.%m.")
            else:
                date = dt.strftime("%d.%m.%y")
            is_cur = cur is not None and os.path.normcase(os.path.abspath(path)) == cur
            iid = tree.insert("", "end", values=(name, date), tags=("current",) if is_cur else ())
            self._file_paths[iid] = path
            if is_cur:
                tree.selection_set(iid)
                tree.see(iid)

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
            self._save_config()
            self.refresh_files()
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
        if iid and iid in self._file_paths:
            self.file_tree.selection_set(iid)
            self.root.after_idle(lambda: self.open_file(self._file_paths.get(iid)))

    def _open_selected_file(self) -> None:
        sel = self.file_tree.selection()
        if sel and sel[0] in self._file_paths:
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
            folder = self.machine.program_folder()
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
        self.received_name = None
        self.origin = "file"
        self.dirty = False
        self._update_title()
        self.refresh_files()
        self.set_status("Geöffnet: {}  ({} Zeilen)".format(os.path.basename(path), fmt_int(count_lines(text))))

    def save(self, silent: bool = False) -> bool:
        """Speichern ohne Nachfrage: bekannte Datei überschreiben, sonst automatisch benennen."""
        if self.busy:
            return False
        text = self.editor.get_text()
        if not text.strip():
            if not silent:
                dialogs.info(self.root, self.fonts, "Nichts zu speichern", "Der Editor ist leer.")
            return False
        if self.current_path:
            return self._write(self.current_path)
        folder = self.machine.program_folder()
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as exc:
            dialogs.error(self.root, self.fonts, "Ordner nicht verfügbar", str(exc))
            return False
        return self._write(self._auto_path(folder, text))

    def _auto_path(self, folder: str, text: str) -> str:
        ext = self.machine.file_ext or ""
        stamp = datetime.datetime.now()
        base = detect_program_name(text) or "Programm_" + stamp.strftime("%Y-%m-%d_%H%M")
        path = os.path.join(folder, base + ext)
        if os.path.exists(path):
            path = os.path.join(folder, "{}_{}{}".format(base, stamp.strftime("%Y-%m-%d_%H%M%S"), ext))
        return path

    def save_as(self) -> bool:
        if self.busy:
            return False
        text = self.editor.get_text()
        folder = os.path.dirname(self.current_path) if self.current_path else self.machine.program_folder()
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError:
            folder = os.path.expanduser("~")
        if self.current_path:
            initial = os.path.basename(self.current_path)
        else:
            initial = os.path.basename(self._auto_path(folder, text))
        ext = self.machine.file_ext or ".nc"
        path = filedialog.asksaveasfilename(parent=self.root, initialdir=folder, initialfile=initial,
                                            defaultextension=ext, filetypes=FILE_TYPES, title="Speichern unter")
        if not path:
            return False
        return self._write(path)

    def _write(self, path: str) -> bool:
        try:
            save_program_file(path, self.editor.get_text())
        except OSError as exc:
            dialogs.error(self.root, self.fonts, "Speichern fehlgeschlagen", str(exc))
            return False
        self.current_path = os.path.normpath(path)
        self.origin = "file"
        self.dirty = False
        self._update_title()
        self.refresh_files()
        self.set_status("✓  Gespeichert: " + path, "ok")
        return True

    def new_document(self) -> None:
        if self.busy or not self._confirm_discard():
            return
        self.editor.set_text("")
        self.current_path = None
        self.origin = "new"
        self.dirty = False
        self._update_title()
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

    def _on_cursor(self, line: int, col: int, total: int) -> None:
        self.cursor_label.configure(text="Zeile {} von {}   ·   Spalte {}".format(fmt_int(line), fmt_int(total), col))

    def _update_title(self) -> None:
        if self.current_path:
            name = os.path.basename(self.current_path)
        elif self.origin == "received":
            name = "Empfangenes Programm"
            if self.received_name and not self.busy:
                name += " – " + self.received_name
        else:
            name = "Neues Programm"
        self.doc_label.configure(text=name)
        self.dirty_label.configure(text="●  nicht gespeichert" if self.dirty and not self.editor.is_empty() else "")
        self.root.title("{}{} – {}".format(name, " *" if self.dirty else "", APP_NAME))
        if self.current_path:
            self.save_btn.set_text(subtitle="Datei überschreiben")
        else:
            self.save_btn.set_text(subtitle="neu im Maschinenordner")

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
        self.link.close()
        self.root.destroy()
