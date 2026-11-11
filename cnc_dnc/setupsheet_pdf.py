"""Einrichteblatt als PDF (A4 hoch): Kopf, Teil, Spannung, Werkzeuge je Seite, Parameter, Notizen."""

from __future__ import annotations

import datetime
import os
from typing import Dict, List, Optional, Sequence, Tuple

from .pdfwriter import A4, PdfDocument, fit_text, hex_color, string_width, wrap_text
from .setupsheet import KorrRow, Parameter, SheetData, ToolUse, side_title, tools_by_side

DARK = hex_color("#18222E")
PRIMARY = hex_color("#1C64D8")
GRID = hex_color("#B9C2CE")
HEAD_BG = hex_color("#EEF2F7")
TEXT = hex_color("#16202B")
MUTED = hex_color("#5D6A79")
FAINT = hex_color("#A3AFBD")
WHITE = (1.0, 1.0, 1.0)

W, H = A4
M = 34.0            # Seitenrand
BOTTOM = H - 40.0   # unterste nutzbare Linie (darunter Fußzeile)
ROW = 18.0          # Zeilenhöhe Tabellen


class SheetContext:
    def __init__(self, program: str, machine: str = "", customer: str = "", file_name: str = "") -> None:
        self.program = program
        self.machine = machine
        self.customer = customer
        self.file_name = file_name
        self.created = datetime.datetime.now()


class _Layout:
    def __init__(self, doc: PdfDocument, ctx: SheetContext, label: str = "EINRICHTEBLATT") -> None:
        self.doc = doc
        self.ctx = ctx
        self.label = label
        self.y = M
        self._on_new_page = None

    @property
    def W(self) -> float:  # noqa: N802 (wie die Konstante)
        return self.doc.width

    @property
    def bottom(self) -> float:
        return self.doc.height - 40.0

    def ensure(self, height: float) -> bool:
        """Platz prüfen; sonst neue Seite. Liefert True, wenn umgebrochen wurde."""
        if self.y + height <= self.bottom:
            return False
        self.doc.new_page()
        self.y = M
        d = self.doc
        d.text(M, self.y + 12, self.label, 8, bold=True, color=MUTED)
        d.text(M + 98, self.y + 12, self.ctx.program + "  (Fortsetzung)", 10, bold=True, color=TEXT)
        d.line(M, self.y + 20, self.W - M, self.y + 20, 0.6, GRID)
        self.y += 30
        if self._on_new_page:
            self._on_new_page()
        return True

    # -- Bausteine -----------------------------------------------------------------

    def section(self, title: str, hint: str = "") -> None:
        self.ensure(22 + ROW * 2)
        self.doc.text(M, self.y + 10, title, 8.5, bold=True, color=PRIMARY)
        if hint:
            self.doc.text(W - M, self.y + 10, hint, 7.5, color=MUTED, align="right")
        self.y += 15

    def cell(self, x: float, y: float, w: float, h: float, text: str, size: float = 9.5, bold: bool = False,
             bg: Optional[Tuple[float, float, float]] = None, color=TEXT, align: str = "left") -> None:
        self.doc.rect(x, y, w, h, fill=bg, stroke=GRID, width=0.5)
        if text:
            t, s = fit_text(text, w - 8, size, bold)
            tx = x + 4 if align == "left" else (x + w / 2 if align == "center" else x + w - 4)
            self.doc.text(tx, y + h / 2 + s * 0.35, t, s, bold=bold, color=color, align=align)


def _draw_header(lay: _Layout) -> None:
    d, ctx = lay.doc, lay.ctx
    W = lay.W  # noqa: N806
    h = 56.0
    d.rect(M, lay.y, W - 2 * M, h, fill=DARK)
    d.text(M + 14, lay.y + 19, lay.label, 8.5, bold=True, color=FAINT)
    name, size = fit_text(ctx.program or "Programm", 300, 22, bold=True, min_size=12)
    d.text(M + 14, lay.y + 43, name, size, bold=True, color=WHITE)
    rows = [("Maschine", ctx.machine), ("Kunde", ctx.customer),
            ("Erstellt", ctx.created.strftime("%d.%m.%Y %H:%M"))]
    yy = lay.y + 18
    for label, value in rows:
        if not value:
            continue
        v, s = fit_text(value, 150, 9.5, bold=True)
        d.text(W - M - 14, yy, v, s, bold=True, color=WHITE, align="right")
        d.text(W - M - 14 - 158, yy, label, 8, color=FAINT, align="left")
        yy += 14
    lay.y += h + 12


