"""Programmordner durchsuchen: Kundenordner, Suche, passender Ordner zum Speichern."""

from __future__ import annotations

import os
import re
from typing import List, NamedTuple, Optional, Tuple


class Entry(NamedTuple):
    name: str
    path: str
    mtime: float


class Hit(NamedTuple):
    name: str
    path: str
    mtime: float
    folder: str  # relativ zum Programmordner, "" = Hauptordner


def _visible(name: str) -> bool:
    low = name.lower()
    return (not name.startswith((".", "~$")) and low not in ("thumbs.db", "desktop.ini")
            and not low.endswith((".einrichteblatt.json", ".tmp")))


def list_folder(path: str) -> Tuple[List[Entry], List[Entry]]:
    """Unterordner und Dateien eines Ordners (nicht rekursiv)."""
    dirs: List[Entry] = []
    files: List[Entry] = []
    try:
        names = os.listdir(path)
    except OSError:
        return dirs, files
    for name in names:
        if not _visible(name):
            continue
        full = os.path.join(path, name)
        try:
            st = os.stat(full)
        except OSError:
            continue
        if os.path.isdir(full):
            dirs.append(Entry(name, full, st.st_mtime))
        else:
            files.append(Entry(name, full, st.st_mtime))
    dirs.sort(key=lambda e: e.name.lower())
    return dirs, files


def count_files(path: str) -> int:
    try:
        return sum(1 for n in os.listdir(path) if _visible(n) and os.path.isfile(os.path.join(path, n)))
    except OSError:
        return 0


def rel_label(root: str, path: str) -> str:
    """'Metlog › Archiv' für einen Ordner unterhalb des Programmordners."""
    rel = os.path.relpath(path, root)
    if rel in (".", ""):
        return ""
    return " › ".join(rel.split(os.sep))


def is_inside(root: str, path: str) -> bool:
    try:
        root_n = os.path.normcase(os.path.abspath(root))
        path_n = os.path.normcase(os.path.abspath(path))
        return os.path.commonpath([root_n, path_n]) == root_n
    except ValueError:  # anderes Laufwerk
        return False


def walk_files(root: str, max_depth: int = 6, limit: int = 20000) -> List[Hit]:
    """Alle Dateien unterhalb des Programmordners (für die Suche)."""
    hits: List[Hit] = []
    root = os.path.abspath(root)
    base_depth = root.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if _visible(d))
        if dirpath.count(os.sep) - base_depth >= max_depth:
            dirnames[:] = []
        folder = rel_label(root, dirpath)
        for name in filenames:
            if not _visible(name):
                continue
            full = os.path.join(dirpath, name)
            try:
                mtime = os.path.getmtime(full)
            except OSError:
                continue
            hits.append(Hit(name, full, mtime, folder))
            if len(hits) >= limit:
                return hits
    return hits


def subfolders(root: str, max_depth: int = 3, limit: int = 400) -> List[Tuple[str, str]]:
    """Alle Unterordner als (Anzeigename, Pfad), z. B. ('Metlog › Archiv', ...)."""
    result: List[Tuple[str, str]] = []

    def visit(path: str, depth: int) -> None:
        if depth > max_depth or len(result) >= limit:
            return
        dirs, _ = list_folder(path)
        for d in dirs:
            result.append((rel_label(root, d.path), d.path))
            visit(d.path, depth + 1)

    visit(root, 1)
    return result


def search(hits: List[Hit], term: str) -> List[Hit]:
    """Dateien, deren Name oder Ordner den Suchbegriff enthält (Groß/klein egal)."""
    words = [w for w in term.lower().split() if w]
    if not words:
        return []
    out = []
    for h in hits:
        hay = (h.folder + " " + h.name).lower()
        if all(w in hay for w in words):
            out.append(h)
    return out


# --------------------------------------------------------------------------
# Passenden Kundenordner zum Speichern finden
# --------------------------------------------------------------------------

def _norm(s: str) -> str:
    return re.sub(r"[\s\-.]+", "_", s.strip().upper())


def match_customer_folder(program_name: str, folder_names: List[str]) -> Optional[str]:
    """Kundenordner, mit dessen Namen der Programmname beginnt.

    ``METLOG_300069`` passt zu ``Metlog``, ``FB_BOESE_12`` zu ``FB_Boese``.
    ``BERGMANN_1`` passt *nicht* zu ``berg`` (der Name muss danach enden oder
    mit Ziffer/Trennzeichen weitergehen). Bei mehreren Treffern gewinnt der längste.
    """
    prog = _norm(program_name)
    best: Optional[str] = None
    for folder in folder_names:
        f = _norm(folder)
        if len(f) < 2 or not prog.startswith(f):
            continue
        rest = prog[len(f):]
        if rest and rest[0].isalpha():
            continue
        if best is None or len(f) > len(_norm(best)):
            best = folder
    return best


def find_existing(root: str, base_name: str, max_depth: int = 6) -> Optional[str]:
    """Ordner, in dem schon eine Datei mit diesem Namen (beliebige Endung) liegt."""
    want = base_name.lower()
    best: Optional[Hit] = None
    for h in walk_files(root, max_depth=max_depth):
        if os.path.splitext(h.name)[0].lower() == want:
            if best is None or h.mtime > best.mtime:
                best = h
    return os.path.dirname(best.path) if best else None


def suggest_folder(root: str, program_name: Optional[str], browsed: Optional[str] = None) -> Tuple[str, str]:
    """Ordner zum Speichern vorschlagen. Liefert (Pfad, Begründung).

    Reihenfolge: Datei gibt es schon → Kundenordner passt zum Namen →
    gerade in der Liste geöffneter Ordner → Programmordner.
    """
    if program_name and os.path.isdir(root):
        existing = find_existing(root, program_name)
        if existing:
            where = rel_label(root, existing) or "im Hauptordner"
            return existing, "„{}“ liegt schon in {}".format(program_name, where)
        dirs, _ = list_folder(root)
        match = match_customer_folder(program_name, [d.name for d in dirs])
        if match:
            return os.path.join(root, match), "Kundenordner passend zum Namen gewählt"
    if browsed and is_inside(root, browsed) and os.path.isdir(browsed) and \
            os.path.normcase(os.path.abspath(browsed)) != os.path.normcase(os.path.abspath(root)):
        return browsed, "gerade geöffneter Ordner"
    return root, ""
