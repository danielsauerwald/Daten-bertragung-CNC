"""Satznummern (N-Nummern) setzen, neu vergeben oder entfernen.

Schemata:
    "tool"     – pro Werkzeug in Tausendern: N1000, N1010 … / N2000, N2010 …
                 (Zeilen vor dem ersten Werkzeug: N10, N20 …)
    "flat"     – durchgehend: N10, N20, N30 …
    "toolonly" – nur die Werkzeugzeilen: N1, N2, N3 … (Rest ohne Nummer)
    "remove"   – alle Satznummern entfernen

Jedes Programm einer Datei (``%_N_…_MPF``, ``O1234``) fängt wieder vorne an.
Keine Nummer bekommen: Leer- und Kommentarzeilen, Programmköpfe, ``;$PATH=``,
``%`` und reine Sprungmarken (``ENDE:``).

Verweise auf Satznummern werden mitgeführt: Fanuc-Zyklen ``G70``–``G73 P… Q…``,
``GOTO n``, ``M99 Pn`` und Sinumerik ``GOTOF/GOTOB Nn``. Lässt sich ein Verweis
nicht eindeutig zuordnen, wird nichts geändert und eine Meldung zurückgegeben.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from .textutil import _is_comment_only, _is_path_line, _is_program_header, is_tool_call, normalize_text

SCHEMES = {
    "tool": "Pro Werkzeug",
    "flat": "Durchgehend",
    "toolonly": "Nur Werkzeugwechsel",
    "remove": "Nummern entfernen",
}

_EXISTING_N = re.compile(r"^(\s*/?\s*)N(\d+)\s*", re.IGNORECASE)
_LABEL_ONLY = re.compile(r"^[A-Za-z_]\w*:\s*(;.*)?$")
_HEID_NUM = re.compile(r"^(\s*)(\d+)(\s+)")
# Verweise auf Satznummern
_CYCLE = re.compile(r"(?<![A-Za-z])G0*7[0-3](?!\d)", re.IGNORECASE)
_P_ADDR = re.compile(r"(?<![A-Za-z])P(\d+)(?![\d.])", re.IGNORECASE)
_Q_ADDR = re.compile(r"(?<![A-Za-z])Q(\d+)(?![\d.])", re.IGNORECASE)
_GOTO_NUM = re.compile(r"\b(GOTO\s*)(\d+)", re.IGNORECASE)
_GOTO_N = re.compile(r"\b(GOTO[FBC]?\s+N)(\d+)", re.IGNORECASE)
_M99_P = re.compile(r"(?<![A-Za-z])(M0*99\s*P)(\d+)", re.IGNORECASE)


class RenumberResult:
    def __init__(self, text: str, numbered: int = 0, refs: int = 0, error: str = "", note: str = "") -> None:
        self.text = text
        self.numbered = numbered  # Zeilen mit (neuer) Nummer
        self.refs = refs          # angepasste Verweise (G71 P/Q, GOTO …)
        self.error = error        # gesetzt -> Text unverändert
        self.note = note          # Hinweis, z. B. "Zehntausender wegen langer Abschnitte"


def _numberable(s: str) -> bool:
    if not s or s == "%" or _is_comment_only(s) or _is_path_line(s) or _is_program_header(s):
        return False
    return not _LABEL_ONLY.match(s)


def _split_programs(lines: List[str]) -> List[Tuple[int, int]]:
    """Bereiche (Start, Ende) je Programm in der Datei."""
    starts = [i for i, ln in enumerate(lines) if ln.strip() and _is_program_header(ln.strip())]
    if not starts or starts[0] != 0:
        starts = [0] + starts
    bounds = []
    for k, st in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else len(lines)
        bounds.append((st, end))
    return bounds


def _strip_n(line: str) -> Tuple[str, Optional[int], str]:
    """Zeile ohne N-Nummer, alte Nummer, Präfix (z. B. "/" für Satzausblendung)."""
    m = _EXISTING_N.match(line)
    if not m:
        return line.strip(), None, ""
    return line[m.end():].rstrip(), int(m.group(2)), m.group(1).strip()


def _find_refs(line: str, numbers: Dict[int, int]) -> List[Tuple[str, int]]:
    """Alle Satznummer-Verweise einer Zeile: (Art, Nummer)."""
    refs: List[Tuple[str, int]] = []
    code = re.sub(r"\([^)]*\)|;.*$", "", line)
    if _CYCLE.search(code):
        p = _P_ADDR.search(code)
        q = _Q_ADDR.search(code)
        # nur wenn P und Q auf vorhandene Sätze zeigen (G73 beim Fräsen: Q = Zustelltiefe!)
        if p and q and int(p.group(1)) in numbers and int(q.group(1)) in numbers:
            refs += [("P", int(p.group(1))), ("Q", int(q.group(1)))]
    for rx in (_GOTO_N, _GOTO_NUM, _M99_P):
        for m in rx.finditer(code):
            refs.append((rx.pattern, int(m.group(2))))
    return refs


def _apply_refs(line: str, mapping: Dict[int, int]) -> Tuple[str, int]:
    count = 0
    code_end = len(line)
    m = re.search(r";|\(", line)
    if m:
        code_end = m.start()
    code, rest = line[:code_end], line[code_end:]

    def sub_addr(rx, text):
        nonlocal count

        def repl(mm):
            nonlocal count
            old = int(mm.group(1))
            if old in mapping:
                count += 1
                return mm.group(0)[0] + str(mapping[old])
            return mm.group(0)
        return rx.sub(repl, text)

    if _CYCLE.search(code):
        p = _P_ADDR.search(code)
        q = _Q_ADDR.search(code)
        if p and q and int(p.group(1)) in mapping and int(q.group(1)) in mapping:
            code = sub_addr(_P_ADDR, code)
            code = sub_addr(_Q_ADDR, code)
    for rx in (_GOTO_N, _GOTO_NUM, _M99_P):
        def repl2(mm):
            nonlocal count
            old = int(mm.group(2))
            if old in mapping:
                count += 1
                return mm.group(1) + str(mapping[old])
            return mm.group(0)
        code = rx.sub(repl2, code)
    return code + rest, count


def renumber(text: str, scheme: str = "tool", step: int = 10, dialect: Optional[str] = None) -> RenumberResult:
    norm = normalize_text(text, strip_control=False)
    ends_nl = norm.endswith("\n")
    lines = norm.split("\n")
    if ends_nl:
        lines.pop()
    if dialect == "heidenhain":
        return _renumber_heidenhain(lines, ends_nl)
    step = max(1, int(step))

    out: List[str] = list(lines)
    total_numbered = total_refs = 0
    note = ""
    for start, end in _split_programs(lines):
        # 1) alte Nummern und Inhalt erfassen
        entries = []  # (Index, Inhalt ohne N, alte Nummer, Präfix, nummerierbar, Werkzeug?)
        old_numbers: Dict[int, int] = {}
        for i in range(start, end):
            raw = lines[i]
            s = raw.strip()
            content, old, prefix = _strip_n(raw)
            numberable = _numberable(content.lstrip("/").strip() if content else s)
            if old is not None:
                old_numbers[old] = old_numbers.get(old, 0) + 1
            entries.append((i, content, old, prefix, numberable, numberable and is_tool_call(content)))
        refs = [r for _, c, _, _, nb, _ in entries if nb for r in _find_refs(c, old_numbers)]
        if refs:
            if scheme in ("toolonly", "remove"):
                return RenumberResult(text, error=(
                    "Das Programm springt auf Satznummern (z. B. G71 P… Q… oder GOTO). "
                    "Diese Nummern müssen bleiben – bitte „Pro Werkzeug“ oder „Durchgehend“ wählen."))
            for _kind, n in refs:
                if old_numbers.get(n, 0) > 1:
                    return RenumberResult(text, error=(
                        "Die Satznummer N{} kommt mehrfach vor und wird angesprungen. "
                        "So lässt sich nicht sicher umnummerieren.".format(n)))

        # 2) neue Nummern vergeben
        new_numbers: List[Optional[int]] = []
        if scheme == "flat":
            k = 0
            for e in entries:
                if e[4]:
                    k += 1
                    new_numbers.append(k * step)
                else:
                    new_numbers.append(None)
        elif scheme == "toolonly":
            k = 0
            for e in entries:
                if e[5]:
                    k += 1
                    new_numbers.append(k)
                else:
                    new_numbers.append(None)
        elif scheme == "remove":
            new_numbers = [None] * len(entries)
        else:  # "tool"
            # Gruppe 0 = Sätze vor dem ersten Werkzeug, danach eine Gruppe je Werkzeugaufruf
            groups: List[List[int]] = [[]]
            for idx, e in enumerate(entries):
                if e[5]:
                    groups.append([])
                if e[4]:
                    groups[-1].append(idx)
            longest = max(len(g) for g in groups)
            block = 1000
            if longest * step >= block:
                block = 10000
                note = "Abschnitte mit vielen Sätzen: Nummern in Zehntausendern (N10000, N20000 …)."
            if longest * step >= block:
                return RenumberResult(text, error=(
                    "Ein Werkzeugabschnitt hat zu viele Sätze für die Nummerierung pro Werkzeug. "
                    "Bitte „Durchgehend“ wählen oder eine kleinere Schrittweite."))
            if (len(groups) - 1) * block + longest * step > 99999 and dialect != "sinumerik":
                return RenumberResult(text, error=(
                    "Zu viele Sätze für die Nummerierung pro Werkzeug (Fanuc erlaubt höchstens N99999). "
                    "Bitte „Durchgehend“ wählen oder eine kleinere Schrittweite."))
            new_numbers = [None] * len(entries)
            for g_no, g in enumerate(groups):
                for k, idx in enumerate(g):
                    new_numbers[idx] = g_no * block + (k * step if g_no else (k + 1) * step)

        mapping = {}
        for idx, e in enumerate(entries):
            nn = new_numbers[idx]
            if e[2] is not None and nn is not None:
                mapping[e[2]] = nn
        if refs and any(n not in mapping for _k, n in refs):
            missing = [n for _k, n in refs if n not in mapping][0]
            return RenumberResult(text, error="Sprungziel N{} wurde nicht gefunden.".format(missing))

        # 3) Zeilen zusammensetzen
        for idx, e in enumerate(entries):
            nn = new_numbers[idx]
            i, content, old, prefix, numberable, _tool = e
            if not numberable:
                if old is not None and scheme == "remove":
                    out[i] = (prefix + content).rstrip()
                continue
            body, n_refs = _apply_refs(content, mapping) if refs else (content, 0)
            total_refs += n_refs
            body = body.strip()
            slash = ""
            if body.startswith("/"):
                slash, body = "/", body[1:].lstrip()
            slash = slash or prefix
            if nn is None:
                out[i] = (slash + body).rstrip()
            else:
                out[i] = "{}N{} {}".format(slash, nn, body).rstrip()
                total_numbered += 1
    result = "\n".join(out) + ("\n" if ends_nl else "")
    return RenumberResult(result, total_numbered, total_refs, note=note)


def _renumber_heidenhain(lines: List[str], ends_nl: bool) -> RenumberResult:
    """Heidenhain-Klartext: Sätze lückenlos 0, 1, 2 … durchzählen."""
    out = []
    n = 0
    for ln in lines:
        if not ln.strip():
            out.append(ln)
            continue
        m = _HEID_NUM.match(ln)
        body = ln[m.end():] if m else ln.strip()
        out.append("{} {}".format(n, body))
        n += 1
    return RenumberResult("\n".join(out) + ("\n" if ends_nl else ""), n, 0,
                          note="Heidenhain: Sätze von 0 an lückenlos durchgezählt.")