def _draw_part(lay: _Layout, data: SheetData) -> None:
    f = data.fields
    lay.section("TEIL")
    lw = 92.0
    vw = (W - 2 * M - 2 * lw) / 2
    rows = [
        ("Teil / Benennung", f.get("teil", ""), "Zeichnungs-Nr.", f.get("zeichnung", "")),
        ("Material", f.get("material", ""), "Rohteil", f.get("rohteil", "")),
        ("Stückzahl", f.get("stueckzahl", ""), "Eingerichtet von", f.get("eingerichtet_von", "")),
        ("Datum", f.get("datum", ""), "Programmdatei", lay.ctx.file_name),
    ]
    for l1, v1, l2, v2 in rows:
        lay.ensure(ROW)
        x = M
        lay.cell(x, lay.y, lw, ROW, l1, 8, bold=True, bg=HEAD_BG, color=MUTED)
        lay.cell(x + lw, lay.y, vw, ROW, v1)
        lay.cell(x + lw + vw, lay.y, lw, ROW, l2, 8, bold=True, bg=HEAD_BG, color=MUTED)
        lay.cell(x + 2 * lw + vw, lay.y, vw, ROW, v2)
        lay.y += ROW
    lay.y += 12


def _bar(value: str) -> str:
    v = value.strip()
    if v and v.replace(",", ".").replace(".", "", 1).isdigit():
        return v + " bar"
    return v


def _draw_clamping(lay: _Layout, data: SheetData) -> None:
    f = data.fields
    lay.section("SPANNUNG")
    w0, w1, w2 = 110.0, 150.0, 90.0
    w3 = W - 2 * M - w0 - w1 - w2
    lay.ensure(ROW * 3)
    x = M
    for text, w in (("", w0), ("Spannmittel", w1), ("Spanndruck", w2), ("Bemerkung", w3)):
        lay.cell(x, lay.y, w, ROW, text, 8, bold=True, bg=HEAD_BG, color=MUTED)
        x += w
    lay.y += ROW
    for label, prefix in (("Hauptspindel (S1)", "hsp"), ("Gegenspindel (S2)", "gsp")):
        x = M
        lay.cell(x, lay.y, w0, ROW, label, 9, bold=True)
        lay.cell(x + w0, lay.y, w1, ROW, f.get(prefix + "_spannmittel", ""))
        lay.cell(x + w0 + w1, lay.y, w2, ROW, _bar(f.get(prefix + "_druck", "")), bold=True)
        lay.cell(x + w0 + w1 + w2, lay.y, w3, ROW, f.get(prefix + "_bemerkung", ""))
        lay.y += ROW
    lay.y += 12


