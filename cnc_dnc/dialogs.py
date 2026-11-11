"""Dialoge: Rückfragen, Maschinenliste und Maschinen-Einstellungen."""

from __future__ import annotations

import datetime
import os
import re
import tkinter as tk
from tkinter import filedialog, ttk
from typing import Callable, Dict, Optional, Sequence, Tuple

from .config import (
    BAUDRATES,
    CONTROL_PRESETS,
    CONTROLS,
    FLOW_CONTROLS,
    PARITIES,
    AppConfig,
    MachineProfile,
)
from .serial_link import list_ports
from .files import subfolders
from .system import IS_WIN, hide_touch_keyboard, toggle_touch_keyboard
from .textutil import END_MODES
from .theme import C, Fonts, px


class Dialog(tk.Toplevel):
    """Modaler Dialog, zentriert über dem Hauptfenster."""

    closes_keyboard = True  # Bildschirmtastatur beim Schließen ausblenden

    def __init__(self, parent: tk.Misc, fonts: Fonts, title: str) -> None:
        super().__init__(parent)
        self.withdraw()
        self.parent = parent.winfo_toplevel()
        self.fonts = fonts
        self.result = None
        self.title(title)
        self.configure(bg=C["bg"])
        self.transient(self.parent)
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.bind("<Escape>", lambda e: self.cancel())

    def cancel(self) -> None:
        self.result = None
        self.destroy()

    def destroy(self) -> None:
        # Eine für diesen Dialog geöffnete Bildschirmtastatur wieder ausblenden
        if self.closes_keyboard:
            hide_touch_keyboard()
        super().destroy()

    def show(self):
        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        pw, ph = self.parent.winfo_width(), self.parent.winfo_height()
        px0, py0 = self.parent.winfo_rootx(), self.parent.winfo_rooty()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        x = max(0, min(sw - w, px0 + (pw - w) // 2))
        y = max(0, min(sh - h - 40, py0 + (ph - h) // 3))
        self.geometry("+{}+{}".format(x, y))
        self.deiconify()
        try:
            self.wait_visibility()
            self.grab_set()
        except tk.TclError:
            pass
        self.lift()
        self.focus_force()
        self.wait_window()
        return self.result


# --------------------------------------------------------------------------
# Rückfrage / Meldung
# --------------------------------------------------------------------------

ICONS = {
    "info": ("i", C["primary"]),
    "question": ("?", C["primary"]),
    "warning": ("!", C["warn"]),
    "error": ("✕", C["error"]),
    "ok": ("✓", C["ok"]),
}


class MessageDialog(Dialog):
    closes_keyboard = False  # z. B. Fehlermeldung beim Ausfüllen: Tastatur bleibt offen
    def __init__(self, parent, fonts: Fonts, title: str, message: str,
                 buttons: Sequence[Tuple[str, str, str]], icon: str = "info", detail: str = "") -> None:
        super().__init__(parent, fonts, title)
        self.resizable(False, False)
        body = tk.Frame(self, bg=C["surface"])
        body.pack(fill="both", expand=True)
        pad = px(self, 24)

        sym, color = ICONS.get(icon, ICONS["info"])
        size = px(self, 44)
        cv = tk.Canvas(body, width=size, height=size, bg=C["surface"], highlightthickness=0)
        cv.create_oval(2, 2, size - 2, size - 2, fill=color, outline="")
        cv.create_text(size / 2, size / 2, text=sym, fill="#FFFFFF", font=fonts.title)
        cv.grid(row=0, column=0, rowspan=2, sticky="n", padx=(pad, px(self, 16)), pady=pad)

        tk.Label(body, text=title, bg=C["surface"], fg=C["text"], font=fonts.title, anchor="w",
                 justify="left").grid(row=0, column=1, sticky="w", pady=(pad, px(self, 6)), padx=(0, pad))
        tk.Label(body, text=message, bg=C["surface"], fg=C["text"], font=fonts.ui, anchor="w", justify="left",
                 wraplength=px(self, 520)).grid(row=1, column=1, sticky="nw", padx=(0, pad), pady=(0, pad))
        if detail:
            tk.Label(body, text=detail, bg=C["surface"], fg=C["muted"], font=fonts.small, anchor="w",
                     justify="left", wraplength=px(self, 520)).grid(row=2, column=1, sticky="nw", padx=(0, pad),
                                                                     pady=(0, pad))

        bar = tk.Frame(self, bg=C["bg"])
        bar.pack(fill="x")
        first = None
        for key, label, kind in reversed(list(buttons)):
            style = {"primary": "Primary.TButton", "danger": "Danger.TButton",
                     "success": "Success.TButton"}.get(kind, "TButton")
            b = ttk.Button(bar, text=label, style=style, command=lambda k=key: self._choose(k))
            b.pack(side="right", padx=(0, px(self, 12)), pady=px(self, 14))
            if kind in ("primary", "success", "danger") and first is None:
                first = b
        if first is not None:
            first.focus_set()
            self.bind("<Return>", lambda e: first.invoke())

    def _choose(self, key: str) -> None:
        self.result = key
        self.destroy()


def ask(parent, fonts: Fonts, title: str, message: str, buttons: Sequence[Tuple[str, str, str]],
        icon: str = "question", detail: str = "") -> Optional[str]:
    return MessageDialog(parent, fonts, title, message, buttons, icon=icon, detail=detail).show()


def info(parent, fonts: Fonts, title: str, message: str, icon: str = "info", detail: str = "") -> None:
    MessageDialog(parent, fonts, title, message, [("ok", "OK", "primary")], icon=icon, detail=detail).show()


def error(parent, fonts: Fonts, title: str, message: str, detail: str = "") -> None:
    info(parent, fonts, title, message, icon="error", detail=detail)


def confirm(parent, fonts: Fonts, title: str, message: str, yes: str = "Ja", no: str = "Abbrechen",
            icon: str = "question", danger: bool = False) -> bool:
    res = ask(parent, fonts, title, message, [("no", no, "neutral"), ("yes", yes, "danger" if danger else "primary")],
              icon=icon)
    return res == "yes"


# --------------------------------------------------------------------------
# Maschinenliste
# --------------------------------------------------------------------------

class MachineListDialog(Dialog):
    """Maschine auswählen, anlegen, bearbeiten, kopieren und löschen."""

    def __init__(self, parent, fonts: Fonts, config: AppConfig, on_changed: Callable[[], None]) -> None:
        super().__init__(parent, fonts, "Maschinen")
        self.config_obj = config
        self.on_changed = on_changed
        self.minsize(px(self, 960), px(self, 480))

        head = tk.Frame(self, bg=C["bg"])
        head.pack(fill="x", padx=px(self, 20), pady=(px(self, 16), px(self, 8)))
        tk.Label(head, text="Maschine auswählen", bg=C["bg"], fg=C["text"], font=fonts.title).pack(anchor="w")
        tk.Label(head, text="Antippen und „Auswählen“ drücken – oder doppelt antippen.", bg=C["bg"], fg=C["muted"],
                 font=fonts.ui).pack(anchor="w")

        main = tk.Frame(self, bg=C["bg"])
        main.pack(fill="both", expand=True, padx=px(self, 20), pady=(0, px(self, 16)))

        box = tk.Frame(main, bg=C["surface"], highlightthickness=1, highlightbackground=C["border"])
        box.pack(side="left", fill="both", expand=True)
        cols = ("name", "control", "serial")
        self.tree = ttk.Treeview(box, columns=cols, show="headings", selectmode="browse")
        self.tree.heading("name", text="MASCHINE", anchor="w")
        self.tree.heading("control", text="STEUERUNG", anchor="w")
        self.tree.heading("serial", text="SCHNITTSTELLE", anchor="w")
        self.tree.column("name", width=px(self, 220), anchor="w")
        self.tree.column("control", width=px(self, 140), anchor="w")
        self.tree.column("serial", width=px(self, 380), anchor="w")
        sb = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.tag_configure("active", font=fonts.ui_bold)
        self.tree.bind("<Double-1>", lambda e: self.choose())

        side = tk.Frame(main, bg=C["bg"])
        side.pack(side="right", fill="y", padx=(px(self, 16), 0))
        bw = 16
        ttk.Button(side, text="✓  Auswählen", style="Primary.TButton", width=bw, command=self.choose).pack(fill="x")
        ttk.Button(side, text="Bearbeiten …", width=bw, command=self.edit).pack(fill="x", pady=(px(self, 16), 0))
        ttk.Button(side, text="Neue Maschine …", width=bw, command=self.new).pack(fill="x", pady=(px(self, 8), 0))
        ttk.Button(side, text="Kopieren", width=bw, command=self.duplicate).pack(fill="x", pady=(px(self, 8), 0))
        ttk.Button(side, text="Löschen", width=bw, command=self.delete).pack(fill="x", pady=(px(self, 8), 0))
        ttk.Button(side, text="Schließen", width=bw, command=self.cancel).pack(side="bottom", fill="x")

        # Allgemeine Einstellungen (gelten für alle Maschinen)
        general = tk.Frame(self, bg=C["bg"])
        general.pack(fill="x", padx=px(self, 20), pady=(0, px(self, 16)), before=main)
        tk.Label(general, text="BILDSCHIRMTASTATUR", bg=C["bg"], fg=C["faint"], font=fonts.small_bold).pack(
            anchor="w")
        self.kb_var = tk.BooleanVar(value=config.touch_keyboard)
        self.kb_editor_var = tk.BooleanVar(value=config.touch_keyboard_editor)
        ttk.Checkbutton(general, text="Automatisch öffnen, wenn ein Eingabefeld angetippt wird",
                        variable=self.kb_var, command=self._kb_changed).pack(anchor="w")
        ttk.Checkbutton(general, text="Auch beim Antippen des Programm-Editors öffnen",
                        variable=self.kb_editor_var, command=self._kb_changed).pack(anchor="w")
        self._fill()

    def _kb_changed(self) -> None:
        self.config_obj.touch_keyboard = bool(self.kb_var.get())
        self.config_obj.touch_keyboard_editor = bool(self.kb_editor_var.get())
        try:
            self.config_obj.save()
        except OSError:
            pass

    def _fill(self, select: Optional[str] = None) -> None:
        self.tree.delete(*self.tree.get_children())
        active = self.config_obj.active_machine.id
        for m in self.config_obj.machines:
            self.tree.insert("", "end", iid=m.id, values=(m.display_name, m.control_name, m.serial_summary),
                             tags=("active",) if m.id == active else ())
        sel = select or active
        if self.tree.exists(sel):
            self.tree.selection_set(sel)
            self.tree.see(sel)

    def _selected(self) -> Optional[MachineProfile]:
        sel = self.tree.selection()
        return self.config_obj.machine_by_id(sel[0]) if sel else None

    def _changed(self, select: Optional[str] = None) -> None:
        self.config_obj.save()
        self._fill(select)
        self.on_changed()

    def choose(self) -> None:
        m = self._selected()
        if m is None:
            return
        self.result = m.id
        self.destroy()

    def edit(self) -> None:
        m = self._selected()
        if m is None:
            return
        res = MachineEditDialog(self, self.fonts, m).show()
        if res is not None:
            idx = self.config_obj.machines.index(m)
            self.config_obj.machines[idx] = res
            self._changed(res.id)

    def new(self) -> None:
        res = MachineEditDialog(self, self.fonts, MachineProfile(), is_new=True).show()
        if res is not None:
            self.config_obj.machines.append(res)
            self._changed(res.id)

    def duplicate(self) -> None:
        m = self._selected()
        if m is None:
            return
        c = m.copy()
        c.name = m.name + " (Kopie)"
        res = MachineEditDialog(self, self.fonts, c, is_new=True).show()
        if res is not None:
            self.config_obj.machines.append(res)
            self._changed(res.id)

    def delete(self) -> None:
        m = self._selected()
        if m is None:
            return
        if len(self.config_obj.machines) <= 1:
            info(self, self.fonts, "Nicht möglich", "Die letzte Maschine kann nicht gelöscht werden.")
            return
        if not confirm(self, self.fonts, "Maschine löschen?",
                       "„{}“ wirklich aus der Liste löschen? Programme im Ordner bleiben erhalten.".format(m.display_name),
                       yes="Löschen", icon="warning", danger=True):
            return
        self.config_obj.machines.remove(m)
        if self.config_obj.active_machine_id == m.id:
            self.config_obj.active_machine_id = self.config_obj.machines[0].id
        self._changed()


# --------------------------------------------------------------------------
# Maschinen-Einstellungen
# --------------------------------------------------------------------------

EOL_LABELS = {"CRLF": "CR + LF (Standard)", "LF": "Nur LF", "CR": "Nur CR"}
FILE_EXTS = [".nc", ".txt", ".cnc", ".h", ".mpf", ".tap", ".iso", ".prg"]


def _inverse(d: Dict[str, str]) -> Dict[str, str]:
    return {v: k for k, v in d.items()}


class MachineEditDialog(Dialog):
    def __init__(self, parent, fonts: Fonts, profile: MachineProfile, is_new: bool = False) -> None:
        super().__init__(parent, fonts, "Neue Maschine" if is_new else "Maschine bearbeiten")
        self.profile = profile
        p = profile
        self.v: Dict[str, tk.Variable] = {
            "number": tk.StringVar(value=p.number),
            "name": tk.StringVar(value=p.name),
            "control": tk.StringVar(value=CONTROLS.get(p.control, CONTROLS["generic"])),
            "folder": tk.StringVar(value=p.folder),
            "file_ext": tk.StringVar(value=p.file_ext),
            "port": tk.StringVar(value=p.port),
            "baudrate": tk.StringVar(value=str(p.baudrate)),
            "bytesize": tk.StringVar(value=str(p.bytesize)),
            "parity": tk.StringVar(value=PARITIES.get(p.parity, PARITIES["N"])),
            "stopbits": tk.StringVar(value="1.5" if p.stopbits == 1.5 else str(int(p.stopbits))),
            "flow": tk.StringVar(value=FLOW_CONTROLS.get(p.flow, FLOW_CONTROLS["none"])),
            "send_eol": tk.StringVar(value=EOL_LABELS.get(p.send_eol, EOL_LABELS["CRLF"])),
            "frame_percent": tk.BooleanVar(value=p.frame_percent),
            "strip_trailing_spaces": tk.BooleanVar(value=p.strip_trailing_spaces),
            "skip_empty_lines": tk.BooleanVar(value=p.skip_empty_lines),
            "leader_nuls": tk.StringVar(value=str(p.leader_nuls)),
            "trailer_nuls": tk.StringVar(value=str(p.trailer_nuls)),
            "char_delay_ms": tk.StringVar(value=str(p.char_delay_ms)),
            "line_delay_ms": tk.StringVar(value=str(p.line_delay_ms)),
            "end_mode": tk.StringVar(value=END_MODES.get(p.end_mode, END_MODES["none"])),
            "idle_timeout_s": tk.StringVar(value=_fmt_num(p.idle_timeout_s)),
            "strip_control_chars": tk.BooleanVar(value=p.strip_control_chars),
            "auto_save_received": tk.BooleanVar(value=p.auto_save_received),
        }

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=px(self, 16), pady=(px(self, 16), 0))
        self.tabs: Dict[str, tk.Frame] = {}
        for key, title in (("general", "Allgemein"), ("serial", "Schnittstelle"), ("send", "Senden"),
                           ("receive", "Empfangen")):
            f = tk.Frame(self.nb, bg=C["surface"], padx=px(self, 20), pady=px(self, 16))
            f.grid_columnconfigure(1, weight=1)
            self.nb.add(f, text=title)
            self.tabs[key] = f
        self._row = {k: 0 for k in self.tabs}

        # Allgemein
        self._entry("general", "Nummer", "number", width=8, hint="z. B. 78 – erscheint vor dem Namen")
        self._entry("general", "Name", "name", width=30)
        cb = self._combo("general", "Steuerung", "control", list(CONTROLS.values()))
        cb.bind("<<ComboboxSelected>>", self._apply_control_preset)
        self._entry("general", "Programmordner", "folder", width=36,
                    hint="Leer = " + p.program_folder() if not p.folder else "",
                    button=("Durchsuchen …", self._browse_folder))
        self._combo("general", "Dateiendung", "file_ext", FILE_EXTS, readonly=False, width=10)

        # Schnittstelle
        ports = list_ports()
        port_values = [x["device"] for x in ports]
        self.port_cb = self._combo("serial", "Anschluss (Port)", "port", port_values, readonly=False, width=14,
                                   button=("↻ Suchen", self._refresh_ports))
        self.port_hint = tk.Label(self.tabs["serial"], text="", bg=C["surface"], fg=C["muted"], font=fonts.small,
                                  anchor="w", justify="left")
        self.port_hint.grid(row=self._row["serial"], column=1, sticky="w", pady=(0, px(self, 6)))
        self._row["serial"] += 1
        self._port_descriptions = {x["device"]: x["description"] for x in ports}
        self.v["port"].trace_add("write", lambda *a: self._update_port_hint())
        self._update_port_hint()
        self._combo("serial", "Baudrate", "baudrate", [str(b) for b in BAUDRATES], readonly=False, width=10)
        self._combo("serial", "Datenbits", "bytesize", ["7", "8"], width=6)
        self._combo("serial", "Parität", "parity", list(PARITIES.values()), width=16)
        self._combo("serial", "Stoppbits", "stopbits", ["1", "1.5", "2"], width=6)
        self._combo("serial", "Handshake", "flow", list(FLOW_CONTROLS.values()), width=22)
        self._note("serial", "Die Werte müssen exakt zu den Schnittstellen-Parametern der Steuerung passen.")

        # Senden
        self._combo("send", "Zeilenende", "send_eol", list(EOL_LABELS.values()), width=20)
        self._check("send", "Programm mit % am Anfang und Ende einrahmen (Fanuc)", "frame_percent")
        self._check("send", "Leerzeichen am Zeilenende entfernen", "strip_trailing_spaces")
        self._check("send", "Leerzeilen nicht senden", "skip_empty_lines")
        self._spin("send", "Vorlauf (NUL-Zeichen)", "leader_nuls", 0, 2000)
        self._spin("send", "Nachlauf (NUL-Zeichen)", "trailer_nuls", 0, 2000)
        self._spin("send", "Pause je Zeichen (ms)", "char_delay_ms", 0, 1000,
                   hint="nur bei Maschinen ohne Handshake nötig")
        self._spin("send", "Pause je Zeile (ms)", "line_delay_ms", 0, 10000)

        # Empfangen
        self._combo("receive", "Programmende erkennen", "end_mode", list(END_MODES.values()), width=30)
        self._spin("receive", "Beenden nach … s ohne Daten", "idle_timeout_s", 0, 600,
                   hint="0 = nur von Hand beenden. Die Zeit läuft erst ab dem ersten Zeichen.")
        self._check("receive", "Steuerzeichen (NUL, XON/XOFF …) entfernen", "strip_control_chars")
        self._check("receive", "Empfangene Programme automatisch speichern", "auto_save_received")

        bar = tk.Frame(self, bg=C["bg"])
        bar.pack(fill="x", padx=px(self, 16), pady=px(self, 14))
        ttk.Button(bar, text="Speichern", style="Primary.TButton", command=self.save).pack(side="right")
        ttk.Button(bar, text="Abbrechen", command=self.cancel).pack(side="right", padx=(0, px(self, 12)))

    # -- Formular-Bausteine ----------------------------------------------------

    def _label(self, tab: str, text: str) -> int:
        r = self._row[tab]
        tk.Label(self.tabs[tab], text=text, bg=C["surface"], fg=C["text"], font=self.fonts.ui, anchor="w").grid(
            row=r, column=0, sticky="w", padx=(0, px(self, 18)), pady=px(self, 6))
        return r

    def _hint(self, tab: str, text: str) -> None:
        if not text:
            return
        tk.Label(self.tabs[tab], text=text, bg=C["surface"], fg=C["muted"], font=self.fonts.small, anchor="w",
                 justify="left", wraplength=px(self, 440)).grid(row=self._row[tab], column=1, sticky="w",
                                                                pady=(0, px(self, 6)))
        self._row[tab] += 1

    def _row_frame(self, tab: str, r: int) -> tk.Frame:
        f = tk.Frame(self.tabs[tab], bg=C["surface"])
        f.grid(row=r, column=1, sticky="w", pady=px(self, 6))
        self._row[tab] += 1
        return f

    def _entry(self, tab, label, key, width=20, hint="", button=None):
        r = self._label(tab, label)
        f = self._row_frame(tab, r)
        ttk.Entry(f, textvariable=self.v[key], width=width, font=self.fonts.ui).pack(side="left")
        if button:
            ttk.Button(f, text=button[0], style="Small.TButton", command=button[1]).pack(side="left",
                                                                                       padx=(px(self, 8), 0))
        self._hint(tab, hint)
        return f

    def _combo(self, tab, label, key, values, readonly=True, width=20, button=None, hint=""):
        r = self._label(tab, label)
        f = self._row_frame(tab, r)
        cb = ttk.Combobox(f, textvariable=self.v[key], values=values, width=width, font=self.fonts.ui,
                          state="readonly" if readonly else "normal")
        cb.pack(side="left")
        if button:
            ttk.Button(f, text=button[0], style="Small.TButton", command=button[1]).pack(side="left",
                                                                                       padx=(px(self, 8), 0))
        self._hint(tab, hint)
        return cb

    def _spin(self, tab, label, key, lo, hi, hint=""):
        r = self._label(tab, label)
        f = self._row_frame(tab, r)
        ttk.Spinbox(f, textvariable=self.v[key], from_=lo, to=hi, width=8, font=self.fonts.ui).pack(side="left")
        self._hint(tab, hint)

    def _check(self, tab, label, key):
        r = self._row[tab]
        ttk.Checkbutton(self.tabs[tab], text=label, variable=self.v[key], style="Surface.TCheckbutton").grid(
            row=r, column=0, columnspan=2, sticky="w", pady=px(self, 2))
        self._row[tab] += 1

    def _note(self, tab, text):
        tk.Label(self.tabs[tab], text=text, bg=C["surface"], fg=C["muted"], font=self.fonts.small, anchor="w",
                 justify="left", wraplength=px(self, 600)).grid(row=self._row[tab], column=0, columnspan=2,
                                                                sticky="w", pady=(px(self, 12), 0))
        self._row[tab] += 1

    # -- Aktionen --------------------------------------------------------------

    def _apply_control_preset(self, _e=None) -> None:
        key = _inverse(CONTROLS).get(self.v["control"].get(), "generic")
        preset = CONTROL_PRESETS.get(key, {})
        if "frame_percent" in preset:
            self.v["frame_percent"].set(preset["frame_percent"])
        if "end_mode" in preset:
            self.v["end_mode"].set(END_MODES[preset["end_mode"]])
        if "send_eol" in preset:
            self.v["send_eol"].set(EOL_LABELS[preset["send_eol"]])

    def _browse_folder(self) -> None:
        start = self.v["folder"].get() or self.profile.program_folder()
        if not os.path.isdir(start):
            start = os.path.expanduser("~")
        path = filedialog.askdirectory(parent=self, initialdir=start, title="Programmordner wählen")
        if path:
            self.v["folder"].set(os.path.normpath(path))

    def _refresh_ports(self) -> None:
        ports = list_ports()
        self._port_descriptions = {x["device"]: x["description"] for x in ports}
        self.port_cb.configure(values=[x["device"] for x in ports])
        self._update_port_hint()
        if not ports:
            self.port_hint.configure(text="Keine Schnittstelle gefunden – Adapter eingesteckt?")

    def _update_port_hint(self) -> None:
        dev = self.v["port"].get()
        desc = self._port_descriptions.get(dev)
        if desc and desc != dev:
            self.port_hint.configure(text=desc, fg=C["muted"])
        elif self._port_descriptions and dev not in self._port_descriptions:
            found = ", ".join(sorted(self._port_descriptions))
            self.port_hint.configure(text="Nicht gefunden. Vorhanden: " + found, fg=C["warn"])
        else:
            self.port_hint.configure(text="")

    def _int(self, key: str, label: str, tab: str, lo: int = 0) -> int:
        try:
            val = int(float(self.v[key].get().replace(",", ".")))
        except ValueError:
            raise _FormError(tab, "„{}“ muss eine Zahl sein.".format(label)) from None
        if val < lo:
            raise _FormError(tab, "„{}“ darf nicht kleiner als {} sein.".format(label, lo))
        return val

    def save(self) -> None:
        try:
            prof = self._collect()
        except _FormError as exc:
            self.nb.select(self.tabs[exc.tab])
            error(self, self.fonts, "Bitte prüfen", str(exc))
            return
        self.result = prof
        self.destroy()

    def _collect(self) -> MachineProfile:
        v = self.v
        name = v["name"].get().strip()
        if not name:
            raise _FormError("general", "Bitte einen Namen für die Maschine eingeben.")
        port = v["port"].get().strip()
        if not port:
            raise _FormError("serial", "Bitte einen Anschluss (z. B. COM1) eingeben.")
        try:
            idle = float(v["idle_timeout_s"].get().replace(",", "."))
            if idle < 0:
                raise ValueError
        except ValueError:
            raise _FormError("receive", "Die Wartezeit muss eine Zahl ≥ 0 sein.") from None
        ext = v["file_ext"].get().strip()
        if ext and not ext.startswith("."):
            ext = "." + ext
        stop = v["stopbits"].get()
        data = dict(
            id=self.profile.id,
            number=v["number"].get().strip(),
            name=name,
            control=_inverse(CONTROLS).get(v["control"].get(), "generic"),
            folder=v["folder"].get().strip(),
            file_ext=ext,
            port=port,
            baudrate=self._int("baudrate", "Baudrate", "serial", lo=50),
            bytesize=int(v["bytesize"].get()),
            parity=_inverse(PARITIES).get(v["parity"].get(), "N"),
            stopbits=1.5 if stop == "1.5" else int(stop),
            flow=_inverse(FLOW_CONTROLS).get(v["flow"].get(), "none"),
            send_eol=_inverse(EOL_LABELS).get(v["send_eol"].get(), "CRLF"),
            frame_percent=bool(v["frame_percent"].get()),
            strip_trailing_spaces=bool(v["strip_trailing_spaces"].get()),
            skip_empty_lines=bool(v["skip_empty_lines"].get()),
            leader_nuls=self._int("leader_nuls", "Vorlauf", "send"),
            trailer_nuls=self._int("trailer_nuls", "Nachlauf", "send"),
            char_delay_ms=self._int("char_delay_ms", "Pause je Zeichen", "send"),
            line_delay_ms=self._int("line_delay_ms", "Pause je Zeile", "send"),
            end_mode=_inverse(END_MODES).get(v["end_mode"].get(), "none"),
            idle_timeout_s=idle,
            strip_control_chars=bool(v["strip_control_chars"].get()),
            auto_save_received=bool(v["auto_save_received"].get()),
        )
        return MachineProfile.from_dict(data)


class _FormError(Exception):
    def __init__(self, tab: str, message: str) -> None:
        super().__init__(message)
        self.tab = tab


def _fmt_num(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else str(x)


# --------------------------------------------------------------------------
# Speichern mit vorgeschlagenem Namen
# --------------------------------------------------------------------------

_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class SaveDialog(Dialog):
    """Fragt den Dateinamen ab – vorbelegt mit dem im Programm erkannten Namen.

    Liefert den vollständigen Pfad oder None.
    """

    def __init__(self, parent, fonts: Fonts, name: str, ext: str, folder: str, detected: str = "",
                 title: str = "Programm speichern", root: Optional[str] = None, folder_reason: str = "") -> None:
        super().__init__(parent, fonts, title)
        self.resizable(False, False)
        self.folder = folder
        self.root_folder = root
        self.name_var = tk.StringVar(value=name)
        self.ext_var = tk.StringVar(value=ext)
        bg = C["surface"]
        pad = px(self, 24)

        body = tk.Frame(self, bg=bg, padx=pad, pady=px(self, 20))
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(0, weight=1)
        tk.Label(body, text=title, bg=bg, fg=C["text"], font=fonts.title, anchor="w").grid(
            row=0, column=0, sticky="w")
        if detected:
            tk.Label(body, text="✓  Im Programm erkannt:  " + detected, bg=bg, fg=C["ok"], font=fonts.ui,
                     anchor="w").grid(row=1, column=0, sticky="w", pady=(px(self, 4), 0))

        tk.Label(body, text="Dateiname", bg=bg, fg=C["text"], font=fonts.ui_bold, anchor="w").grid(
            row=2, column=0, sticky="w", pady=(px(self, 16), 0))
        row = tk.Frame(body, bg=bg)
        row.grid(row=3, column=0, sticky="ew", pady=(px(self, 6), px(self, 4)))
        self.entry = ttk.Entry(row, textvariable=self.name_var, font=fonts.title, width=28)
        self.entry.pack(side="left", fill="x", expand=True)
        exts = list(FILE_EXTS)
        if ext and ext not in exts:
            exts.insert(0, ext)
        ttk.Combobox(row, textvariable=self.ext_var, values=exts, width=6, font=fonts.title).pack(
            side="left", padx=(px(self, 8), 0))
        if IS_WIN:
            ttk.Button(row, text="⌨", style="Small.TButton", width=3, command=toggle_touch_keyboard).pack(
                side="left", padx=(px(self, 8), 0))
        self.hint = tk.Label(body, text="", bg=bg, fg=C["muted"], font=fonts.small, anchor="w", justify="left",
                             wraplength=px(self, 600))
        self.hint.grid(row=4, column=0, sticky="w")

        tk.Label(body, text="Ordner", bg=bg, fg=C["text"], font=fonts.ui_bold, anchor="w").grid(
            row=5, column=0, sticky="w", pady=(px(self, 16), 0))
        frow = tk.Frame(body, bg=bg)
        frow.grid(row=6, column=0, sticky="ew", pady=(px(self, 6), 0))
        # Kundenordner zum Antippen statt Windows-Ordnerdialog
        self.folder_var = tk.StringVar()
        self._folder_map: Dict[str, str] = {}
        self.folder_cb = ttk.Combobox(frow, textvariable=self.folder_var, state="readonly", width=30,
                                      font=fonts.title, height=14)
        self.folder_cb.pack(side="left", fill="x", expand=True)
        self.folder_cb.bind("<<ComboboxSelected>>", lambda e: self._folder_chosen())
        ttk.Button(frow, text="+ Neuer Ordner", style="Small.TButton", command=self._new_folder).pack(
            side="left", padx=(px(self, 8), 0))
        ttk.Button(frow, text="Ändern …", style="Small.TButton", command=self._change_folder).pack(
            side="left", padx=(px(self, 8), 0))
        self.folder_label = tk.Label(body, text="", bg=bg, fg=C["muted"], font=fonts.small, anchor="w",
                                     justify="left", wraplength=px(self, 600))
        self.folder_label.grid(row=7, column=0, sticky="w", pady=(px(self, 4), 0))
        self.folder_reason = folder_reason
        self._fill_folders()

        bar = tk.Frame(self, bg=C["bg"])
        bar.pack(fill="x")
        self.save_btn = ttk.Button(bar, text="Speichern", style="Primary.TButton", command=self.save)
        self.save_btn.pack(side="right", padx=(0, px(self, 12)), pady=px(self, 14))
        ttk.Button(bar, text="Abbrechen", command=self.cancel).pack(side="right", padx=(0, px(self, 12)))
        ttk.Button(bar, text="+ Datum anhängen", command=self._add_date).pack(side="left", padx=px(self, 12))

        self.name_var.trace_add("write", lambda *a: self._validate())
        self.ext_var.trace_add("write", lambda *a: self._validate())
        self.bind("<Return>", lambda e: self.save())
        self._validate()
        self.entry.focus_set()
        self.entry.select_range(0, "end")
        self.entry.icursor("end")

    def _ext(self) -> str:
        ext = self.ext_var.get().strip()
        if ext and not ext.startswith("."):
            ext = "." + ext
        return ext

    def _base(self) -> str:
        name = self.name_var.get().strip()
        ext = self._ext()
        if ext and name.lower().endswith(ext.lower()):
            name = name[: -len(ext)]
        return name

    def path(self) -> str:
        return os.path.join(self.folder, self._base() + self._ext())

    def _validate(self) -> bool:
        base = self._base()
        if not base:
            self._set_hint("Bitte einen Dateinamen eingeben.", C["error"], ok=False)
            return False
        if _BAD_CHARS.search(base + self._ext()):
            self._set_hint('Diese Zeichen sind in Dateinamen nicht erlaubt:  \\ / : * ? " < > |', C["error"],
                           ok=False)
            return False
        path = self.path()
        if os.path.exists(path):
            when = datetime.datetime.fromtimestamp(os.path.getmtime(path)).strftime("%d.%m.%Y %H:%M")
            self._set_hint("„{}“ gibt es schon (gespeichert am {}). Speichern überschreibt diese Datei – "
                           "oder „+ Datum anhängen“ antippen.".format(os.path.basename(path), when),
                           C["warn"], ok=True, overwrite=True)
        else:
            self._set_hint("Wird gespeichert als:  " + os.path.basename(path), C["muted"], ok=True)
        return True

    def _set_hint(self, text: str, color: str, ok: bool, overwrite: bool = False) -> None:
        self.hint.configure(text=text, fg=color)
        self.save_btn.configure(text="Überschreiben" if overwrite else "Speichern",
                                style="Danger.TButton" if overwrite else "Primary.TButton")
        self.save_btn.state(["!disabled"] if ok else ["disabled"])

    def _add_date(self) -> None:
        base = re.sub(r"_\d{4}-\d{2}-\d{2}(_\d{4,6})?$", "", self._base())
        now = datetime.datetime.now()
        candidate = "{}_{}".format(base, now.strftime("%Y-%m-%d"))
        if os.path.exists(os.path.join(self.folder, candidate + self._ext())):
            candidate = "{}_{}".format(base, now.strftime("%Y-%m-%d_%H%M"))
        self.name_var.set(candidate)
        self.entry.icursor("end")

    # -- Ordnerwahl --------------------------------------------------------------

    def _root_label(self) -> str:
        root = self.root_folder or ""
        return "{}  (Hauptordner)".format(os.path.basename(root.rstrip("\\/")) or root)

    def _fill_folders(self) -> None:
        self._folder_map = {}
        if self.root_folder:
            self._folder_map[self._root_label()] = self.root_folder
            for label, path in subfolders(self.root_folder):
                self._folder_map[label] = path
        current = None
        for label, path in self._folder_map.items():
            if os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(self.folder)):
                current = label
        if current is None:  # Ordner außerhalb des Programmordners
            current = self.folder
            self._folder_map[current] = self.folder
        self.folder_cb.configure(values=list(self._folder_map))
        self.folder_var.set(current)
        self._show_folder()

    def _show_folder(self) -> None:
        text = self.folder
        if self.folder_reason:
            text = "✓ " + self.folder_reason + "   ·   " + text
        self.folder_label.configure(text=text, fg=C["ok"] if self.folder_reason else C["muted"])

    def _folder_chosen(self) -> None:
        path = self._folder_map.get(self.folder_var.get())
        if path:
            self.folder = path
            self.folder_reason = ""
            self._show_folder()
            self._validate()

    def _new_folder(self) -> None:
        base = self.root_folder or self.folder
        name = ask_text(self, self.fonts, "Neuer Kundenordner",
                        "Name des neuen Ordners (wird im Programmordner angelegt):")
        if not name:
            return
        name = name.strip()
        if _BAD_CHARS.search(name) or name in (".", ".."):
            error(self, self.fonts, "Ungültiger Name", 'Diese Zeichen sind nicht erlaubt:  \\ / : * ? " < > |')
            return
        path = os.path.join(base, name)
        try:
            os.makedirs(path, exist_ok=True)
        except OSError as exc:
            error(self, self.fonts, "Ordner konnte nicht angelegt werden", str(exc))
            return
        self.folder = path
        self.folder_reason = "neuer Ordner angelegt"
        self._fill_folders()
        self._validate()

    def _change_folder(self) -> None:
        start = self.folder if os.path.isdir(self.folder) else os.path.expanduser("~")
        path = filedialog.askdirectory(parent=self, initialdir=start, title="Ordner wählen")
        if path:
            self.folder = os.path.normpath(path)
            self.folder_reason = ""
            self._fill_folders()
            self._validate()

    def save(self) -> None:
        if not self._validate():
            return
        self.result = self.path()
        self.destroy()


class TextInputDialog(Dialog):
    """Einfache Texteingabe (z. B. Name für einen neuen Ordner)."""

    def __init__(self, parent, fonts: Fonts, title: str, message: str, initial: str = "") -> None:
        super().__init__(parent, fonts, title)
        self.resizable(False, False)
        bg = C["surface"]
        body = tk.Frame(self, bg=bg, padx=px(self, 24), pady=px(self, 20))
        body.pack(fill="both", expand=True)
        tk.Label(body, text=title, bg=bg, fg=C["text"], font=fonts.title, anchor="w").pack(anchor="w")
        tk.Label(body, text=message, bg=bg, fg=C["muted"], font=fonts.ui, anchor="w", justify="left",
                 wraplength=px(self, 480)).pack(anchor="w", pady=(px(self, 4), px(self, 10)))
        row = tk.Frame(body, bg=bg)
        row.pack(fill="x")
        self.var = tk.StringVar(value=initial)
        self.entry = ttk.Entry(row, textvariable=self.var, font=fonts.title, width=28)
        self.entry.pack(side="left", fill="x", expand=True)
        if IS_WIN:
            ttk.Button(row, text="⌨", style="Small.TButton", width=3, command=toggle_touch_keyboard).pack(
                side="left", padx=(px(self, 8), 0))
        bar = tk.Frame(self, bg=C["bg"])
        bar.pack(fill="x")
        ttk.Button(bar, text="OK", style="Primary.TButton", command=self.ok).pack(
            side="right", padx=(0, px(self, 12)), pady=px(self, 14))
        ttk.Button(bar, text="Abbrechen", command=self.cancel).pack(side="right", padx=(0, px(self, 12)))
        self.bind("<Return>", lambda e: self.ok())
        self.entry.focus_set()

    def ok(self) -> None:
        text = self.var.get().strip()
        if text:
            self.result = text
            self.destroy()


def ask_text(parent, fonts: Fonts, title: str, message: str, initial: str = "") -> Optional[str]:
    return TextInputDialog(parent, fonts, title, message, initial).show()


class FormDialog(Dialog):
    """Kleines Formular mit mehreren Textfeldern, z. B. Bearbeitung + Bemerkung eines Werkzeugs."""

    def __init__(self, parent, fonts: Fonts, title: str, fields: Sequence[tuple],
                 message: str = "") -> None:
        """``fields``: (Schlüssel, Beschriftung, Wert) oder (…, Auswahlliste) für ein Auswahlfeld."""
        super().__init__(parent, fonts, title)
        self.resizable(False, False)
        bg = C["surface"]
        body = tk.Frame(self, bg=bg, padx=px(self, 24), pady=px(self, 20))
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(1, weight=1)
        tk.Label(body, text=title, bg=bg, fg=C["text"], font=fonts.title, anchor="w").grid(
            row=0, column=0, columnspan=3, sticky="w")
        row = 1
        if message:
            tk.Label(body, text=message, bg=bg, fg=C["muted"], font=fonts.ui, anchor="w", justify="left",
                     wraplength=px(self, 560)).grid(row=1, column=0, columnspan=3, sticky="w",
                                                   pady=(px(self, 4), px(self, 6)))
            row = 2
        self.vars: Dict[str, tk.StringVar] = {}
        first = None
        for field in fields:
            key, label, value = field[0], field[1], field[2]
            choices = field[3] if len(field) > 3 else None
            tk.Label(body, text=label, bg=bg, fg=C["text"], font=fonts.ui, anchor="w").grid(
                row=row, column=0, sticky="w", padx=(0, px(self, 14)), pady=px(self, 6))
            var = tk.StringVar(master=self, value=value)
            self.vars[key] = var
            if choices:
                ttk.Combobox(body, textvariable=var, values=list(choices), state="readonly", font=fonts.title,
                             width=14).grid(row=row, column=1, sticky="w", pady=px(self, 6))
                row += 1
                continue
            e = ttk.Entry(body, textvariable=var, font=fonts.title, width=34)
            e.grid(row=row, column=1, sticky="ew", pady=px(self, 6))
            if IS_WIN:
                ttk.Button(body, text="⌨", style="Small.TButton", width=3, command=toggle_touch_keyboard).grid(
                    row=row, column=2, padx=(px(self, 8), 0))
            first = first or e
            row += 1
        bar = tk.Frame(self, bg=C["bg"])
        bar.pack(fill="x")
        ttk.Button(bar, text="Übernehmen", style="Primary.TButton", command=self.ok).pack(
            side="right", padx=(0, px(self, 12)), pady=px(self, 14))
        ttk.Button(bar, text="Abbrechen", command=self.cancel).pack(side="right", padx=(0, px(self, 12)))
        self.bind("<Return>", lambda e: self.ok())
        if first is not None:
            first.focus_set()
            first.icursor("end")

    def ok(self) -> None:
        self.result = {k: v.get().strip() for k, v in self.vars.items()}
        self.destroy()
