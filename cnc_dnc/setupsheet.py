"""Einrichteblatt: Werkzeuge je Seite aus dem Programm lesen, Eingaben speichern, PDF erzeugen.

Kanal 1 = Seite 1, Kanal 2 = Seite 2. Zu jedem Werkzeug wird der Kommentar
direkt darüber übernommen (z. B. ``;FERTIGDREHEN VCMT04``). Eigene Einträge
(Bemerkung je Werkzeug, Spanndruck, Notizen …) liegen in einer Begleitdatei
neben dem Programm: ``METLOG_300069.txt`` → ``METLOG_300069.einrichteblatt.json``.
"""

from __future__ import annotations

import datetime
import json
import os
import re
from typing import Dict, List, NamedTuple, Optional, Tuple

from .textutil import (
    _FANUC_NAME,
    _FANUC_O,
    _HEID_HEAD,
    _SIN_HEAD,
    _SIN_PATH,
    MAX_SETUP_LINES,
    _is_comment_only,
    is_setup_line,
    is_tool_call,
)

SIDECAR_SUFFIX = ".einrichteblatt.json"
PDF_SUFFIX = "_Einrichteblatt.pdf"
KORR_PDF_SUFFIX = "_Korrekturblatt.pdf"

# Programmname → Seite: KANAL1, KANAL_2, SEITE1, CHAN2, S1 …
_SIDE_FROM_NAME = re.compile(r"(?:^|_)(?:KANAL|KAN|CHAN|SEITE|S)_?([12])$", re.IGNORECASE)
# Kommentarzeile, die eine Seite ankündigt: "(SEITE 2)", ";2. SEITE", ";AUFSPANNUNG 1"
_SIDE_FROM_COMMENT = re.compile(
    r"\b(?:SEITE|SPANNUNG|AUFSPANNUNG)\s*([12])\b|\b([12])\s*\.\s*(?:SEITE|SPANNUNG|AUFSPANNUNG)\b",
    re.IGNORECASE)
_LOOKS_LIKE_CODE = re.compile(r"^[A-Z]{1,2}\d", re.IGNORECASE)  # ";M22 …", ";g04f0.1" = auskommentierter Code
_TOOL_PREFIX = re.compile(r"^(?:WERKZEUG|WZ|TOOL)\s*:?\s*T?\s*\d*\s*[-:–]?\s*", re.IGNORECASE)
_PARAM_LINE = re.compile(r"^\s*([A-Z$_#][\w$#\[\],.]*)\s*=\s*([-+]?\d+(?:\.\d*)?|[-+]?\.\d+)\s*(?:;(.*)|\((.*)\))\s*$",
                         re.IGNORECASE)


class ToolUse(NamedTuple):
    side: int          # 1, 2 oder 0 (= weitere Programme, z. B. ABSTECH)
    tool: str          # Anzeige, z. B. "T1", "T3 D2", "T0101", "BOHRER_8"
    description: str   # aus dem Kommentar, z. B. "VORDREHEN VNMG08"
    program: str       # Unterprogramm, z. B. "KANAL1"
    line: int

    @property
    def key(self) -> str:
        return "{}|{}|{}".format(self.side, self.tool, self.description)


class Parameter(NamedTuple):
    name: str
    value: str
    comment: str


def format_tool(line: str) -> str:
    """Werkzeug für die Anzeige: T01D1 → T1, T03D2 → T3 D2, T0101 bleibt, T="BOHRER" → BOHRER."""
    code = re.sub(r"\([^)]*\)|;.*$", "", line)
    m = re.search(r'(?<![A-Za-z_$])T\s*=\s*"([^"]+)"', code, re.IGNORECASE)
    if m:
        name = m.group(1)
    else:
        m = re.search(r"(?<![A-Za-z_$])T\s*=?\s*(\d+)", code, re.IGNORECASE)
        if not m:
            m2 = re.search(r"TOOL\s+CALL\s+(\d+|\"[^\"]+\")", code, re.IGNORECASE)
            return "T" + m2.group(1).strip('"') if m2 else "T?"
        digits = m.group(1)
        name = "T" + digits if len(digits) == 4 else "T" + str(int(digits))
    d = re.search(r"(?<![A-Za-z_$])D\s*=?\s*(\d+)", code, re.IGNORECASE)
    if d and int(d.group(1)) not in (0, 1):
        name += " D" + str(int(d.group(1)))
    return name


