"""Maschinenliste und Programmeinstellungen (gespeichert als JSON)."""

from __future__ import annotations

import copy
import json
import os
import sys
import uuid
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Dict, List, Optional

from .textutil import safe_filename

CONTROLS = {
    "fanuc": "Fanuc / ISO",
    "heidenhain": "Heidenhain",
    "sinumerik": "Siemens Sinumerik",
    "generic": "Allgemein",
}

# Was beim Wechsel der Steuerung im Dialog vorbelegt wird
CONTROL_PRESETS: Dict[str, Dict[str, Any]] = {
    "fanuc": {"frame_percent": True, "end_mode": "percent", "send_eol": "CRLF"},
    "heidenhain": {"frame_percent": False, "end_mode": "endpgm", "send_eol": "CRLF"},
    "sinumerik": {"frame_percent": False, "end_mode": "none", "send_eol": "LF"},
    "generic": {"frame_percent": False, "end_mode": "none", "send_eol": "CRLF"},
}

FLOW_CONTROLS = {
    "rtscts": "Hardware (RTS/CTS)",
    "xonxoff": "Software (XON/XOFF)",
    "dsrdtr": "Hardware (DTR/DSR)",
    "none": "Kein Handshake",
}

PARITIES = {"N": "Keine", "E": "Gerade (Even)", "O": "Ungerade (Odd)", "M": "Mark", "S": "Space"}
BAUDRATES = [300, 600, 1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200]
BYTESIZES = [7, 8]
STOPBITS = [1, 1.5, 2]


def default_program_root() -> str:
    home = os.path.expanduser("~")
    docs = os.path.join(home, "Documents")
    if not os.path.isdir(docs):
        docs = home
    return os.path.join(docs, "CNC-Programme")


@dataclass
class MachineProfile:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    number: str = ""
    name: str = "Neue Maschine"
    control: str = "fanuc"
    # Schnittstelle
    port: str = "COM1"
    baudrate: int = 9600
    bytesize: int = 8
    parity: str = "N"
    stopbits: float = 1
    flow: str = "rtscts"
    # Senden
    send_eol: str = "CRLF"
    frame_percent: bool = True
    strip_trailing_spaces: bool = False
    skip_empty_lines: bool = False
    leader_nuls: int = 0
    trailer_nuls: int = 0
    char_delay_ms: int = 0
    line_delay_ms: int = 0
    # Empfangen
    end_mode: str = "percent"
    idle_timeout_s: float = 5.0
    strip_control_chars: bool = True
    auto_save_received: bool = False
    # Dateien
    folder: str = ""
    file_ext: str = ".nc"
    # Postprozessor für den Kontur-Assistenten (G-/M-Befehle, siehe postpro.py); leer = Vorlage zur Steuerung
    post: Dict[str, Any] = field(default_factory=dict)
    # Aus Bildern gelesen (PC-Version): M-/G-Code-Liste, abgelegte Bilder, Zyklen (siehe zyklen.py)
    codes: List[Dict[str, str]] = field(default_factory=list)
    bilder: List[str] = field(default_factory=list)
    zyklen: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        return "{}: {}".format(self.number, self.name) if self.number else self.name

    @property
    def frame_format(self) -> str:
        sb = "1.5" if self.stopbits == 1.5 else str(int(self.stopbits))
        return "{}{}{}".format(self.bytesize, self.parity, sb)

    @property
    def flow_short(self) -> str:
        return {"rtscts": "RTS/CTS", "xonxoff": "XON/XOFF", "dsrdtr": "DTR/DSR"}.get(self.flow, "ohne Handshake")

    @property
    def serial_summary(self) -> str:
        return "{} · {} Baud · {} · {}".format(self.port, self.baudrate, self.frame_format, self.flow_short)

    @property
    def control_name(self) -> str:
        return CONTROLS.get(self.control, self.control)

    def program_folder(self) -> str:
        if self.folder:
            return self.folder
        return os.path.join(default_program_root(), safe_filename(self.display_name) or "Maschine")

    def copy(self) -> "MachineProfile":
        data = copy.deepcopy(asdict(self))
        data["id"] = uuid.uuid4().hex[:12]
        return MachineProfile(**data)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MachineProfile":
        known = {f.name for f in fields(cls)}
        clean = {k: v for k, v in data.items() if k in known}
        prof = cls(**clean)
        prof._sanitize()
        return prof

    def _sanitize(self) -> None:
        """Ungültige Werte aus einer (evtl. von Hand bearbeiteten) Datei reparieren."""
        defaults = asdict(MachineProfile())
        for name, conv in (("baudrate", int), ("bytesize", int), ("leader_nuls", int), ("trailer_nuls", int),
                           ("char_delay_ms", int), ("line_delay_ms", int), ("stopbits", float),
                           ("idle_timeout_s", float)):
            try:
                setattr(self, name, conv(getattr(self, name)))
            except (TypeError, ValueError):
                setattr(self, name, defaults[name])
        if self.stopbits == int(self.stopbits):
            self.stopbits = int(self.stopbits)
        if self.parity not in PARITIES:
            self.parity = "N"
        if self.flow not in FLOW_CONTROLS:
            self.flow = "none"
        if self.control not in CONTROLS:
            self.control = "generic"
        if self.bytesize not in (5, 6, 7, 8):
            self.bytesize = 8
        if self.stopbits not in STOPBITS:
            self.stopbits = 1
        if self.send_eol not in ("CRLF", "LF", "CR"):
            self.send_eol = "CRLF"
        for name in ("number", "name", "port", "folder", "file_ext", "id"):
            setattr(self, name, str(getattr(self, name) or ""))
        if not self.id:
            self.id = uuid.uuid4().hex[:12]
        if self.file_ext and not self.file_ext.startswith("."):
            self.file_ext = "." + self.file_ext
        if not isinstance(self.post, dict):
            self.post = {}
        self.codes = [{"code": str(c.get("code", "")), "bedeutung": str(c.get("bedeutung", ""))}
                      for c in (self.codes if isinstance(self.codes, list) else []) if isinstance(c, dict)]
        self.bilder = [str(b) for b in (self.bilder if isinstance(self.bilder, list) else []) if b]
        self.zyklen = [z for z in (self.zyklen if isinstance(self.zyklen, list) else []) if isinstance(z, dict)]