def _draw_tool_tables(lay: _Layout, tables: Sequence[Tuple[str, str, List[Tuple[str, str, str]]]],
                      extra_rows: int = 2) -> None:
    """Werkzeugtabellen nebeneinander (wie auf dem Zettel: S1 | S2)."""
    n = len(tables)
    gap = 12.0
    tw = (W - 2 * M - gap * (n - 1)) / n
    t_w = 40.0 if n > 1 else 50.0
    desc_w = (tw - t_w) * (0.58 if n > 1 else 0.55)
    rem_w = tw - t_w - desc_w

    def heads() -> None:
        for i, (title, sub, _rows) in enumerate(tables):
            x = M + i * (tw + gap)
            lay.doc.rect(x, lay.y, tw, 22, fill=DARK)
            lay.doc.text(x + 6, lay.y + 15, title, 10.5, bold=True, color=WHITE)
            if sub:
                s, size = fit_text(sub, tw - 100, 8)
                lay.doc.text(x + tw - 6, lay.y + 15, s, size, color=FAINT, align="right")
            yy = lay.y + 22
            lay.cell(x, yy, t_w, 16, "T", 7.5, bold=True, bg=HEAD_BG, color=MUTED, align="center")
            lay.cell(x + t_w, yy, desc_w, 16, "Bearbeitung", 7.5, bold=True, bg=HEAD_BG, color=MUTED)
            lay.cell(x + t_w + desc_w, yy, rem_w, 16, "Bemerkung", 7.5, bold=True, bg=HEAD_BG, color=MUTED)
        lay.y += 38

    lay.ensure(38 + ROW * 2)
    heads()
    lay._on_new_page = heads
    rows_total = max(len(t[2]) for t in tables) + extra_rows
    for r in range(rows_total):
        lay.ensure(ROW)
        for i, (_title, _sub, rows) in enumerate(tables):
            x = M + i * (tw + gap)
            tool, desc, rem = rows[r] if r < len(rows) else ("", "", "")
            lay.cell(x, lay.y, t_w, ROW, tool, 10, bold=True, align="center")
            lay.cell(x + t_w, lay.y, desc_w, ROW, desc, 9.5)
            lay.cell(x + t_w + desc_w, lay.y, rem_w, ROW, rem, 9, color=PRIMARY)
        lay.y += ROW
    lay._on_new_page = None
    lay.y += 12


def _draw_parameters(lay: _Layout, params: List[Parameter]) -> None:
    rh = 14.0
    cols = 2 if len(params) > 6 else 1
    per_col = (len(params) + cols - 1) // cols
    gap = 10.0
    cw = (W - 2 * M - gap * (cols - 1)) / cols
    w0, w1 = (70.0, 42.0) if cols == 2 else (110.0, 70.0)
    w2 = cw - w0 - w1
    height = 15 + rh * (per_col + 1)
    if lay.y + height > BOTTOM and height < 260:
        lay.ensure(BOTTOM)  # kurzen Block nicht zerreißen -> komplett auf die nächste Seite
    lay.section("PARAMETER AUS DEM PROGRAMM")

    def head() -> None:
        for c in range(cols):
            x = M + c * (cw + gap)
            lay.cell(x, lay.y, w0, rh, "Parameter", 7, bold=True, bg=HEAD_BG, color=MUTED)
            lay.cell(x + w0, lay.y, w1, rh, "Wert", 7, bold=True, bg=HEAD_BG, color=MUTED, align="right")
            lay.cell(x + w0 + w1, lay.y, w2, rh, "Bedeutung (Kommentar im Programm)", 7, bold=True, bg=HEAD_BG,
                     color=MUTED)
        lay.y += rh

    lay.ensure(rh * 3)
    head()
    lay._on_new_page = head
    for r in range(per_col):
        lay.ensure(rh)
        for c in range(cols):
            i = c * per_col + r
            if i >= len(params):
                continue
            p = params[i]
            x = M + c * (cw + gap)
            lay.cell(x, lay.y, w0, rh, p.name, 7.5, color=MUTED)
            lay.cell(x + w0, lay.y, w1, rh, p.value, 8.5, bold=True, align="right")
            lay.cell(x + w0 + w1, lay.y, w2, rh, p.comment, 7.5)
        lay.y += rh
    lay._on_new_page = None
    lay.y += 12