def _clean_comment(s: str) -> str:
    s = s.strip()
    if s.startswith(";"):
        s = s[1:]
    s = s.strip()
    if s.startswith("(") and s.endswith(")"):
        s = s[1:-1]
    s = _TOOL_PREFIX.sub("", s.strip()) if re.match(r"(?:WERKZEUG|WZ|TOOL)\b", s.strip(), re.I) else s
    return re.sub(r"\s+", " ", s).strip(" -:")


def _inline_comment(line: str) -> str:
    m = re.search(r";(.*)$", line)
    if m:
        return _clean_comment(m.group(1))
    m = re.search(r"\(([^)]*)\)", line)
    return _clean_comment(m.group(1)) if m else ""


def _meaningful_comment(s: str) -> bool:
    body = _clean_comment(s)
    return bool(re.search(r"[A-Za-zÄÖÜäöü]", body)) and not _LOOKS_LIKE_CODE.match(body) \
        and not _SIN_PATH.match(s)


def _program_name(s: str) -> Optional[str]:
    m = _SIN_HEAD.match(s)
    if m:
        return m.group(1)
    m = _FANUC_O.match(s)
    if m:
        return "O" + m.group(2)
    m = _HEID_HEAD.match(s)
    if m:
        return m.group(1)
    m = _FANUC_NAME.match(s)
    return m.group(1) if m else None


def extract_tools(text: str) -> List[ToolUse]:
    """Alle Werkzeugaufrufe mit Seite und Beschreibung, in Programmreihenfolge."""
    result: List[ToolUse] = []
    # Werkstück mit KANAL1/KANAL2-Programmen? Dann kommen Werkzeuge aus anderen
    # Unterprogrammen (ABSTECH, ANSCHLAGEN …) in eine eigene Gruppe.
    has_side_programs = any(
        _SIDE_FROM_NAME.search(m.group(1))
        for m in (_SIN_HEAD.match(ln.strip()) for ln in text.split("\n")) if m)
    program = ""
    program_side: Optional[int] = None
    marker_side: Optional[int] = None
    pending_comment = ""
    setup_lines = 0  # Sätze ohne Achsbewegung seit dem Kommentar (M5, G04 …)
    for no, raw in enumerate(text.split("\n"), start=1):
        s = raw.strip()
        if not s:
            continue
        name = _program_name(s)
        if name:
            program = name
            m = _SIDE_FROM_NAME.search(name)
            program_side = int(m.group(1)) if m else None
            marker_side = None
            pending_comment = ""
            continue
        if _is_comment_only(s):
            if _SIN_PATH.match(s):
                continue
            m = _SIDE_FROM_COMMENT.search(s)
            if m:
                marker_side = int(m.group(1) or m.group(2))
            if _meaningful_comment(s):
                pending_comment = _clean_comment(s)
                setup_lines = 0
            continue
        if is_tool_call(s):
            desc = pending_comment or _inline_comment(s)
            side = program_side or marker_side
            if side is None:
                side = 0 if has_side_programs else 1
            if not desc and side == 0:
                desc = program
            result.append(ToolUse(side, format_tool(s), desc, program, no))
        elif pending_comment and setup_lines < MAX_SETUP_LINES and is_setup_line(s):
            setup_lines += 1  # z. B. M5 zwischen ";FRAESEN 5MM" und dem Werkzeug
            continue
        pending_comment = ""
    return _borrow_descriptions(_dedupe(result))


