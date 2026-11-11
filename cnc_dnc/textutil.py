"""Textverarbeitung für CNC-Programme.

Hier steckt die Logik, die beim alten Programm die "verrutschten Zeilen"
verursacht hat: Steuerungen benutzen ganz unterschiedliche Zeilenenden
(Fanuc gibt standardmäßig ``LF CR CR`` aus, andere ``CR LF``, ``LF`` oder nur
``CR``), und über die serielle Schnittstelle kommen die Daten in beliebig
zerstückelten Blöcken an. Ein Zeilenende kann also mitten zwischen zwei
Blöcken liegen. Der :class:`LineEndingNormalizer` erkennt die Art des
Zeilenendes automatisch und arbeitet über Blockgrenzen hinweg korrekt.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

ENCODING = "cp1252"

# Zeilenenden, die beim Senden gewählt werden können
EOL_CHOICES = {
    "CRLF": "\r\n",
    "LF": "\n",
    "CR": "\r",
}

# Steuerzeichen, die beim Empfang entfernt werden (NUL-Vorlauf, XON/XOFF, DEL ...).
# TAB, LF und CR bleiben erhalten (CR/LF werden danach normalisiert).
_DROP_TABLE = {c: None for c in list(range(0, 32)) + [127] if c not in (9, 10, 13)}


class LineEndingNormalizer:
    """Wandelt einen Zeichenstrom mit beliebigen Zeilenenden in ``\\n`` um.

    Unterstützt: ``LF``, ``CR LF``, ``LF CR CR`` (Fanuc-Standard),
    ``CR CR LF`` und reines ``CR``. Sobald irgendwo ein LF auftaucht, gilt LF
    als Zeilenende und alle CR werden verworfen. Folgt auf ein CR direkt
    normaler Text, ist es ein reiner CR-Strom und CR wird zum Zeilenende.
    """

    def __init__(self, strip_control: bool = True) -> None:
        self.mode: Optional[str] = None  # None (noch unklar), "LF" oder "CR"
        self.pending_cr = 0
        self.strip_control = strip_control

    def _fast(self, text: str) -> str:
        if self.mode == "LF":
            return text.replace("\r", "")
        # reiner CR-Strom
        return text.replace("\n", "").replace("\r", "\n")

    def feed(self, text: str) -> str:
        if self.strip_control:
            text = text.translate(_DROP_TABLE)
        if not text:
            return ""
        if self.mode is not None:
            return self._fast(text)

        out: List[str] = []
        for i, ch in enumerate(text):
            if ch == "\r":
                self.pending_cr += 1
            elif ch == "\n":
                self.mode = "LF"
                self.pending_cr = 0
                out.append("\n")
                out.append(self._fast(text[i + 1:]))
                break
            elif self.pending_cr:
                self.mode = "CR"
                out.append("\n" * self.pending_cr)
                self.pending_cr = 0
                out.append(self._fast(text[i:]))
                break
            else:
                out.append(ch)
        return "".join(out)

    def flush(self) -> str:
        """Am Ende des Datenstroms: noch offene CR als Zeilenende ausgeben."""
        rest = "\n" * self.pending_cr if self.mode != "LF" else ""
        self.pending_cr = 0
        return rest


def normalize_text(text: str, strip_control: bool = True) -> str:
    """Normalisiert einen kompletten Text auf ``\\n``-Zeilenenden."""
    n = LineEndingNormalizer(strip_control=strip_control)
    return n.feed(text) + n.flush()


def decode_bytes(data: bytes) -> str:
    """Bytes aus Datei oder Schnittstelle in Text umwandeln (nie ein Fehler)."""
    return data.decode(ENCODING, errors="replace")


def load_program_file(path: str) -> str:
    """Programmdatei lesen und Zeilenenden vereinheitlichen."""
    with open(path, "rb") as fh:
        raw = fh.read()
    # Ein evtl. vorhandenes UTF-8-BOM entfernen und UTF-8 bevorzugen, falls gültig
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = decode_bytes(raw)
    return normalize_text(text, strip_control=True)


def save_program_file(path: str, text: str, eol: str = "\r\n") -> None:
    """Programm mit einheitlichen Zeilenenden speichern."""
    text = normalize_text(text, strip_control=False)
    if text and not text.endswith("\n"):
        text += "\n"
    data = text.replace("\n", eol).encode(ENCODING, errors="replace")
    with open(path, "wb") as fh:
        fh.write(data)


# --------------------------------------------------------------------------
# Senden
# --------------------------------------------------------------------------

def prepare_lines(
    text: str,
    frame_percent: bool = False,
    strip_trailing_spaces: bool = False,
    skip_empty_lines: bool = False,
) -> List[str]:
    """Zeilen für den Versand aufbereiten (ohne Zeilenende)."""
    lines = normalize_text(text, strip_control=False).split("\n")
    if strip_trailing_spaces:
        lines = [ln.rstrip() for ln in lines]
    # Leere Zeilen am Anfang und Ende sind nie Teil des Programms
    while lines and not lines[-1].strip():
        lines.pop()
    while lines and not lines[0].strip():
        lines.pop(0)
    if skip_empty_lines:
        lines = [ln for ln in lines if ln.strip()]
    # Sinumerik-Köpfe wie "%_N_TEIL_MPF" beginnen auch mit %, dürfen aber nicht
    # zusätzlich eingerahmt werden – sonst lehnt die Steuerung das Programm ab.
    sinumerik_head = bool(lines) and lines[0].lstrip().startswith("%") and lines[0].strip() != "%"
    if frame_percent and not sinumerik_head:
        if not lines or lines[0].strip() != "%":
            lines.insert(0, "%")
        if len(lines) < 2 or lines[-1].strip() != "%":
            lines.append("%")
    return lines


def build_send_chunks(
    text: str,
    eol: str = "CRLF",
    frame_percent: bool = False,
    strip_trailing_spaces: bool = False,
    skip_empty_lines: bool = False,
    leader_nuls: int = 0,
    trailer_nuls: int = 0,
) -> List[bytes]:
    """Text in eine Liste von Byte-Blöcken (eine Zeile pro Block) umwandeln."""
    eol_str = EOL_CHOICES.get(eol, "\r\n")
    lines = prepare_lines(text, frame_percent, strip_trailing_spaces, skip_empty_lines)
    chunks: List[bytes] = []
    if leader_nuls > 0:
        chunks.append(b"\x00" * leader_nuls)
    for ln in lines:
        chunks.append((ln + eol_str).encode(ENCODING, errors="replace"))
    if trailer_nuls > 0:
        chunks.append(b"\x00" * trailer_nuls)
    return chunks


def find_problem_chars(text: str, limit: int = 20) -> List[Tuple[int, str]]:
    """Zeichen finden, die viele Steuerungen nicht verstehen (Umlaute, Ø, ...).

    Liefert eine Liste von (Zeilennummer, Zeichen).
    """
    found: List[Tuple[int, str]] = []
    for no, line in enumerate(text.split("\n"), start=1):
        for ch in line:
            if ord(ch) > 126 or (ord(ch) < 32 and ch != "\t"):
                found.append((no, ch))
                break
        if len(found) >= limit:
            break
    return found


# --------------------------------------------------------------------------
# Empfang: Programmende erkennen
# --------------------------------------------------------------------------

END_MODES = {
    "percent": "Beim zweiten % (Fanuc/ISO)",
    "endpgm": "Bei END PGM (Heidenhain)",
    "m30": "Nach M30 / M02",
    "none": "Nur nach Zeit ohne Daten",
}

_END_PGM_RE = re.compile(r"^\s*\d*\s*END\s+PGM\b", re.IGNORECASE)
_M30_RE = re.compile(r"(?<![A-Z0-9.=])M0*(30|2)(?![0-9=])", re.IGNORECASE)


class EndDetector:
    """Erkennt im empfangenen (normalisierten) Text das Programmende.

    ``feed`` liefert den aktuellen Stand. Im %-Modus kann ein Ende auch wieder
    zurückgenommen werden: Ein ``%`` am Zeilenanfang zählt nur, wenn danach
    nichts weiter in der Zeile steht. So beendet ein Sinumerik-Kopf wie
    ``%_N_TEIL_MPF`` den Empfang nicht vorzeitig.
    """

    def __init__(self, mode: str = "none") -> None:
        self.mode = mode
        self.percent_count = 0
        self.pending_percent = False
        self.at_line_start = True
        self.partial = ""
        self.found = False

    def feed(self, text: str) -> bool:
        if self.mode == "none" or not text:
            return self.found
        if self.mode == "percent":
            for ch in text:
                if ch == "\n":
                    if self.pending_percent:
                        self.percent_count += 1
                        self.pending_percent = False
                    self.at_line_start = True
                elif self.pending_percent:
                    if ch not in " \t\r":
                        self.pending_percent = False  # z. B. "%_N_..." – kein Programmende
                        self.at_line_start = False
                elif self.at_line_start:
                    if ch == "%":
                        self.pending_percent = True
                    elif ch not in " \t\r":
                        self.at_line_start = False
            self.found = self.percent_count + (1 if self.pending_percent else 0) >= 2
            return self.found
        if self.found:
            return True
        # zeilenweise Modi
        data = self.partial + text
        lines = data.split("\n")
        self.partial = lines.pop()
        for line in lines:
            if self._line_is_end(line):
                self.found = True
                break
        return self.found

    def _line_is_end(self, line: str) -> bool:
        code = re.sub(r"\([^)]*\)|;.*$", "", line)  # Kommentare ignorieren
        if self.mode == "endpgm":
            return bool(_END_PGM_RE.match(line))
        if self.mode == "m30":
            return bool(_M30_RE.search(code))
        return False


# --------------------------------------------------------------------------
# Programm analysieren: Name, Steuerung, enthaltene Programme
# --------------------------------------------------------------------------

_INVALID_FS = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def safe_filename(name: str, max_len: int = 60) -> str:
    name = _INVALID_FS.sub("_", name).strip(" ._")
    name = re.sub(r"\s+", "_", name)
    name = re.sub(r"_+", "_", name)
    return name[:max_len].strip("_")


DIALECT_NAMES = {"sinumerik": "Sinumerik", "fanuc": "Fanuc / ISO", "heidenhain": "Heidenhain"}

_SIN_HEAD = re.compile(r"^%_N_(\w+)_([A-Za-z]{3})\s*$")          # %_N_KANAL1_MPF
_SIN_OLD_HEAD = re.compile(r"^%((?:MPF|SPF)\d+)\s*$", re.IGNORECASE)  # %MPF100
_SIN_PATH = re.compile(r"^;\s*\$PATH\s*=\s*(\S+)", re.IGNORECASE)  # ;$PATH=/_N_WKS_DIR/_N_TEIL_WPD
_SIN_WPD = re.compile(r"_N_(\w+?)_WPD\b", re.IGNORECASE)
_HEID_HEAD = re.compile(r"^\d*\s*BEGIN\s+PGM\s+(\S+)", re.IGNORECASE)
_FANUC_O = re.compile(r"^([O:])\s*(\d{1,8})\s*(?:\((.*?)\)?)?\s*$", re.IGNORECASE)
_FANUC_NAME = re.compile(r"^<([^>]+)>")
_PAREN_COMMENT = re.compile(r"\(([^)]+)\)")


class ProgramInfo:
    """Was in einem Programmtext steckt."""

    def __init__(self) -> None:
        self.name: Optional[str] = None       # Vorschlag für den Dateinamen
        self.dialect: Optional[str] = None    # "sinumerik" | "fanuc" | "heidenhain" | None
        self.workpiece: Optional[str] = None  # Sinumerik-Werkstück (…_WPD)
        self.programs: List[Tuple[int, str, str]] = []  # (Zeile, Name, Typ z. B. "MPF")

    def summary(self) -> str:
        parts = []
        if self.workpiece:
            parts.append("Werkstück " + self.workpiece)
        elif self.name:
            parts.append(self.name)
        if self.dialect:
            parts.append(DIALECT_NAMES[self.dialect])
        n = len(self.programs)
        if n > 1:
            parts.append("{} Programme".format(n))
        return "  ·  ".join(parts)


def analyze_program(text: str) -> ProgramInfo:
    """Programmname, Steuerungsart und alle enthaltenen (Unter-)Programme finden.

    Beispiele für den Namen:
      Sinumerik-Werkstück  ;$PATH=/_N_WKS_DIR/_N_METLOG_300069_WPD  -> METLOG_300069
      Sinumerik            %_N_WELLE_MPF                            -> WELLE
      Heidenhain           0 BEGIN PGM TEIL1 MM                     -> TEIL1
      Fanuc                O1234 (FLANSCH)                          -> O1234_FLANSCH
    """
    info = ProgramInfo()
    first_name: Optional[str] = None
    first_comment: Optional[str] = None
    fanuc_hint = False
    for no, line in enumerate(text.split("\n"), start=1):
        s = line.strip()
        if not s:
            continue
        c = s[0]
        if c == "%":
            if s == "%":
                fanuc_hint = True
                continue
            m = _SIN_HEAD.match(s) or _SIN_OLD_HEAD.match(s)
            if m:
                info.dialect = "sinumerik"
                typ = m.group(2).upper() if m.lastindex and m.lastindex >= 2 else m.group(1)[:3].upper()
                info.programs.append((no, m.group(1), typ))
                first_name = first_name or m.group(1)
            continue
        if c == ";":
            m = _SIN_PATH.match(s)
            if m:
                info.dialect = "sinumerik"
                w = _SIN_WPD.search(m.group(1))
                if w and not info.workpiece:
                    info.workpiece = w.group(1)
            continue
        if c in "Oo:":
            m = _FANUC_O.match(s)
            if m:
                name = "O" + m.group(2)
                info.programs.append((no, name + (" (" + m.group(3).strip() + ")" if m.group(3) else ""), ""))
                if first_name is None:
                    first_name = name + ("_" + m.group(3) if m.group(3) and m.group(3).strip() else "")
                fanuc_hint = True
                continue
        if c == "<":
            m = _FANUC_NAME.match(s)
            if m:
                info.programs.append((no, m.group(1), ""))
                first_name = first_name or m.group(1)
                fanuc_hint = True
                continue
        if c.isdigit() or c in "Bb":
            m = _HEID_HEAD.match(s)
            if m:
                info.dialect = info.dialect or "heidenhain"
                info.programs.append((no, m.group(1), "PGM"))
                first_name = first_name or m.group(1)
                continue
        if first_comment is None and no <= 40 and "(" in s:
            m = _PAREN_COMMENT.search(s)
            if m and m.group(1).strip():
                first_comment = m.group(1)

    if info.dialect is None and fanuc_hint:
        info.dialect = "fanuc"
    raw = info.workpiece or first_name or first_comment
    info.name = safe_filename(raw) if raw else None
    return info


def detect_program_name(text: str) -> Optional[str]:
    """Vorschlag für den Dateinamen (siehe :func:`analyze_program`)."""
    return analyze_program(text).name


# --------------------------------------------------------------------------
# Programm zusammenrücken
# --------------------------------------------------------------------------

_TOOL_CALL = re.compile(r"(?<![A-Za-z_$])T\s*(?:=|\d)|\bTOOL\s+CALL\b", re.IGNORECASE)
_CODE_ONLY = re.compile(r"\([^)]*\)|;.*$|\"[^\"]*\"")


def _is_comment_only(s: str) -> bool:
    return s.startswith(";") or (s.startswith("(") and s.endswith(")"))


def _is_path_line(s: str) -> bool:
    return bool(_SIN_PATH.match(s))


def _is_program_header(s: str) -> bool:
    if s.startswith("%"):
        return s != "%"
    return bool(_FANUC_O.match(s) or _FANUC_NAME.match(s) or _HEID_HEAD.match(s))


def is_tool_call(line: str) -> bool:
    """Wird in dieser Zeile ein Werkzeug aufgerufen? (T12D1, T01 M06, T="BOHRER", TOOL CALL 5)

    Kommentare zählen nicht: ``(WERKZEUG T01)`` ist kein Werkzeugaufruf.
    """
    return bool(_TOOL_CALL.search(_CODE_ONLY.sub("", line)))


class CompactResult:
    def __init__(self, text: str, removed: int, tool_gaps: int, program_gaps: int, tools: int) -> None:
        self.text = text
        self.removed = removed            # entfernte Leerzeilen (aus dem Original)
        self.tool_gaps = tool_gaps        # Abstand vor Werkzeugaufrufen eingefügt
        self.program_gaps = program_gaps  # Abstand vor weiteren Programmen eingefügt
        self.tools = tools                # gefundene Werkzeugaufrufe


def compact_program(text: str, blank_lines: int = 2, dialect: Optional[str] = None) -> CompactResult:
    """Leerzeilen entfernen, nur vor Werkzeugaufrufen ``blank_lines`` Leerzeilen lassen.

    - Kommentarzeilen direkt über einem Werkzeug (z. B. ``;FERTIGDREHEN VCMT04``)
      gehören zum Werkzeug – der Abstand kommt über den Kommentar.
    - Direkt nach einem Programmkopf (``%_N_..._MPF``, ``;$PATH=``, ``O1234``)
      wird kein Abstand eingefügt.
    - Vor jedem weiteren Programm einer Datei (z. B. ``%_N_KANAL2_MPF``) kommt
      ebenfalls Abstand, damit die Unterprogramme getrennt bleiben.
    - Heidenhain: Leerzeilen sind dort nicht erlaubt, es werden nur welche entfernt.
    - Leerzeichen am Zeilenende werden entfernt, Leerzeichen innerhalb der Zeile nicht.
    """
    if dialect == "heidenhain":
        blank_lines = 0
    norm = normalize_text(text, strip_control=False)
    ends_with_newline = norm.endswith("\n")
    lines = [ln.rstrip() for ln in norm.split("\n")]
    if ends_with_newline:
        lines.pop()
    removed = sum(1 for ln in lines if not ln.strip())
    gap = [""] * blank_lines

    out: List[str] = []
    gaps = {"tool": 0, "program": 0}
    tools = 0
    has_content = False   # schon richtiger Programminhalt da (nicht nur "%")?
    header_zone = True    # direkt nach Programmanfang/-kopf: kein Abstand

    def insert_gap(position: int, kind: str) -> None:
        if not blank_lines:
            return
        out[position:position] = gap
        gaps[kind] += 1

    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        if s == "%":
            out.append(ln)
            continue
        if _is_program_header(s):
            if has_content:
                insert_gap(len(out), "program")
            out.append(ln)
            has_content = True
            header_zone = True
            continue
        if _is_path_line(s):
            out.append(ln)
            has_content = True
            continue
        if is_tool_call(s):
            tools += 1
            if not header_zone and has_content:
                # Kommentarzeilen direkt darüber gehören zum Werkzeug
                pos = len(out)
                while pos > 0 and out[pos - 1].strip() and _is_comment_only(out[pos - 1].strip()) \
                        and not _is_path_line(out[pos - 1].strip()):
                    pos -= 1
                insert_gap(pos, "tool")
            out.append(ln)
            has_content = True
            header_zone = False
            continue
        out.append(ln)
        has_content = True
        if not _is_comment_only(s):
            header_zone = False

    result = "\n".join(out) + ("\n" if ends_with_newline and out else "")
    return CompactResult(result, removed, gaps["tool"], gaps["program"], tools)


def count_lines(text: str) -> int:
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)
