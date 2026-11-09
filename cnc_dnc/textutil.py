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
    if frame_percent:
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
    "m30": "Nach M30 / M02 / M17",
    "none": "Nur nach Zeit ohne Daten",
}

_END_PGM_RE = re.compile(r"^\s*\d*\s*END\s+PGM\b", re.IGNORECASE)
_M30_RE = re.compile(r"(?<![A-Z0-9.])M0*(30|2|17)(?![0-9])", re.IGNORECASE)


class EndDetector:
    """Erkennt im empfangenen (normalisierten) Text das Programmende."""

    def __init__(self, mode: str = "none") -> None:
        self.mode = mode
        self.percent_count = 0
        self.in_comment = False
        self.partial = ""
        self.found = False

    def feed(self, text: str) -> bool:
        if self.found or self.mode == "none" or not text:
            return self.found
        if self.mode == "percent":
            for ch in text:
                if ch == "(":
                    self.in_comment = True
                elif ch == ")":
                    self.in_comment = False
                elif ch == "\n":
                    self.in_comment = False
                elif ch == "%" and not self.in_comment:
                    self.percent_count += 1
                    if self.percent_count >= 2:
                        self.found = True
                        break
            return self.found
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
# Programmname / Dateiname
# --------------------------------------------------------------------------

_INVALID_FS = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def safe_filename(name: str, max_len: int = 60) -> str:
    name = _INVALID_FS.sub("_", name).strip(" ._")
    name = re.sub(r"\s+", "_", name)
    name = re.sub(r"_+", "_", name)
    return name[:max_len].strip("_")


def detect_program_name(text: str) -> Optional[str]:
    """Programmnamen aus dem Programmkopf ableiten (z. B. ``O1234_FLANSCH``)."""
    head = text.split("\n", 40)[:40]
    for line in head:
        s = line.strip()
        if not s or s == "%":
            continue
        # Sinumerik: %_N_TEIL1_MPF
        m = re.match(r"%_N_(\w+?)_(MPF|SPF)\b", s, re.IGNORECASE)
        if m:
            return safe_filename(m.group(1))
        # Heidenhain: 0 BEGIN PGM TEIL1 MM
        m = re.match(r"\d*\s*BEGIN\s+PGM\s+(\S+)", s, re.IGNORECASE)
        if m:
            return safe_filename(m.group(1))
        # Fanuc: O1234 (KOMMENTAR)  oder  :1234
        m = re.match(r"([O:])\s*(\d{1,8})\s*(?:\((.*?)\)?)?\s*$", s, re.IGNORECASE)
        if m:
            name = "O" + m.group(2)
            if m.group(3):
                name += "_" + m.group(3)
            return safe_filename(name)
        # Fanuc 30i: <PROGRAMMNAME>
        m = re.match(r"<([^>]+)>", s)
        if m:
            return safe_filename(m.group(1))
    # Fallback: erster Kommentar
    for line in head:
        m = re.search(r"\(([^)]+)\)", line)
        if m and m.group(1).strip():
            return safe_filename(m.group(1))
    return None


def count_lines(text: str) -> int:
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)