def _borrow_descriptions(tools: List[ToolUse]) -> List[ToolUse]:
    """Werkzeug ohne Kommentar: Bezeichnung von einem anderen Aufruf desselben Werkzeugs übernehmen.

    Nur innerhalb derselben Seite – bei zwei Kanälen sind T2 auf Seite 1 und T2 auf Seite 2
    verschiedene Werkzeuge (eigener Revolver). Seite 1 nimmt auch Bezeichnungen aus den
    weiteren Programmen (Abstechen, Anschlag), die zur Hauptspindel gehören.
    """
    known: Dict[Tuple[int, str], str] = {}
    for t in tools:
        if t.description:
            known.setdefault((t.side, t.tool), t.description)
    out = []
    for t in tools:
        if not t.description:
            desc = known.get((t.side, t.tool)) or (known.get((0, t.tool), "") if t.side == 1 else "")
            t = t._replace(description=desc)
        out.append(t)
    return out


def _dedupe(tools: List[ToolUse]) -> List[ToolUse]:
    seen = set()
    out: List[ToolUse] = []
    merged: Dict[str, int] = {}
    for t in tools:
        if t.side == 0:
            # weitere Programme: gleiches Werkzeug nur einmal, Programme zusammenfassen
            if t.tool in merged:
                i = merged[t.tool]
                old = out[i]
                if t.description and t.description not in old.description.split(" / "):
                    out[i] = old._replace(description=(old.description + " / " + t.description).strip(" /"))
                continue
            merged[t.tool] = len(out)
            out.append(t)
            continue
        if (t.side, t.tool, t.description) in seen:
            continue
        seen.add((t.side, t.tool, t.description))
        out.append(t)
    return out


def extract_parameters(text: str) -> List[Parameter]:
    """Einstellwerte aus einem Parameter-Programm (z. B. ``R72=32 ;DURCHMESSER STANGE``)."""
    params: List[Parameter] = []
    in_param_program = False
    for raw in text.split("\n"):
        s = raw.strip()
        name = _SIN_HEAD.match(s)
        if name:
            in_param_program = "PARAM" in name.group(1).upper()
            continue
        if not in_param_program:
            continue
        m = _PARAM_LINE.match(s)
        if m:
            comment = (m.group(3) or m.group(4) or "").strip()
            if comment:
                params.append(Parameter(m.group(1), m.group(2), re.sub(r"\s+", " ", comment)))
    return params


def tools_by_side(tools: List[ToolUse]) -> Dict[int, List[ToolUse]]:
    groups: Dict[int, List[ToolUse]] = {}
    for t in tools:
        groups.setdefault(t.side, []).append(t)
    return groups


def side_title(side: int, tools: List[ToolUse]) -> Tuple[str, str]:
    """Überschrift und Untertitel einer Werkzeug-Spalte."""
    progs = []
    for t in tools:
        if t.program and t.program not in progs:
            progs.append(t.program)
    if side == 0:
        return "WEITERE PROGRAMME", ", ".join(progs)
    return "SEITE {}".format(side), ", ".join(progs)


# --------------------------------------------------------------------------
# Eigene Eingaben (Begleitdatei)
# --------------------------------------------------------------------------

FIELDS = [
    # (Schlüssel, Beschriftung, Gruppe)
    ("teil", "Teil / Benennung", "teil"),
    ("zeichnung", "Zeichnungs-Nr.", "teil"),
    ("material", "Material", "teil"),
    ("rohteil", "Rohteil", "teil"),
    ("stueckzahl", "Stückzahl", "teil"),
    ("eingerichtet_von", "Eingerichtet von", "teil"),
    ("datum", "Datum", "teil"),
    ("hsp_spannmittel", "Spannmittel", "hsp"),
    ("hsp_druck", "Spanndruck (bar)", "hsp"),
    ("hsp_bemerkung", "Bemerkung", "hsp"),
    ("gsp_spannmittel", "Spannmittel", "gsp"),
    ("gsp_druck", "Spanndruck (bar)", "gsp"),
    ("gsp_bemerkung", "Bemerkung", "gsp"),
]