def _draw_notes(lay: _Layout, notes: str) -> None:
    lay.section("NOTIZEN")
    line_h = 18.0
    lines = wrap_text(notes.strip(), W - 2 * M - 12, 9.5) if notes.strip() else []
    # mindestens 5 Schreiblinien, sonst bis zum Seitenende
    free = max(3, int((BOTTOM - lay.y - 4) // line_h))
    total = max(len(lines) + 1, free)
    for i in range(total):
        lay.ensure(line_h)
        if i < len(lines):
            lay.doc.text(M + 6, lay.y + 13, lines[i], 9.5, color=TEXT)
        lay.doc.line(M, lay.y + line_h, W - M, lay.y + line_h, 0.4, GRID)
        lay.y += line_h


def _draw_footers(doc: PdfDocument, ctx: SheetContext, label: str = "Einrichteblatt") -> None:
    W, H = doc.width, doc.height  # noqa: N806
    n = len(doc.pages)
    for i in range(n):
        doc.set_page(i)
        y = H - 20
        doc.line(M, y - 10, W - M, y - 10, 0.4, GRID)
        doc.text(M, y, "{} {}  ·  erstellt mit CNC-Datenübertragung  ·  {}".format(
            label, ctx.program, ctx.created.strftime("%d.%m.%Y")), 7, color=MUTED)
        doc.text(W - M, y, "Seite {} von {}".format(i + 1, n), 7, color=MUTED, align="right")


def render_setup_sheet(path: str, ctx: SheetContext, tools: List[ToolUse], params: List[Parameter],
                       data: SheetData) -> None:
    doc = PdfDocument(title="Einrichteblatt " + ctx.program)
    lay = _Layout(doc, ctx)
    _draw_header(lay)
    _draw_part(lay, data)
    _draw_clamping(lay, data)

    groups: Dict[int, List[ToolUse]] = tools_by_side(tools)
    lay.section("WERKZEUGE", "Bearbeitung = Kommentar über dem Werkzeug im Programm")

    def rows(side: int) -> List[Tuple[str, str, str]]:
        out = []
        for t in groups.get(side, []):
            e = data.tool_entry(t)
            out.append((t.tool, e["description"], e["remark"]))
        return out

    sides = [s for s in (1, 2) if s in groups]
    if not sides and 0 not in groups:
        sides = [1]
    if sides:
        tables = []
        for s in sides:
            title, sub = side_title(s, groups.get(s, []))
            tables.append((title, sub, rows(s)))
        if len(tables) == 1 and 2 not in groups and 1 in groups:
            tables[0] = ("WERKZEUGE", tables[0][1], tables[0][2])
        _draw_tool_tables(lay, tables)
    if 0 in groups:
        title, sub = side_title(0, groups[0])
        _draw_tool_tables(lay, [(title, sub, rows(0))], extra_rows=0)
    if params:
        _draw_parameters(lay, params)
    _draw_notes(lay, data.notes)
    _draw_footers(doc, ctx)
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    doc.save(path)


# --------------------------------------------------------------------------
# Korrekturblatt: Seite 1 links, Seite 2 rechts, viel Platz für Bemerkungen
# --------------------------------------------------------------------------

def _wrap_lines(text: str, width: float, size: float, bold: bool, max_lines: int,
                shrink: bool = False) -> List[str]:
    lines = []
    for ln in wrap_text(text, width, size, bold):
        lines.append(ln if shrink else fit_text(ln, width, size, bold, min_size=size)[0])
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = fit_text(lines[-1] + " …", width, size, bold, min_size=size)[0]
    return lines


def render_correction_sheet(path: str, ctx: SheetContext, rows: Dict[int, List[KorrRow]], data: SheetData,
                            min_rows: int = 12, landscape: bool = False) -> None:
    """Korrekturblatt: Seite 1 links, Seite 2 rechts; je Werkzeug T, Funktion, Werkzeug, Bemerkung.

    ``landscape``: A4 quer (breitere Spalten) statt hochkant (höhere Zeilen).
    """
    size = (A4[1], A4[0]) if landscape else A4
    doc = PdfDocument(title="Korrekturblatt " + ctx.program, size=size)
    lay = _Layout(doc, ctx, label="KORREKTURBLATT")
    W = doc.width  # noqa: N806
    _draw_header(lay)
    f = data.fields
    info = [(lbl, f.get(key, "")) for lbl, key in (("Teil", "teil"), ("Zeichnungs-Nr.", "zeichnung"),
                                                   ("Material", "material"))]
    info = [(lbl, v) for lbl, v in info if v]
    if info:
        x = M
        for lbl, v in info:
            doc.text(x, lay.y + 8, lbl, 7.5, color=MUTED)
            lw = string_width(lbl, 7.5) + 6
            vv, size_ = fit_text(v, 170 - lw, 9.5, bold=True)
            doc.text(x + lw, lay.y + 8, vv, size_, bold=True, color=TEXT)
            x += 185
        lay.y += 18

    gap = 16.0 if landscape else 12.0
    colw = (W - 2 * M - gap) / 2
    t_w = 40.0 if landscape else 32.0
    func_w = colw * (0.29 if landscape else 0.33)
    tool_w = colw * (0.22 if landscape else 0.21)
    rem_w = colw - t_w - func_w - tool_w
    n = max(min_rows, len(rows.get(1, [])), len(rows.get(2, [])))
    head_h = 24.0 + 16.0
    avail = lay.bottom - lay.y - head_h
    row_h = max(26.0, min(58.0, avail / n))

    def subtitle(side: int) -> str:
        progs: List[str] = []
        for r in rows.get(side, []):
            for p in r.programs.split(", "):
                if p and p not in progs:
                    progs.append(p)
        return ", ".join(progs)

    def heads() -> None:
        for i, side in enumerate((1, 2)):
            x = M + i * (colw + gap)
            doc.rect(x, lay.y, colw, 24, fill=DARK)
            doc.text(x + 8, lay.y + 16.5, "SEITE {}".format(side), 12, bold=True, color=WHITE)
            sub_t, size_ = fit_text(subtitle(side), colw - 90, 7.5)
            doc.text(x + colw - 8, lay.y + 16, sub_t, size_, color=FAINT, align="right")
            yy = lay.y + 24
            lay.cell(x, yy, t_w, 16, "T", 7.5, bold=True, bg=HEAD_BG, color=MUTED, align="center")
            lay.cell(x + t_w, yy, func_w, 16, "Funktion", 7.5, bold=True, bg=HEAD_BG, color=MUTED)
            lay.cell(x + t_w + func_w, yy, tool_w, 16, "Werkzeug", 7.5, bold=True, bg=HEAD_BG, color=MUTED)
            lay.cell(x + t_w + func_w + tool_w, yy, rem_w, 16, "Bemerkung", 7.5, bold=True, bg=HEAD_BG,
                     color=MUTED)
        lay.y += head_h

    heads()
    lay._on_new_page = heads
    line_h = 10.0
    max_lines = max(1, int((row_h - 8) // line_h))
    for i in range(n):
        lay.ensure(row_h)
        for c, side in enumerate((1, 2)):
            x = M + c * (colw + gap)
            xs = (x, x + t_w, x + t_w + func_w, x + t_w + func_w + tool_w)
            ws = (t_w, func_w, tool_w, rem_w)
            for k in range(4):
                doc.rect(xs[k], lay.y, ws[k], row_h, stroke=GRID, width=0.6)
            side_rows = rows.get(side, [])
            if i >= len(side_rows):
                continue
            r = side_rows[i]
            t_text, t_size = fit_text(r.tool, t_w - 6, 13 if landscape else 12, bold=True, min_size=8)
            doc.text(x + t_w / 2, lay.y + 16, t_text, t_size, bold=True, color=TEXT, align="center")
            for col, text, size_, bold, color in ((1, r.function, 9, True, TEXT), (2, r.tooltext, 8.5, False, MUTED),
                                                  (3, r.remark, 9, False, PRIMARY)):
                if not text:
                    continue
                for k, ln in enumerate(_wrap_lines(text, ws[col] - 10, size_, bold, max_lines, shrink=True)):
                    # lange Einzelwörter (FERTIGDREHEN) etwas kleiner statt abgeschnitten
                    t2, s2 = fit_text(ln, ws[col] - 10, size_, bold, min_size=size_ - 2)
                    doc.text(xs[col] + 5, lay.y + 13 + k * line_h, t2, s2, bold=bold, color=color)
        lay.y += row_h
    lay._on_new_page = None
    _draw_footers(doc, ctx, "Korrekturblatt")
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    doc.save(path)