def default_machines() -> List[MachineProfile]:
    return [
        MachineProfile(number="78", name="Emco - 2", control="fanuc", port="COM1", baudrate=9600,
                       bytesize=8, parity="N", stopbits=1, flow="rtscts"),
    ]


@dataclass
class AppConfig:
    machines: List[MachineProfile] = field(default_factory=default_machines)
    active_machine_id: str = ""
    editor_font_size: int = 14
    show_file_panel: bool = True
    # Bildschirmtastatur automatisch öffnen beim Antippen von Eingabefeldern / des Editors
    touch_keyboard: bool = True
    touch_keyboard_editor: bool = False
    # zuletzt gewählte Satznummerierung
    renumber_scheme: str = "tool"
    renumber_step: int = 10
    korr_landscape: bool = False  # Korrekturblatt quer statt hochkant
    geometry: str = ""
    # Bilder lesen (PC-Version): API-Schlüssel von console.anthropic.com; leer = Umgebungsvariable
    ki_schluessel: str = ""

    path: str = field(default="", repr=False, compare=False)

    @property
    def active_machine(self) -> MachineProfile:
        for m in self.machines:
            if m.id == self.active_machine_id:
                return m
        if not self.machines:
            self.machines = default_machines()
        self.active_machine_id = self.machines[0].id
        return self.machines[0]

    def machine_by_id(self, mid: str) -> Optional[MachineProfile]:
        for m in self.machines:
            if m.id == mid:
                return m
        return None

    # -- Laden / Speichern ---------------------------------------------------

    @classmethod
    def load(cls, path: Optional[str] = None) -> "AppConfig":
        path = path or config_path()
        cfg = cls()
        cfg.path = path
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return cfg
        if not isinstance(data, dict):
            return cfg
        machines = []
        for item in data.get("machines", []):
            if isinstance(item, dict):
                try:
                    machines.append(MachineProfile.from_dict(item))
                except TypeError:
                    continue
        if machines:
            cfg.machines = machines
        cfg.active_machine_id = str(data.get("active_machine_id", ""))
        try:
            cfg.editor_font_size = max(8, min(40, int(data.get("editor_font_size", cfg.editor_font_size))))
        except (TypeError, ValueError):
            pass
        cfg.show_file_panel = bool(data.get("show_file_panel", True))
        cfg.touch_keyboard = bool(data.get("touch_keyboard", True))
        cfg.touch_keyboard_editor = bool(data.get("touch_keyboard_editor", False))
        cfg.korr_landscape = bool(data.get("korr_landscape", False))
        if data.get("renumber_scheme") in ("tool", "flat", "toolonly", "remove"):
            cfg.renumber_scheme = data["renumber_scheme"]
        try:
            cfg.renumber_step = max(1, min(100, int(data.get("renumber_step", 10))))
        except (TypeError, ValueError):
            pass
        cfg.geometry = str(data.get("geometry", ""))
        cfg.ki_schluessel = str(data.get("ki_schluessel", "") or "")
        return cfg

    def save(self) -> None:
        path = self.path or config_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = {
            "version": 1,
            "active_machine_id": self.active_machine_id,
            "editor_font_size": self.editor_font_size,
            "show_file_panel": self.show_file_panel,
            "touch_keyboard": self.touch_keyboard,
            "touch_keyboard_editor": self.touch_keyboard_editor,
            "renumber_scheme": self.renumber_scheme,
            "renumber_step": self.renumber_step,
            "korr_landscape": self.korr_landscape,
            "geometry": self.geometry,
            "ki_schluessel": self.ki_schluessel,
            "machines": [asdict(m) for m in self.machines],
        }
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, path)


def config_dir() -> str:
    override = os.environ.get("CNC_DNC_CONFIG_DIR")
    if override:
        return override
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "CNC-Datenuebertragung")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "cnc-datenuebertragung")


def config_path() -> str:
    return os.path.join(config_dir(), "config.json")