class SheetData:
    def __init__(self) -> None:
        self.fields: Dict[str, str] = {k: "" for k, _, _ in FIELDS}
        self.notes = ""
        self.tools: Dict[str, Dict[str, str]] = {}  # key -> {"description":…, "remark":…}
        # Korrekturblatt: je Werkzeug (Schlüssel "Herkunftsseite|T") Seite, Name, Bemerkung
        self.korrektur: Dict[str, Dict[str, object]] = {}

    def tool_entry(self, t: ToolUse) -> Dict[str, str]:
        """Gespeicherte Eingaben zu einem Werkzeug – passend auch, wenn sich der Kommentar geändert hat."""
        e = self.tools.get(t.key)
        if e is None:
            for k, v in self.tools.items():
                side, tool, _ = k.split("|", 2)
                if side == str(t.side) and tool == t.tool:
                    e = v
                    break
        return {"description": (e or {}).get("description") or t.description,
                "remark": (e or {}).get("remark", "")}

    def set_tool(self, t: ToolUse, description: str, remark: str) -> None:
        self.tools[t.key] = {"description": description, "remark": remark}

    def to_json(self) -> dict:
        return {"version": 1, "updated": datetime.datetime.now().isoformat(timespec="seconds"),
                "fields": self.fields, "notes": self.notes, "tools": self.tools, "korrektur": self.korrektur}

    @classmethod
    def from_json(cls, data: dict) -> "SheetData":
        sd = cls()
        if isinstance(data, dict):
            for k, v in (data.get("fields") or {}).items():
                if k in sd.fields:
                    sd.fields[k] = str(v)
            sd.notes = str(data.get("notes") or "")
            tools = data.get("tools") or {}
            if isinstance(tools, dict):
                sd.tools = {str(k): {"description": str(v.get("description", "")), "remark": str(v.get("remark", ""))}
                            for k, v in tools.items() if isinstance(v, dict)}
            korr = data.get("korrektur") or {}
            if isinstance(korr, dict):
                for k, v in korr.items():
                    if not isinstance(v, dict):
                        continue
                    entry: Dict[str, object] = {}
                    if v.get("side") in (1, 2):
                        entry["side"] = int(v["side"])
                    for f in ("name", "remark"):
                        if v.get(f) is not None:
                            entry[f] = str(v[f])
                    sd.korrektur[str(k)] = entry
        return sd


def sidecar_path(program_path: str) -> str:
    return os.path.splitext(program_path)[0] + SIDECAR_SUFFIX


def pdf_path(program_path: str) -> str:
    return os.path.splitext(program_path)[0] + PDF_SUFFIX


def korr_pdf_path(program_path: str) -> str:
    return os.path.splitext(program_path)[0] + KORR_PDF_SUFFIX


def load_sheet(program_path: str) -> SheetData:
    try:
        with open(sidecar_path(program_path), "r", encoding="utf-8") as fh:
            return SheetData.from_json(json.load(fh))
    except (OSError, ValueError):
        return SheetData()


def save_sheet(program_path: str, data: SheetData) -> None:
    path = sidecar_path(program_path)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data.to_json(), fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def is_sidecar(name: str) -> bool:
    return name.lower().endswith(SIDECAR_SUFFIX)

# --------------------------------------------------------------------------
# Korrekturblatt: je Seite eine Zeile pro Werkzeug
# --------------------------------------------------------------------------

class KorrRow(NamedTuple):
    key: str           # Hauptschlüssel "Herkunftsseite|T", z. B. "0|T12" (dort stehen Name/Bemerkung)
    tool: str          # "T12"
    name: str          # Bezeichnung (aus dem Kommentar bzw. selbst geändert)
    remark: str
    side: int          # aktuelle Seite 1 oder 2
    origin: int        # Seite laut Programm (0 = weiteres Programm, z. B. ABSTECH)
    programs: str      # "KANAL1" oder "ABSTECH, UEBERGABE"
    keys: Tuple[str, ...] = ()  # alle zusammengefassten Schlüssel (z. B. "1|T12" und "0|T12")


def _tool_sort_key(tool: str):
    m = re.match(r"T(\d+)", tool)
    return (0, int(m.group(1)), tool) if m else (1, 0, tool)


