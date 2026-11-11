"""Kleiner PDF-Erzeuger ohne Zusatzbibliotheken (läuft auch mit Python 3.8 unter Windows 7).

Kann genau das, was für das Einrichteblatt nötig ist: Text in Helvetica
(normal/fett, mit Umlauten), Linien und Rechtecke, mehrere A4-Seiten.
Koordinaten werden von **oben links** in Punkt (1/72 Zoll) angegeben.
"""

from __future__ import annotations

import zlib
from typing import List, Optional, Tuple

from .pdfmetrics import HELVETICA, HELVETICA_BOLD

A4 = (595.28, 841.89)
MM = 72.0 / 25.4

Color = Tuple[float, float, float]


def hex_color(value: str) -> Color:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def _encode(text: str) -> bytes:
    return text.encode("cp1252", errors="replace")


def string_width(text: str, size: float, bold: bool = False) -> float:
    table = HELVETICA_BOLD if bold else HELVETICA
    total = 0
    for b in _encode(text):
        total += table[b - 32] if 32 <= b <= 255 else 0
    return total * size / 1000.0


def fit_text(text: str, max_width: float, size: float, bold: bool = False,
             min_size: float = 6.5) -> Tuple[str, float]:
    """Text passend machen: erst Schrift verkleinern, dann mit „…“ kürzen."""
    s = size
    while s > min_size and string_width(text, s, bold) > max_width:
        s -= 0.5
    if string_width(text, s, bold) <= max_width:
        return text, s
    while text and string_width(text + "…", s, bold) > max_width:
        text = text[:-1]
    return text.rstrip() + "…", s


def wrap_text(text: str, max_width: float, size: float, bold: bool = False) -> List[str]:
    """Zeilenumbruch an Leerzeichen (für Notizen)."""
    lines: List[str] = []
    for para in text.split("\n"):
        words = para.split(" ")
        cur = ""
        for w in words:
            cand = w if not cur else cur + " " + w
            if string_width(cand, size, bold) <= max_width or not cur:
                cur = cand
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
    return lines


def _pdf_string(text: str) -> bytes:
    raw = _encode(text)
    return b"(" + raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)") + b")"


def _num(v: float) -> str:
    s = "{:.2f}".format(v).rstrip("0").rstrip(".")
    return s if s not in ("-0", "") else "0"


class PdfDocument:
    def __init__(self, title: str = "", size: Tuple[float, float] = A4) -> None:
        self.width, self.height = size
        self.title = title
        self.pages: List[List[str]] = []
        self.current = -1
        self.new_page()

    # -- Seiten ------------------------------------------------------------------

    def new_page(self) -> None:
        self.pages.append([])
        self.current = len(self.pages) - 1

    def set_page(self, index: int) -> None:
        """Auf eine frühere Seite zurückgehen (z. B. für „Seite 1 von 2“ in der Fußzeile)."""
        self.current = index

    @property
    def _ops(self) -> List[str]:
        return self.pages[self.current]

    def _y(self, y_top: float) -> float:
        return self.height - y_top

    # -- Zeichnen ----------------------------------------------------------------

    def text(self, x: float, y: float, text: str, size: float = 10, bold: bool = False,
             color: Color = (0, 0, 0), align: str = "left") -> None:
        """Text mit Grundlinie bei y (von oben gemessen)."""
        if not text:
            return
        if align != "left":
            w = string_width(text, size, bold)
            x = x - w if align == "right" else x - w / 2
        font = "F2" if bold else "F1"
        self._ops.append("BT {} {} {} rg /{} {} Tf {} {} Td {} Tj ET".format(
            _num(color[0]), _num(color[1]), _num(color[2]), font, _num(size), _num(x), _num(self._y(y)),
            _pdf_string(text).decode("latin-1")))

    def line(self, x1: float, y1: float, x2: float, y2: float, width: float = 0.5,
             color: Color = (0, 0, 0)) -> None:
        self._ops.append("{} {} {} RG {} w {} {} m {} {} l S".format(
            _num(color[0]), _num(color[1]), _num(color[2]), _num(width),
            _num(x1), _num(self._y(y1)), _num(x2), _num(self._y(y2))))

    def rect(self, x: float, y: float, w: float, h: float, fill: Optional[Color] = None,
             stroke: Optional[Color] = None, width: float = 0.5) -> None:
        """Rechteck mit oberer linker Ecke (x, y)."""
        parts = []
        if fill is not None:
            parts.append("{} {} {} rg".format(*(_num(c) for c in fill)))
        if stroke is not None:
            parts.append("{} {} {} RG {} w".format(*(_num(c) for c in stroke), _num(width)))
        parts.append("{} {} {} {} re".format(_num(x), _num(self._y(y + h)), _num(w), _num(h)))
        parts.append("B" if fill is not None and stroke is not None else ("f" if fill is not None else "S"))
        self._ops.append(" ".join(parts))

    # -- Ausgabe -----------------------------------------------------------------

    def to_bytes(self) -> bytes:
        objects: List[bytes] = []

        def add(obj: bytes) -> int:
            objects.append(obj)
            return len(objects)

        font1 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
        font2 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
        pages_id = len(objects) + 1 + 2 * len(self.pages)  # wird unten als letztes Seitenobjekt angelegt
        page_ids = []
        for ops in self.pages:
            stream = zlib.compress("\n".join(ops).encode("latin-1"))
            content = add(b"<< /Length " + str(len(stream)).encode() + b" /Filter /FlateDecode >>\nstream\n"
                          + stream + b"\nendstream")
            page = add("<< /Type /Page /Parent {} 0 R /MediaBox [0 0 {} {}] /Contents {} 0 R "
                       "/Resources << /Font << /F1 {} 0 R /F2 {} 0 R >> >> >>".format(
                           pages_id, _num(self.width), _num(self.height), content, font1, font2).encode())
            page_ids.append(page)
        kids = " ".join("{} 0 R".format(p) for p in page_ids)
        real_pages_id = add("<< /Type /Pages /Kids [{}] /Count {} >>".format(kids, len(page_ids)).encode())
        assert real_pages_id == pages_id
        title = _pdf_string(self.title).decode("latin-1")
        info = add("<< /Title {} /Producer (CNC-Datenuebertragung) >>".format(title).encode("latin-1"))
        catalog = add("<< /Type /Catalog /Pages {} 0 R >>".format(pages_id).encode())

        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, obj in enumerate(objects, start=1):
            offsets.append(len(out))
            out += "{} 0 obj\n".format(i).encode() + obj + b"\nendobj\n"
        xref = len(out)
        out += "xref\n0 {}\n".format(len(objects) + 1).encode()
        out += b"0000000000 65535 f \n"
        for off in offsets:
            out += "{:010d} 00000 n \n".format(off).encode()
        out += "trailer\n<< /Size {} /Root {} 0 R /Info {} 0 R >>\nstartxref\n{}\n%%EOF\n".format(
            len(objects) + 1, catalog, info, xref).encode()
        return bytes(out)

    def save(self, path: str) -> None:
        data = self.to_bytes()
        with open(path, "wb") as fh:
            fh.write(data)