def korrektur_rows(tools: List[ToolUse], data: SheetData) -> Dict[int, List[KorrRow]]:
    """Werkzeuge für das Korrekturblatt.

    - Gleiches Werkzeug auf derselben Seite nur einmal (Bezeichnungen zusammengefasst),
      denn korrigiert wird pro Werkzeug.
    - Werkzeuge aus weiteren Programmen (Abstechen, Anschlag …) stehen auf Seite 1.
    - Eigene Änderungen (andere Seite, Name, Bemerkung) aus ``data.korrektur``.
    """
    class _Acc:
        def __init__(self, tool: str, origin: int, pos: int) -> None:
            self.tool, self.origin, self.pos = tool, origin, pos
            self.names: List[str] = []
            self.programs: List[str] = []

    merged: Dict[str, _Acc] = {}
    for t in tools:
        key = "{}|{}".format(t.side, t.tool)
        acc = merged.setdefault(key, _Acc(t.tool, t.side, len(merged)))
        desc = data.tool_entry(t)["description"]
        if desc and desc not in acc.names:
            acc.names.append(desc)
        if t.program and t.program not in acc.programs:
            acc.programs.append(t.program)

    # Reihenfolge: Seite 1 wie im Programm, Seite 2 wie im Programm, weitere nach T-Nummer
    rank = {1: 0, 2: 1, 0: 2}

    def sort_key(key: str):
        acc = merged[key]
        tool_key = _tool_sort_key(acc.tool) if acc.origin == 0 else (0, 0, "")
        return (rank.get(acc.origin, 2), tool_key, acc.pos)

    # 1) je Herkunft eine Zeile
    single: List[KorrRow] = []
    for key in sorted(merged, key=sort_key):
        acc = merged[key]
        own = data.korrektur.get(key, {})
        side = own.get("side") if own.get("side") in (1, 2) else (acc.origin if acc.origin in (1, 2) else 1)
        name = str(own["name"]) if own.get("name") is not None else " / ".join(acc.names)
        single.append(KorrRow(key, acc.tool, name, str(own.get("remark", "")), int(side), acc.origin,
                              ", ".join(acc.programs), (key,)))

    # 2) gleiches Werkzeug auf derselben Seite zu einer Zeile zusammenfassen – z. B. T12 aus
    #    KANAL1 und T12 aus ABSTECH. Abstechen/Anschlag bleiben hinten auf Seite 1, sonst
    #    steht die Zeile dort, wo das Werkzeug seine Bezeichnung hat.
    groups: Dict[Tuple[int, str], List[int]] = {}
    for i, r in enumerate(single):
        groups.setdefault((r.side, r.tool), []).append(i)
    rows: List[Tuple[int, KorrRow]] = []
    for (side, tool), idxs in groups.items():
        members = [single[i] for i in idxs]
        # Abstechen/Anschlag (aus weiteren Programmen) stehen hinten – dort bleibt die Zeile
        extra = [i for i in idxs if single[i].origin == 0]
        named = [i for i in idxs if single[i].name]
        pos = extra[0] if extra else (named[0] if named else idxs[0])
        primary = single[pos]

        def unique(values):
            out: List[str] = []
            for v in values:
                for part in v.split(" / "):
                    if part and part not in out:
                        out.append(part)
            return out

        overridden = [m.name for m in members if data.korrektur.get(m.key, {}).get("name") is not None]
        name = overridden[0] if overridden else " / ".join(unique(m.name for m in members))
        remark = " / ".join(r for r in dict.fromkeys(m.remark for m in members) if r)
        programs = ", ".join(unique(p for m in members for p in m.programs.split(", ")))
        rows.append((pos, KorrRow(primary.key, tool, name, remark, side, primary.origin, programs,
                                  tuple(m.key for m in members))))
    result: Dict[int, List[KorrRow]] = {1: [], 2: []}
    for _pos, r in sorted(rows, key=lambda x: x[0]):
        result[r.side].append(r)
    return result
