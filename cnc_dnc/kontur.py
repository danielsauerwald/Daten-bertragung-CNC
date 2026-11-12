"""Kontur-Assistent: Drehkontur und Fräsungen auf Winkellagen  ->  Sinumerik-Programm.

Ohne Oberfläche, damit alles mit Tests abgesichert werden kann. Das Fenster dazu steht in
``kontur_dialog.py``.

Koordinaten
-----------
* Die Kontur wird wie in der Zeichnung eingegeben: X als **Durchmesser**, Z mit Nullpunkt in
  der **Mitte der Stirnseite**, das Teil liegt bei Z negativ. Erster Punkt ist immer X0 Z0.
* Intern wird mit dem **Radius** ``r = X/2`` gerechnet; Punkte sind Paare ``(z, r)``.
* Die Kontur muss von rechts nach links „steigen“ (X wird nicht kleiner, Z nicht größer).
  Einstiche/Hinterschnitte können mit einem normalen Längsdrehmeißel nicht geschruppt werden
  und werden deshalb (noch) abgelehnt.

Erzeugter Code (Stil wie in den vorhandenen KANAL1/KANAL2-Programmen)
--------------------------------------------------------------------
* Je Arbeitsgang ein Kommentar über dem Werkzeug (``;VORDREHEN VNMG08``) – den nutzen auch
  Einrichte- und Korrekturblatt.
* Drehen: ``T01D1 G95 G18 S2300 M3 M8``, Schruppen achsparallel mit Nachziehen entlang der
  Kontur, Schlichten mit Schneidenradiuskorrektur G42.
* Fräsen/Bohren mit angetriebenem Werkzeug: Hauptspindel mit ``SPOS=DC(Winkel)`` positionieren,
  Werkzeugspindel ``S3=… M3=3``, Vorschub G94, Ebene G19 (radial, Y-Achse) bzw. G17 (axial).
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .postpro import Postprozessor, fmt, nc_name, nc_text, program_number  # noqa: F401 (auch für Aufrufer)

EPS = 1e-6
TOL = 1e-3  # Eingabetoleranz in mm

SEG_OPS = {"drehen": "Drehen", "frei": "Nicht drehen"}
ECKEN = {"": "scharf", "entgraten": "Entgraten", "fase": "Fase", "radius": "Radius"}
FEATURES = {
    "flaeche": "Fläche / Mehrkant",
    "nut": "Längsnut",
    "bohrung_radial": "Bohrung radial",
    "bohrung_axial": "Bohrung Stirnseite",
    "mantelnut": "Mantelnut (C-Achse)",
}
TOOL_ARTEN = {
    "schruppen": "Drehen schruppen",
    "schlichten": "Drehen schlichten",
    "innen": "Bohrstange (innen)",
    "fraeser": "Fräser",
    "bohrer": "Bohrer",
    "fase": "Fasenfräser 90°",
}
LAGEN = {"": "–", "radial": "radial", "axial": "axial", "mitte": "stehend (Drehmitte)"}
# Welche Werkzeuglage eine Fräsung braucht
FEATURE_LAGE = {"flaeche": "radial", "nut": "radial", "bohrung_radial": "radial", "bohrung_axial": "axial",
                "mantelnut": "radial"}


# ==========================================================================
# Datenmodell
# ==========================================================================


@dataclass
class KPunkt:
    """Konturpunkt. ``op`` gilt für das Element, das **in** diesem Punkt endet."""

    x: float = 0.0  # Durchmesser
    z: float = 0.0
    ecke: str = ""  # "" | "entgraten" | "fase" | "radius"
    wert: float = 0.0  # Fasenlänge bzw. Radius
    op: str = "drehen"  # "drehen" | "frei"


@dataclass
class Werkzeug:
    t: int = 1
    d: int = 1
    name: str = ""
    art: str = "schruppen"
    lage: str = ""  # angetrieben "radial"/"axial"; stehender Bohrer in Drehmitte "mitte"
    dm: float = 0.0  # Durchmesser (Fräser, Bohrer, Fasenfräser)
    s: float = 2000.0  # Drehzahl
    f: float = 0.2  # Drehen mm/U, Fräsen/Bohren mm/min
    ap: float = 1.0  # Zustellung (Drehen je Seite, Fräsen Tiefe je Schnitt, Bohren Spanbruch-Tiefe)
    halter: str = ""  # Katalog: Halter/Bohrstange (Drehen) bzw. Bohrer/Fräser (werkzeugkatalog.py)
    platte: str = ""  # Drehen: Wendeschneidplatte nach ISO, z. B. "VNMG 160408-M"

    @property
    def label(self) -> str:
        return "T{} {}".format(self.t, self.name).strip()


@dataclass
class Fraesung:
    art: str = "flaeche"
    winkel: List[float] = field(default_factory=lambda: [0.0])
    z1: float = 0.0  # Fläche/Nut: Z rechts · Bohrung radial: Z der Bohrung · Stirn: Z der Oberfläche
    z2: float = -10.0  # Fläche/Nut: Z links
    mass: float = 10.0  # Fläche: Schlüsselweite (Maß über Fläche) · Nut: Breite · Bohrung: Ø
    tiefe: float = 2.0  # Nut/Bohrung
    teilkreis: float = 0.0  # Bohrung Stirnseite: Lochkreis-Ø
    wz: int = 0
    entgraten: bool = False
    wz_entgraten: int = 0
    fase: float = 0.3
    strategie: str = ""  # leer = Vorgabe der Art, siehe STRATEGIEN
    # Nut auf dem Mantel: Mittellinie als Punkte [Z, C in Grad] auf der Abwicklung (C darf über 360 gehen)
    bahn: List[List[float]] = field(default_factory=list)


# Frässtrategien je Art (erster Eintrag = Vorgabe)
STRATEGIEN: Dict[str, Dict[str, str]] = {
    "flaeche": {"zickzack": "Zickzack (hin und her)", "gleichlauf": "nur Gleichlauf", "gegenlauf": "nur Gegenlauf"},
    "nut": {"bahnen": "Bahnen, senkrecht eintauchen", "rampe": "Bahnen, schräg eintauchen (Rampe)",
            "trochoidal": "trochoidal (Wirbelfräsen)"},
    "bohrung_radial": {"entspanen": "Entspanen (ganz raus)", "spanbruch": "Spanbruch (kurz zurück)",
                       "einzug": "in einem Zug"},
    "bohrung_axial": {"entspanen": "Entspanen (ganz raus)", "spanbruch": "Spanbruch (kurz zurück)",
                      "einzug": "in einem Zug"},
    "mantelnut": {"pendeln": "schräg eintauchen (Wendel / Pendeln)", "senkrecht": "senkrecht eintauchen"},
}
RAMPENWINKEL = 3.0  # Grad
TROCHOID_MIN = 1.25  # Nutbreite mindestens 1,25 × Fräser-Ø


def strategie(fr: "Fraesung") -> str:
    opts = STRATEGIEN.get(fr.art, {})
    return fr.strategie if fr.strategie in opts else (next(iter(opts)) if opts else "")


@dataclass
class Teil:
    name: str = "TEIL"
    rohteil_d: float = 32.0
    rohteil_l: float = 0.0  # Länge des Rohteils inkl. Planaufmaß; 0 = Stange (Länge offen)
    planaufmass: float = 0.5
    punkte: List[KPunkt] = field(default_factory=lambda: [KPunkt(0.0, 0.0)])
    fraesungen: List[Fraesung] = field(default_factory=list)
    werkzeuge: List[Werkzeug] = field(default_factory=list)
    wz_schruppen: int = 1
    wz_schlichten: int = 2
    # Innenbearbeitung: Vorbohren in der Mitte (stehender Bohrer), dann Bohrstange
    innen: List[KPunkt] = field(default_factory=list)  # Innenkontur ab Stirnseite nach innen, leer = keine
    bohr_d: float = 0.0  # Vorbohr-Ø
    bohr_tiefe: float = 0.0  # Tiefe mit vollem Ø (ohne Spitze)
    wz_bohren: int = 10
    wz_innen_schruppen: int = 3
    wz_innen_schlichten: int = 4
    aufmass_x: float = 0.4  # Schlichtaufmaß im Durchmesser
    aufmass_z: float = 0.1
    kante: float = 0.2  # „Entgraten“ an Drehkanten = Fase dieser Größe
    sicherheit: float = 2.0
    # Nullpunkt: so weit liegt Z0 des Programms hinter der Stirnseite (z. B. an einem Absatz, aus dem 3D-Modell
    # gewählt). Gerechnet und gezeichnet wird ab der Stirnseite; im Code steht Z + z_null.
    z_null: float = 0.0
    # Werkzeugwechselpunkt, M-/G-Befehle usw. stehen im Postprozessor der Maschine (postpro.py)

    def tool(self, t: int) -> Optional[Werkzeug]:
        for w in self.werkzeuge:
            if w.t == t:
                return w
        return None

    # -- Speichern / Laden ----------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Teil":
        base = cls()
        if not isinstance(data, dict):
            return base
        for f in fields(cls):
            if f.name in ("punkte", "fraesungen", "werkzeuge", "innen") or f.name not in data:
                continue
            default = getattr(base, f.name)
            try:
                value = type(default)(data[f.name])
            except (TypeError, ValueError):
                continue
            setattr(base, f.name, value)
        base.punkte = [_load(KPunkt, p) for p in data.get("punkte", []) if isinstance(p, dict)] or base.punkte
        base.innen = [_load(KPunkt, p) for p in data.get("innen", []) if isinstance(p, dict)]
        base.fraesungen = [_load(Fraesung, p) for p in data.get("fraesungen", []) if isinstance(p, dict)]
        base.werkzeuge = [_load(Werkzeug, p) for p in data.get("werkzeuge", []) if isinstance(p, dict)]
        for fr in base.fraesungen:
            fr.winkel = [float(w) for w in fr.winkel if isinstance(w, (int, float))] or [0.0]
            fr.bahn = [[float(p[0]), float(p[1])] for p in fr.bahn
                       if isinstance(p, (list, tuple)) and len(p) >= 2
                       and all(isinstance(v, (int, float)) for v in p[:2])]
        base.punkte[0].x, base.punkte[0].z = 0.0, 0.0
        if "innen" not in data and base.werkzeuge:  # Datei von vor der Innenbearbeitung
            used = {w.t for w in base.werkzeuge}
            for w in standard_werkzeuge():  # Bohrer (Drehmitte) und Bohrstangen ergänzen, wo frei
                if (w.art == "innen" or w.lage == "mitte") and w.t not in used:
                    base.werkzeuge.append(w)
                    used.add(w.t)
        return base


def load_teil(path: str) -> Teil:
    """Zuletzt bearbeitetes Teil laden; beim ersten Mal (oder kaputter Datei) das Beispiel."""
    import json

    try:
        with open(path, "r", encoding="utf-8") as fh:
            return Teil.from_dict(json.load(fh))
    except (OSError, ValueError):
        return beispiel_teil()


def save_teil(teil: Teil, path: str) -> None:
    import json
    import os

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(teil.to_dict(), fh, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def _load(cls, data: Dict[str, Any]):
    obj = cls()
    for f in fields(cls):
        if f.name not in data:
            continue
        default = getattr(obj, f.name)
        value = data[f.name]
        try:
            if isinstance(default, list):
                value = list(value)
            elif isinstance(default, bool):
                value = bool(value)
            else:
                value = type(default)(value)
        except (TypeError, ValueError):
            continue
        setattr(obj, f.name, value)
    return obj


def standard_werkzeuge() -> List[Werkzeug]:
    return [
        Werkzeug(1, 1, "VNMG08", "schruppen", "", 0, 2300, 0.25, 1.5, "PVJNR 2525M16", "VNMG 160408-M"),
        Werkzeug(2, 1, "VCMT04", "schlichten", "", 0, 2500, 0.1, 0, "SVJCR 2020K16", "VCMT 160404-F"),
        Werkzeug(5, 1, "FRAESER D8", "fraeser", "radial", 8, 3000, 300, 1.0, "VHM-FRAESER D8 Z3"),
        Werkzeug(6, 1, "BOHRER D5", "bohrer", "radial", 5, 2500, 150, 5, "VHM-BOHRER D5"),
        Werkzeug(7, 1, "FASENFRAESER 90", "fase", "radial", 10, 4000, 400, 0, "FASENFRAESER D10 90"),
        Werkzeug(8, 1, "BOHRER D5", "bohrer", "axial", 5, 2500, 150, 5, "VHM-BOHRER D5"),
        Werkzeug(3, 1, "DCMT04", "innen", "", 0, 1800, 0.15, 1.0, "A12M-SDUCR07", "DCMT 070204-M"),
        Werkzeug(4, 1, "DCMT02", "innen", "", 0, 2200, 0.08, 0, "A12M-SDUCR07", "DCMT 070202-F"),
        Werkzeug(10, 1, "BOHRER D16", "bohrer", "mitte", 16, 900, 0.12, 12, "HSS-BOHRER D16"),
    ]


def kurzname(platte_code: str) -> str:
    """Name wie in den Programmen üblich: „VNMG 160408-M“ -> „VNMG08“."""
    from .werkzeugkatalog import parse_platte

    p = parse_platte(platte_code)
    if p is None:
        return ""
    if p.form == "R":
        return "{} D{:g}".format(p.code[:4], p.ic)
    return "{}{:02d}".format(p.code[:4], int(round(p.radius * 10)))


def dreh_geometrie(w: Werkzeug):
    """(Halter, Platte, Hinweise) eines Drehwerkzeugs für die Simulation.

    Fehlt etwas (z. B. alte Werkzeugliste), wird es aus der Bezeichnung erraten bzw. eine übliche
    Vorgabe genommen – das steht dann in den Hinweisen.
    """
    from . import werkzeugkatalog as wk

    notes: List[str] = []
    p = wk.parse_platte(w.platte) or wk.parse_platte(w.name)
    if p is None:
        guess = re.match(r"^\s*([CDVWTS])([A-Z])([A-Z])([A-Z])\s*0?(\d)", w.name.upper())
        default = {"schlichten": "VCMT 160404-F", "innen": "DCMT 070204-M"}.get(w.art, "CNMG 120408-M")
        if guess:
            form, frei = guess.group(1), guess.group(2)
            rad = guess.group(5)
            for code in wk.PLATTEN:
                q = wk.parse_platte(code)
                if q and q.form == form and q.freiwinkel == frei and abs(q.radius * 10 - int(rad)) < 1e-6:
                    default = code
                    break
        p = wk.parse_platte(default)
        notes.append("T{}: keine Platte angegeben – Simulation nimmt {}.".format(w.t, p.code))
    h = wk.halter(w.halter)
    art = "innen" if w.art == "innen" else "aussen"
    if h is not None and h.art != art:
        notes.append("T{}: {} ist {} – hier wird {} gebraucht.".format(
            w.t, h.code, "eine Bohrstange" if h.art == "innen" else "ein Außenhalter",
            "eine Bohrstange" if art == "innen" else "ein Außenhalter"))
        h = None
    if h is None or not wk.platte_passt(h, p):
        if h is not None:
            notes.append("T{}: Platte {} passt nicht in {}.".format(w.t, p.code, h.code))
        cand = [x for x in wk.HALTER if x.art == art and x.hand == "R" and wk.platte_passt(x, p)]
        if not cand:
            cand = [x for x in wk.HALTER if x.art == art and x.hand == "R" and x.form == p.form]
        if not cand:
            return None
        h = cand[0]
        if not w.halter:
            notes.append("T{}: kein Halter angegeben – Simulation nimmt {}.".format(w.t, h.code))
    return h, p, notes


def masse(teil: Teil) -> Dict[str, Any]:
    """Längenmaße für die Zeichnung (Z-Werte, Länge).

    ``kette``: Abschnitte zwischen den Z-Werten der Konturpunkte (Absätze haben keine Länge),
    ``gesamt``: Konturlänge ab Stirnseite (nur bei mehr als einem Abschnitt),
    ``rohteil``: Rohteillänge ab Rohteil-Stirnseite (Planaufmaß) – None bei Stange,
    ``innen``: Abschnitte der Innenkontur und Bohrtiefe (ab Stirnseite).
    """
    zs: List[float] = []
    for p in teil.punkte:
        if not any(abs(p.z - z) < TOL for z in zs):
            zs.append(p.z)
    zs.sort(reverse=True)
    kette = [(a, b, a - b) for a, b in zip(zs, zs[1:])]
    gesamt = (zs[0], zs[-1], zs[0] - zs[-1]) if len(kette) > 1 else None
    rohteil = None
    if teil.rohteil_l > TOL:
        front = max(0.0, teil.planaufmass)
        rohteil = (front, front - teil.rohteil_l, teil.rohteil_l)
    zi: List[float] = []
    for z in [p.z for p in teil.innen] + ([-teil.bohr_tiefe] if teil.bohr_d > TOL and teil.bohr_tiefe > TOL else []):
        if not any(abs(z - v) < TOL for v in zi):
            zi.append(z)
    if teil.bohr_d > TOL and teil.bohr_tiefe > TOL and not any(abs(v) < TOL for v in zi):
        zi.append(0.0)
    zi.sort(reverse=True)
    innen = [(a, b, a - b) for a, b in zip(zi, zi[1:])]
    return {"kette": kette, "gesamt": gesamt, "rohteil": rohteil, "innen": innen}


def neues_teil() -> Teil:
    return Teil(werkzeuge=standard_werkzeuge())


def beispiel_teil() -> Teil:
    """Kleine Welle mit zwei Schlüsselflächen und einer Querbohrung (zum Ausprobieren)."""
    t = neues_teil()
    t.name = "BEISPIEL"
    t.rohteil_l = 45.0
    t.punkte = [
        KPunkt(0, 0),
        KPunkt(20, 0, "fase", 1.0),
        KPunkt(20, -15, "radius", 1.0),
        KPunkt(26, -15, "entgraten"),
        KPunkt(26, -30),
        KPunkt(32, -30),
        KPunkt(32, -40, op="frei"),
    ]
    t.fraesungen = [
        Fraesung("flaeche", [0.0, 180.0], 0.0, -10.0, 17.0, 0, 0, 5, True, 7, 0.3),
        Fraesung("bohrung_radial", [90.0], -22.5, 0, 5.0, 6.0, 0, 6, True, 7, 0.3),
    ]
    return t


# ==========================================================================
# Hilfsfunktionen
# ==========================================================================


def parse_num(text: str) -> float:
    """Zahl aus einer Eingabe – Komma oder Punkt als Dezimaltrenner."""
    s = str(text).strip().replace(",", ".").replace(" ", "")
    if not s:
        raise ValueError("leer")
    return float(s)


def parse_winkel(text: str) -> List[float]:
    """„0 180“, „0;90;180“, „0, 120, 240“ -> Liste in Grad (0 … <360, ohne Doppelte).

    Komma trennt hier Winkel; Kommazahlen bitte mit Punkt schreiben (22.5).
    """
    parts = [p for p in re.split(r"[\s,;/]+", str(text).strip()) if p]
    result: List[float] = []
    for p in parts:
        w = parse_num(p) % 360.0
        if abs(w - 360.0) < TOL:
            w = 0.0
        if not any(abs(w - x) < TOL for x in result):
            result.append(w)
    if not result:
        raise ValueError("kein Winkel")
    return result


def gleich_verteilt(anzahl: int, start: float = 0.0) -> List[float]:
    return [round((start + i * 360.0 / anzahl) % 360.0, 3) for i in range(max(1, anzahl))]


def winkel_text(winkel: Sequence[float]) -> str:
    return " ".join(fmt(w) for w in winkel)


# ==========================================================================
# Geometrie der Drehkontur
# ==========================================================================


@dataclass
class Elem:
    """Konturelement in (z, r). Linie oder Kreisbogen (Fase/Radius an einer Ecke)."""

    z0: float
    r0: float
    z1: float
    r1: float
    arc: bool = False
    ccw: bool = False
    radius: float = 0.0
    cz: float = 0.0
    cr: float = 0.0
    seg: int = -1  # Nummer des eingegebenen Elements (endet in Punkt seg)
    corner: int = -1  # Nummer des Punkts, an dem die Fase/der Radius sitzt

    def points(self, max_deg: float = 5.0) -> List[Tuple[float, float]]:
        """Stützpunkte (ohne Startpunkt); Bögen werden fein in Geraden zerlegt."""
        if not self.arc:
            return [(self.z1, self.r1)]
        a0 = math.atan2(self.r0 - self.cr, self.z0 - self.cz)
        a1 = math.atan2(self.r1 - self.cr, self.z1 - self.cz)
        da = a1 - a0
        if self.ccw and da < 0:
            da += 2 * math.pi
        if not self.ccw and da > 0:
            da -= 2 * math.pi
        n = max(2, int(math.ceil(abs(math.degrees(da)) / max_deg)))
        pts = []
        for i in range(1, n + 1):
            a = a0 + da * i / n
            pts.append((self.cz + self.radius * math.cos(a), self.cr + self.radius * math.sin(a)))
        pts[-1] = (self.z1, self.r1)
        return pts


@dataclass
class Geometrie:
    elems: List[Elem] = field(default_factory=list)  # komplette Kontur
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    turned: List[bool] = field(default_factory=list)  # je Element 1..n (Index 0 unbenutzt)
    first: int = 0  # erstes/letztes gedrehtes Element (0 = keines)
    last: int = 0

    def turn_elems(self) -> List[Elem]:
        if not self.first:
            return []
        out = []
        for e in self.elems:
            if e.seg >= 0 and self.first <= e.seg <= self.last:
                out.append(e)
            elif e.corner >= 0 and self.first <= e.corner < self.last:
                out.append(e)
        return out


def _unit(dz: float, dr: float) -> Tuple[float, float, float]:
    ln = math.hypot(dz, dr)
    return (dz / ln, dr / ln, ln) if ln > EPS else (0.0, 0.0, 0.0)


def build_geometry(teil: Teil) -> Geometrie:
    g = Geometrie()
    pts = teil.punkte
    n = len(pts) - 1
    if n < 1:
        g.errors.append("Die Kontur braucht mindestens einen zweiten Punkt.")
        return g
    if abs(pts[0].x) > TOL or abs(pts[0].z) > TOL:
        g.errors.append("Der erste Punkt muss X0 Z0 sein (Mitte der Stirnseite).")
        return g
    if teil.rohteil_d <= 0:
        g.errors.append("Rohteil-Durchmesser fehlt.")
        return g
    R_s = teil.rohteil_d / 2.0
    zr = [(p.z, p.x / 2.0) for p in pts]
    dirs: List[Tuple[float, float, float]] = [(0.0, 0.0, 0.0)]
    for i in range(1, n + 1):
        dz, dr = zr[i][0] - zr[i - 1][0], zr[i][1] - zr[i - 1][1]
        u = _unit(dz, dr)
        dirs.append(u)
        if u[2] < TOL:
            g.errors.append("Punkt {} liegt auf Punkt {} – bitte löschen oder ändern.".format(i, i - 1))
        elif zr[i][1] < TOL and zr[i - 1][1] < TOL:
            g.errors.append("Element {} liegt auf der Mittelachse (X0).".format(i))
        elif dz > TOL or dr < -TOL:
            g.errors.append(
                "Element {} (Punkt {} → {}): Die Kontur muss von der Stirnseite nach links verlaufen und "
                "darf im Durchmesser nicht kleiner werden. Einstiche und Hinterschnitte gehen noch nicht."
                .format(i, i - 1, i))
        if pts[i].x < -TOL:
            g.errors.append("Punkt {}: X darf nicht negativ sein.".format(i))
        if pts[i].x / 2.0 > R_s + TOL:
            g.errors.append("Punkt {}: Ø{} ist größer als das Rohteil (Ø{}).".format(i, fmt(pts[i].x),
                                                                                    fmt(teil.rohteil_d)))
    if g.errors:
        return g

    # Welche Elemente werden gedreht?
    g.turned = [False] + [pts[i].op != "frei" for i in range(1, n + 1)]
    turned_idx = [i for i in range(1, n + 1) if g.turned[i]]
    if turned_idx:
        g.first, g.last = turned_idx[0], turned_idx[-1]
        for i in range(g.first, g.last + 1):
            if not g.turned[i]:
                g.errors.append("Element {}: „Nicht drehen“ geht nur für die Stirnfläche am Anfang oder für die "
                                "letzten Elemente der Kontur.".format(i))
        if g.first > 1:
            # vorne darf nur die Stirnfläche (senkrecht ab X0) ausgelassen werden
            if not (g.first == 2 and abs(dirs[1][0]) < EPS):
                g.errors.append("„Nicht drehen“ am Anfang geht nur für die Stirnfläche (senkrecht ab X0).")
    if g.errors:
        return g

    # Ecken: Fase / Radius / Entgraten -> Kürzung der angrenzenden Elemente
    trims = [0.0] * (n + 1)
    kinds: Dict[int, Tuple[str, float]] = {}
    for i in range(1, n):
        p = pts[i]
        if not p.ecke:
            continue
        value = teil.kante if p.ecke == "entgraten" else p.wert
        if value <= TOL:
            g.errors.append("Punkt {}: Größe für {} fehlt.".format(i, ECKEN.get(p.ecke, p.ecke)))
            continue
        if g.first and not (g.turned[i] and g.turned[i + 1]):
            g.warnings.append("Punkt {}: {} liegt am Rand der Drehbearbeitung und wird nicht gefertigt."
                              .format(i, ECKEN.get(p.ecke, p.ecke)))
            continue
        d1, d2 = dirs[i], dirs[i + 1]
        cross = d1[0] * d2[1] - d1[1] * d2[0]
        dot = d1[0] * d2[0] + d1[1] * d2[1]
        theta = math.atan2(abs(cross), dot)
        if theta < math.radians(0.5):
            g.warnings.append("Punkt {}: keine Ecke (gerade weiter) – {} entfällt.".format(i, ECKEN[p.ecke]))
            continue
        if p.ecke == "radius":
            trims[i] = value * math.tan(theta / 2.0)
        else:
            trims[i] = value
        kinds[i] = (p.ecke, value)
    for i in range(1, n + 1):
        if trims[i - 1] + trims[i] > dirs[i][2] + TOL:
            g.errors.append("Element {}: Fase/Radius an den Enden passt nicht auf die Länge {}."
                            .format(i, fmt(dirs[i][2])))
    if g.errors:
        return g

    for i in range(1, n + 1):
        dz, dr, ln = dirs[i]
        z0, r0 = zr[i - 1]
        z1, r1 = zr[i]
        a = (z0 + dz * trims[i - 1], r0 + dr * trims[i - 1])
        b = (z1 - dz * trims[i], r1 - dr * trims[i])
        if math.hypot(b[0] - a[0], b[1] - a[1]) > EPS:
            g.elems.append(Elem(a[0], a[1], b[0], b[1], seg=i))
        if i in kinds:
            kind, value = kinds[i]
            d2 = dirs[i + 1]
            c = (z1 + d2[0] * trims[i], r1 + d2[1] * trims[i])
            if kind == "radius":
                cross = dz * d2[1] - dr * d2[0]
                ccw = cross > 0
                nz, nr = (-dr, dz) if ccw else (dr, -dz)
                g.elems.append(Elem(b[0], b[1], c[0], c[1], arc=True, ccw=ccw, radius=value,
                                    cz=b[0] + nz * value, cr=b[1] + nr * value, corner=i))
            else:
                g.elems.append(Elem(b[0], b[1], c[0], c[1], corner=i))
    return g


def build_innen(teil: Teil) -> Geometrie:
    """Geometrie der Innenkontur (Bohrung). Leer, wenn keine Innenkontur eingegeben ist.

    Die Kontur beginnt an der Stirnseite (Z0) und läuft nach innen; der Durchmesser darf dabei
    nicht größer werden. Eine Fase/ein Radius am ersten Punkt ist die Kante am Bohrungseingang.
    """
    g = Geometrie()
    pts = teil.innen
    if not pts:
        return g
    if len(pts) < 2:
        g.errors.append("Innenkontur: mindestens zwei Punkte (Bohrungsanfang an der Stirnseite und Ende).")
        return g
    if abs(pts[0].z) > TOL:
        g.errors.append("Innenkontur: der erste Punkt liegt an der Stirnseite (Z0).")
        return g
    # gedachter Punkt auf der Stirnfläche über dem Bohrungsanfang -> Ecke am Eingang wie jede andere
    zr = [(0.0, pts[0].x / 2.0 + 10.0)] + [(p.z, p.x / 2.0) for p in pts]
    n = len(zr) - 1
    dirs: List[Tuple[float, float, float]] = [(0.0, 0.0, 0.0)]
    for i in range(1, n + 1):
        dz, dr = zr[i][0] - zr[i - 1][0], zr[i][1] - zr[i - 1][1]
        u = _unit(dz, dr)
        dirs.append(u)
        if i == 1:
            continue
        k = i - 1  # Punktnummer in teil.innen
        if u[2] < TOL:
            g.errors.append("Innenkontur: Punkt {} liegt auf Punkt {}.".format(k, k - 1))
        elif dz > TOL or dr > TOL:
            g.errors.append("Innenkontur Element {}: muss nach innen verlaufen, der Durchmesser darf nicht "
                            "größer werden (Hinterschnitte gehen noch nicht).".format(k))
    for k, p in enumerate(pts):
        if p.x <= TOL:
            g.errors.append("Innenkontur Punkt {}: Durchmesser fehlt.".format(k))
        elif teil.bohr_d > 0 and p.x < teil.bohr_d - TOL:
            g.errors.append("Innenkontur Punkt {}: Ø{} ist kleiner als die Vorbohrung Ø{}.".format(
                k, fmt(p.x), fmt(teil.bohr_d)))
    if g.errors:
        return g
    trims = [0.0] * (n + 1)
    kinds: Dict[int, Tuple[str, float]] = {}
    for i in range(1, n):
        p = pts[i - 1]
        if not p.ecke:
            continue
        value = teil.kante if p.ecke == "entgraten" else p.wert
        if value <= TOL:
            g.errors.append("Innenkontur Punkt {}: Größe für {} fehlt.".format(i - 1, ECKEN.get(p.ecke, p.ecke)))
            continue
        d1, d2 = dirs[i], dirs[i + 1]
        cross = d1[0] * d2[1] - d1[1] * d2[0]
        theta = math.atan2(abs(cross), d1[0] * d2[0] + d1[1] * d2[1])
        if theta < math.radians(0.5):
            continue
        trims[i] = value * math.tan(theta / 2.0) if p.ecke == "radius" else value
        kinds[i] = (p.ecke, value)
    for i in range(2, n + 1):
        if trims[i - 1] + trims[i] > dirs[i][2] + TOL:
            g.errors.append("Innenkontur Element {}: Fase/Radius passt nicht auf die Länge {}.".format(
                i - 1, fmt(dirs[i][2])))
    if g.errors:
        return g
    for i in range(1, n + 1):
        dz, dr, _ln = dirs[i]
        z0, r0 = zr[i - 1]
        z1, r1 = zr[i]
        if i >= 2:
            a = (z0 + dz * trims[i - 1], r0 + dr * trims[i - 1])
            b = (z1 - dz * trims[i], r1 - dr * trims[i])
            if math.hypot(b[0] - a[0], b[1] - a[1]) > EPS:
                g.elems.append(Elem(a[0], a[1], b[0], b[1], seg=i - 1))
        else:
            b = (z1 - dz * trims[i], r1 - dr * trims[i])
        if i in kinds:
            kind, value = kinds[i]
            d2 = dirs[i + 1]
            c = (z1 + d2[0] * trims[i], r1 + d2[1] * trims[i])
            if kind == "radius":
                cross = dz * d2[1] - dr * d2[0]
                ccw = cross > 0
                nz, nr = (-dr, dz) if ccw else (dr, -dz)
                g.elems.append(Elem(b[0], b[1], c[0], c[1], arc=True, ccw=ccw, radius=value,
                                    cz=b[0] + nz * value, cr=b[1] + nr * value, corner=i - 1))
            else:
                g.elems.append(Elem(b[0], b[1], c[0], c[1], corner=i - 1))
    g.first, g.last = 1, len(pts) - 1
    g.turned = [True] * len(pts)
    return g


def polyline(elems: Sequence[Elem], max_deg: float = 5.0) -> List[Tuple[float, float]]:
    if not elems:
        return []
    pts = [(elems[0].z0, elems[0].r0)]
    for e in elems:
        pts.extend(e.points(max_deg))
    return pts


def contour_radius(teil: Teil, geom: Geometrie, z_lo: float, z_hi: float) -> float:
    """Größter Radius des Teils zwischen z_lo und z_hi (vor der Stirnseite: 0, hinter der Kontur: Rohteil)."""
    if z_lo > z_hi:
        z_lo, z_hi = z_hi, z_lo
    pl = polyline(geom.elems, 2.0)
    best = 0.0
    if not pl:
        return teil.rohteil_d / 2.0
    for j in range(1, len(pl)):
        (za, ra), (zb, rb) = pl[j - 1], pl[j]
        lo, hi = min(za, zb), max(za, zb)
        if hi < z_lo - EPS or lo > z_hi + EPS:
            continue
        if abs(zb - za) < EPS:
            best = max(best, ra, rb)
            continue
        for zz in (max(lo, z_lo), min(hi, z_hi)):
            t = (zz - za) / (zb - za)
            best = max(best, ra + t * (rb - ra))
    if z_lo < pl[-1][0] - TOL:  # Bereich hinter dem Konturende ist Rohteil
        best = max(best, teil.rohteil_d / 2.0)
    return best


# ==========================================================================
# NC-Ausgabe
# ==========================================================================


@dataclass
class Move:
    """Bewegung für die Vorschau/Prüfung (Drehen, in z/r)."""

    kind: str  # "G0" | "G1" | "G2" | "G3"
    z0: float
    r0: float
    z1: float
    r1: float
    radius: float = 0.0
    t: int = 0  # Werkzeugnummer
    comp: str = ""  # Schneidenradiuskorrektur in diesem Satz: "" | "G41" | "G42"
    line: int = -1  # Zeile im fertigen Programm (0-basiert)

    def points(self) -> List[Tuple[float, float]]:
        """Stützpunkte (z, r) inkl. Start; Bögen (kürzerer Bogen, CR=) in 10°-Schritten."""
        if self.kind not in ("G2", "G3") or self.radius <= EPS:
            return [(self.z0, self.r0), (self.z1, self.r1)]
        dz, dr = self.z1 - self.z0, self.r1 - self.r0
        chord = math.hypot(dz, dr)
        if chord < EPS:
            return [(self.z0, self.r0), (self.z1, self.r1)]
        h = math.sqrt(max(0.0, self.radius ** 2 - chord ** 2 / 4))
        sign = 1 if self.kind == "G3" else -1  # Mittelpunkt links (G3) bzw. rechts (G2) der Sehne
        cz = (self.z0 + self.z1) / 2 - sign * h * dr / chord
        cr = (self.r0 + self.r1) / 2 + sign * h * dz / chord
        arc = Elem(self.z0, self.r0, self.z1, self.r1, True, self.kind == "G3", self.radius, cz, cr)
        return [(self.z0, self.r0)] + arc.points(10.0)


class _Out:
    def __init__(self, pp: Postprozessor) -> None:
        self.pp = pp
        self.lines: List[str] = []
        self.moves: List[Move] = []
        self.z: Optional[float] = None
        self.r: Optional[float] = None
        self.tool = 0
        self.comp = ""
        self.start: Optional[Tuple[float, float]] = None  # Startpunkt (Werkzeugwechselpunkt) für die Simulation

    def raw(self, line: str) -> None:
        self.lines.append(line)

    def _track(self, kind: str, z: Optional[float], r: Optional[float], radius: float = 0.0) -> None:
        oz = self.z if self.z is not None else (self.start[0] if self.start else None)
        orr = self.r if self.r is not None else (self.start[1] if self.start else None)
        nz = oz if z is None else z
        nr = orr if r is None else r
        if oz is not None and orr is not None and nz is not None and nr is not None:
            self.moves.append(Move(kind, oz, orr, nz, nr, radius, self.tool, self.comp, len(self.lines) - 1))
        self.z = nz if z is not None or self.z is not None else None
        self.r = nr if r is not None or self.r is not None else None

    def move(self, kind: str, r: Optional[float] = None, z: Optional[float] = None, pre: str = "",
             post: str = "", radius: float = 0.0) -> None:
        """Bewegung in X (Radius r, ausgegeben als Durchmesser) / Z. Unveränderte Achsen entfallen."""
        words = [w for w in (pre, kind) if w]
        axes = []
        if r is not None and (self.r is None or fmt(2 * r) != fmt(2 * self.r) or kind in ("G02", "G03")):
            axes.append("X" + self.pp.num(2 * r))
        if z is not None and (self.z is None or fmt(z) != fmt(self.z) or kind in ("G02", "G03")):
            axes.append("Z" + self.pp.num(z))
        if not axes and not pre and not post:
            return
        words += axes
        if post:
            words.append(post)
        if "G41" in pre or "G42" in pre:
            self.comp = "G41" if "G41" in pre else "G42"
        elif "G40" in pre:
            self.comp = ""
        self.raw(" ".join(words))
        short = {"G00": "G0", "G01": "G1", "G02": "G2", "G03": "G3"}[kind]
        self._track(short, z, r, radius)


@dataclass
class Ergebnis:
    code: str = ""
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    moves: List[Move] = field(default_factory=list)  # Drehbearbeitung, für die Vorschau
    arbeitsgaenge: List[str] = field(default_factory=list)
    fraes_ops: List["FraesOp"] = field(default_factory=list)  # Fräsen/Bohren, für die Simulation

    @property
    def ok(self) -> bool:
        return not self.errors


def _turn_head(out: _Out, title: str, w: Werkzeug) -> None:
    pp = out.pp
    out.tool, out.comp = w.t, ""
    out.raw("")
    out.raw(pp.comment(title + " " + w.name))  # Kommentar direkt über dem Werkzeug (Einrichteblatt)
    out.lines.extend(pp.tool_lines(w.t, w.d, pp.vorschub_u, pp.ebene_drehen, w.s))


def _retract(out: _Out, g40: bool = False) -> None:
    pp = out.pp
    out.lines.extend(pp.fill(pp.kuehl_aus))
    out.move("G00", r=pp.wechsel_x / 2.0, pre="G40" if g40 else "")
    out.move("G00", z=pp.wechsel_z)


def _cross(pl: List[Tuple[float, float]], level: float) -> Tuple[float, int]:
    """Z, an dem die Kontur über ``level`` steigt, und Index des folgenden Punkts."""
    for j in range(1, len(pl)):
        (za, ra), (zb, rb) = pl[j - 1], pl[j]
        if rb > level + EPS:
            if rb - ra > EPS:
                t = min(1.0, max(0.0, (level - ra) / (rb - ra)))
                return za + t * (zb - za), j
            return za, j
    return pl[-1][0], len(pl)


def _turning(teil: Teil, geom: Geometrie, res: Ergebnis, out: _Out, nach_schruppen=None,
             nach_schlichten=None) -> None:
    elems = geom.turn_elems()
    if not elems:
        for hook in (nach_schruppen, nach_schlichten):
            if hook is not None and not res.errors:
                hook()
        return
    rough = teil.tool(teil.wz_schruppen)
    fine = teil.tool(teil.wz_schlichten)
    if rough is None:
        res.errors.append("Schruppwerkzeug T{} steht nicht in der Werkzeugliste.".format(teil.wz_schruppen))
    if fine is None:
        res.errors.append("Schlichtwerkzeug T{} steht nicht in der Werkzeugliste.".format(teil.wz_schlichten))
    if rough is not None and rough.ap <= TOL:
        res.errors.append("Schruppwerkzeug T{}: Zustellung fehlt.".format(rough.t))
    if res.errors:
        return
    assert rough is not None and fine is not None
    R_s = teil.rohteil_d / 2.0
    s = max(0.5, teil.sicherheit)
    ax, az = max(0.0, teil.aufmass_x) / 2.0, max(0.0, teil.aufmass_z)
    first = elems[0]
    face = first.seg == 1 and abs(first.z1 - first.z0) < EPS and first.r0 < EPS
    stock_front = max(0.0, teil.planaufmass)

    # ---- Schruppen (Planen + Längs) ----
    pl = [(z + az, r + ax) for z, r in polyline(elems)]
    if pl[-1][1] < R_s + 0.5:  # Ende: senkrecht bis über das Rohteil (Absatz zum ungedrehten Rest)
        pl.append((pl[-1][0], R_s + 0.5))
    front = stock_front
    passes_face: List[float] = []
    if face and stock_front > pl[0][0] + TOL:
        total = stock_front - pl[0][0]
        n = int(math.ceil(total / rough.ap - 1e-9))
        passes_face = [stock_front - total * k / n for k in range(1, n + 1)]
        front = pl[0][0]
    elif face:
        front = max(stock_front, pl[0][0])
    zs = front + s
    k0 = 0
    while k0 + 1 < len(pl) and abs(pl[k0 + 1][0] - pl[0][0]) < EPS:
        k0 += 1  # senkrechter Anfang (Stirnfläche)
    r_low = pl[k0][1]
    levels: List[float] = []
    if R_s - r_low > TOL:
        n = int(math.ceil((R_s - r_low) / rough.ap - 1e-9))
        levels = [R_s - (R_s - r_low) * k / n for k in range(1, n + 1)]
    if passes_face or levels:
        _turn_head(out, "VORDREHEN", rough)
        res.arbeitsgaenge.append("Vordrehen T{}".format(rough.t))
        out.move("G00", r=R_s + 1, z=stock_front + s)
        for zf in passes_face:
            out.move("G00", z=zf)
            out.move("G01", r=-0.8, post="F" + out.pp.num(rough.f))
            out.move("G00", z=zf + 1)
            out.move("G00", r=R_s + 1)
        if levels:
            out.move("G00", z=zs)
        r_prev = R_s
        feed_set = False
        for rk in levels:
            z_end, j = _cross(pl, rk)
            if z_end < zs - TOL:
                out.move("G00", r=rk)
                out.move("G01", z=z_end, post="" if feed_set else "F" + out.pp.num(rough.f))
                feed_set = True
                cz, cr = z_end, rk
                for m in range(j, len(pl)):
                    zb, rb = pl[m]
                    if rb >= r_prev - EPS:
                        if rb - cr > EPS:
                            t = (r_prev - cr) / (rb - cr)
                            cz, cr = cz + t * (zb - cz), r_prev
                        else:
                            cz, cr = zb, rb
                        out.move("G01", r=cr, z=cz)
                        break
                    if abs(zb - cz) > EPS or abs(rb - cr) > EPS:
                        out.move("G01", r=rb, z=zb)
                    cz, cr = zb, rb
                out.move("G01", r=cr + 0.5, z=cz + 0.5)
                out.move("G00", z=zs)
            r_prev = rk
        _retract(out)
    if nach_schruppen is not None:
        nach_schruppen()
        if res.errors:
            return

    # ---- Schlichten ----
    last = elems[-1]
    _turn_head(out, "FERTIGDREHEN", fine)
    res.arbeitsgaenge.append("Fertigdrehen T{}".format(fine.t))
    out.move("G00", r=first.r0, z=max(first.z0 + s, front + 1))
    out.move("G01", z=first.z0, pre="G42", post="F" + out.pp.num(fine.f))
    for e in elems:
        if e.arc:
            out.move("G03" if e.ccw else "G02", r=e.r1, z=e.z1, post=out.pp.kreis + out.pp.num(e.radius),
                     radius=e.radius)
        else:
            out.move("G01", r=e.r1, z=e.z1)
    out.move("G01", r=max(R_s, last.r1) + 0.5)
    _retract(out, g40=True)
    if nach_schlichten is not None:
        nach_schlichten()


# ---------------------------------------------------------------------------
# Innenbearbeitung: Mitte bohren, Bohrstange schruppen/schlichten
# ---------------------------------------------------------------------------


def _bohren_aktiv(teil: Teil) -> bool:
    return teil.bohr_d > TOL and teil.bohr_tiefe > TOL


def _innen_pruefen(teil: Teil, geom: Geometrie, gin: Geometrie, res: Ergebnis) -> None:
    from .werkzeugkatalog import rundwerkzeug

    bohren = _bohren_aktiv(teil)
    if not gin.elems and not bohren:
        return
    if not bohren:
        res.errors.append("Innenkontur: bitte Vorbohren (Ø und Tiefe) angeben.")
        return
    if not gin.elems and _min_radius(teil, geom, -teil.bohr_tiefe, 0.0) - teil.bohr_d / 2 < 0.5:
        res.errors.append("Vorbohren Ø{}: Wand zu dünn.".format(fmt(teil.bohr_d)))
    drill = teil.tool(teil.wz_bohren)
    if drill is None:
        res.errors.append("Bohrer T{} (Vorbohren) steht nicht in der Werkzeugliste.".format(teil.wz_bohren))
    elif drill.art != "bohrer" or drill.lage != "mitte":
        res.errors.append("T{} zum Vorbohren muss ein Bohrer mit Lage „stehend (Drehmitte)“ sein.".format(drill.t))
    else:
        if drill.dm > 0 and abs(drill.dm - teil.bohr_d) > TOL:
            res.warnings.append("Bohrer T{} hat Ø{}, Vorbohren ist mit Ø{} eingetragen.".format(
                drill.t, fmt(drill.dm), fmt(teil.bohr_d)))
        rw = rundwerkzeug(drill.halter)
        if rw is not None and rw.schneidenlaenge + TOL < teil.bohr_tiefe:
            res.errors.append("Bohrer {} (T{}) ist zu kurz: Schneidenlänge {} für {} mm Tiefe.".format(
                rw.code, drill.t, fmt(rw.schneidenlaenge), fmt(teil.bohr_tiefe)))
    if not gin.elems:
        return
    z_last = min(p.z for p in teil.innen)
    if teil.bohr_tiefe < -z_last - TOL:
        res.errors.append("Vorbohren {} tief reicht nicht – die Innenkontur geht bis Z{}.".format(
            fmt(teil.bohr_tiefe), fmt(z_last)))
    for k, p in enumerate(teil.innen):
        aussen = _min_radius(teil, geom, p.z - 0.01, p.z + 0.01)
        if aussen - p.x / 2 < 0.5:
            res.errors.append("Innenkontur Punkt {}: Wand zu dünn (außen Ø{}, innen Ø{}).".format(
                k, fmt(2 * aussen), fmt(p.x)))
    tiefe = -z_last
    for nr, was in ((teil.wz_innen_schruppen, "Schruppen"), (teil.wz_innen_schlichten, "Schlichten")):
        w = teil.tool(nr)
        if w is None:
            res.errors.append("Bohrstange T{} (innen {}) steht nicht in der Werkzeugliste.".format(nr, was))
            continue
        if w.art != "innen":
            res.errors.append("T{} (innen {}) ist keine Bohrstange.".format(nr, was))
            continue
        g = dreh_geometrie(w)
        if g is None:
            continue
        h = g[0]
        if h.dmin > teil.bohr_d + TOL:
            res.errors.append("Bohrstange {} (T{}) braucht mindestens Ø{} – vorgebohrt ist nur Ø{}.".format(
                h.code, w.t, fmt(h.dmin), fmt(teil.bohr_d)))
        if h.laenge - 3 * h.b < tiefe:
            res.errors.append("Bohrstange {} (T{}) ist zu kurz für {} mm Tiefe (Einspannung mind. 3×Ø).".format(
                h.code, w.t, fmt(tiefe)))
        elif tiefe > 4 * h.b:
            res.warnings.append("Bohrstange {} (T{}): Auskragung {} mm > 4×Ø – Rattergefahr, Schnittwerte "
                                "reduzieren oder stärkere Stange.".format(h.code, w.t, fmt(tiefe)))


def _min_radius(teil: Teil, geom: Geometrie, z_lo: float, z_hi: float) -> float:
    """Kleinster Außenradius des Fertigteils zwischen z_lo und z_hi (für die Wandstärke)."""
    if not geom.elems:
        return teil.rohteil_d / 2.0
    best = float("inf")
    pl = polyline(geom.elems, 2.0)
    for (za, ra), (zb, rb) in zip(pl, pl[1:]):
        lo, hi = min(za, zb), max(za, zb)
        if hi < z_lo - EPS or lo > z_hi + EPS:
            continue
        if hi - lo < EPS:
            best = min(best, max(ra, rb))  # Absatz: zählt der größere Durchmesser
            continue
        for z in (max(lo, z_lo), min(hi, z_hi)):
            best = min(best, ra + (rb - ra) * (z - za) / (zb - za))
    if best == float("inf"):
        return teil.rohteil_d / 2.0
    return best


def _mitte_bohren(teil: Teil, res: Ergebnis, out: _Out) -> None:
    from .werkzeugkatalog import rundwerkzeug

    w = teil.tool(teil.wz_bohren)
    if w is None:
        return
    s = max(0.5, teil.sicherheit)
    rd = teil.bohr_d / 2
    rw = rundwerkzeug(w.halter)
    spitze = rw.spitzenwinkel if rw is not None else 118.0
    z_tip = -(teil.bohr_tiefe + (rd / math.tan(math.radians(spitze / 2)) if spitze < 179 else 0.0))
    front = max(0.0, teil.planaufmass)
    _turn_head(out, "VORBOHREN", w)
    out.move("G00", r=0.0, z=front + s)
    depths = _steps(0.0, z_tip, w.ap if w.ap > TOL else -z_tip)
    prev = None
    for i, dv in enumerate(depths):
        if i:
            out.move("G00", z=front + s)
            out.move("G00", z=prev + 0.5)
        out.move("G01", z=dv, post="F" + out.pp.num(w.f) if i == 0 else "")
        prev = dv
    out.move("G00", z=front + s)
    _retract(out)
    res.arbeitsgaenge.append("Bohren Ø{} T{}".format(fmt(teil.bohr_d), w.t))


def _innen_schruppen(teil: Teil, gin: Geometrie, res: Ergebnis, out: _Out) -> None:
    w = teil.tool(teil.wz_innen_schruppen)
    if w is None or not gin.elems:
        return
    if w.ap <= TOL:
        res.errors.append("Bohrstange T{}: Zustellung fehlt.".format(w.t))
        return
    s = max(0.5, teil.sicherheit)
    ax, az = max(0.0, teil.aufmass_x) / 2.0, max(0.0, teil.aufmass_z)
    rd = teil.bohr_d / 2
    zs = max(0.0, teil.planaufmass) + s
    pl = [(z + az, r - ax) for z, r in polyline(gin.elems)]
    if pl[-1][1] > rd - 0.5:  # Ende: senkrecht bis in die Vorbohrung (Absatz bzw. Boden)
        pl.append((pl[-1][0], rd - 0.5))
    r_high = max(r for _, r in pl)
    if r_high - rd <= TOL:
        return
    n = int(math.ceil((r_high - rd) / w.ap - 1e-9))
    levels = [rd + (r_high - rd) * k / n for k in range(1, n + 1)]
    _turn_head(out, "INNEN VORDREHEN", w)
    res.arbeitsgaenge.append("Innen vordrehen T{}".format(w.t))
    out.move("G00", r=rd - 0.5, z=zs)
    r_prev = rd
    feed = False
    for rk in levels:
        z_end, j = None, len(pl)
        for k in range(1, len(pl)):
            (za, ra), (zb, rb) = pl[k - 1], pl[k]
            if rb < rk - EPS:
                t = 0.0 if ra - rb < EPS else min(1.0, max(0.0, (ra - rk) / (ra - rb)))
                z_end, j = za + t * (zb - za), k
                break
        if z_end is None or z_end >= zs - TOL:
            r_prev = rk
            continue
        out.move("G00", r=rk)
        out.move("G01", z=z_end, post="" if feed else "F" + out.pp.num(w.f))
        feed = True
        cz, cr = z_end, rk
        for m in range(j, len(pl)):
            zb, rb = pl[m]
            if rb <= r_prev + EPS:
                if cr - rb > EPS:
                    t = (cr - r_prev) / (cr - rb)
                    cz, cr = cz + t * (zb - cz), r_prev
                else:
                    cz, cr = zb, rb
                out.move("G01", r=cr, z=cz)
                break
            if abs(zb - cz) > EPS or abs(rb - cr) > EPS:
                out.move("G01", r=rb, z=zb)
            cz, cr = zb, rb
        out.move("G01", r=cr - 0.5, z=cz + 0.5)
        out.move("G00", z=zs)
        r_prev = rk
    _retract(out)


def _innen_schlichten(teil: Teil, gin: Geometrie, res: Ergebnis, out: _Out) -> None:
    w = teil.tool(teil.wz_innen_schlichten)
    if w is None or not gin.elems:
        return
    pp = out.pp
    s = max(0.5, teil.sicherheit)
    rd = teil.bohr_d / 2
    zs = max(0.0, teil.planaufmass) + s
    elems = gin.elems
    first, last = elems[0], elems[-1]
    _turn_head(out, "INNEN FERTIGDREHEN", w)
    res.arbeitsgaenge.append("Innen fertigdrehen T{}".format(w.t))
    out.move("G00", r=first.r0, z=zs)
    out.move("G01", z=first.z0, pre="G41", post="F" + pp.num(w.f))
    for e in elems:
        if e.arc:
            out.move("G03" if e.ccw else "G02", r=e.r1, z=e.z1, post=pp.kreis + pp.num(e.radius), radius=e.radius)
        else:
            out.move("G01", r=e.r1, z=e.z1)
    out.move("G01", r=min(last.r1, rd) - 0.5)  # Absatz/Boden bis in die Vorbohrung, weg von der Wand
    out.lines.extend(pp.fill(pp.kuehl_aus))
    out.move("G00", z=zs, pre="G40")  # erst aus der Bohrung heraus, dann zum Wechselpunkt
    out.move("G00", r=pp.wechsel_x / 2.0)
    out.move("G00", z=pp.wechsel_z)


# ---------------------------------------------------------------------------
# Fräsen / Bohren mit angetriebenem Werkzeug
# ---------------------------------------------------------------------------


@dataclass
class FMove:
    """Fräsbewegung (für die Simulation); None = Achse bleibt. x als Durchmesser wie im Programm."""

    kind: str  # "G0" | "G1"
    x: Optional[float] = None
    y: Optional[float] = None
    z: Optional[float] = None
    c: Optional[float] = None  # Mantel (Zylinder-Interpolation): Winkel der Werkzeugachse in Grad


@dataclass
class FraesOp:
    """Ein Fräs-/Bohr-Arbeitsgang: dieselben Bewegungen je Winkellage."""

    nr: int  # Nummer der Fräsung (1-basiert)
    t: int
    lage: str  # "radial" | "axial"
    winkel: List[float]
    moves: List[FMove]
    zeilen: List[int]  # erste Programmzeile der Bewegungen je Winkel
    entgraten: bool = False
    titel: str = ""


class _MillOut:
    def __init__(self, pp: Postprozessor) -> None:
        self.pp = pp
        self.lines: List[str] = []
        self.moves: List[FMove] = []

    def g(self, code: str, x: Optional[float] = None, y: Optional[float] = None, z: Optional[float] = None,
          f: Optional[float] = None) -> None:
        """x = Durchmesser (wie beim Drehen)."""
        words = [code]
        for name, v in (("X", x), ("Y", y), ("Z", z)):
            if v is not None:
                words.append(name + self.pp.num(v))
        if f is not None:
            words.append("F" + self.pp.num(f))
        self.lines.append(" ".join(words))
        self.moves.append(FMove("G0" if code == "G00" else "G1", x, y, z))


def _lin(a: float, b: float, n: int) -> List[float]:
    if n <= 1:
        return [a]
    return [a + (b - a) * i / (n - 1) for i in range(n)]


def _steps(top: float, bottom: float, ap: float) -> List[float]:
    """Zwischentiefen von top nach bottom (inkl. bottom), höchstens ap je Schritt."""
    depth = top - bottom
    if depth <= TOL:
        return [bottom]
    n = int(math.ceil(depth / ap - 1e-9)) if ap > TOL else 1
    return [top - depth * k / n for k in range(1, n + 1)]


def _check_tool(res: Ergebnis, nr: int, fr: Fraesung, teil: Teil, t: int, arten: Sequence[str],
                what: str) -> Optional[Werkzeug]:
    w = teil.tool(t)
    label = "Fräsung {} ({})".format(nr, FEATURES.get(fr.art, fr.art))
    if w is None:
        res.errors.append("{}: {} T{} steht nicht in der Werkzeugliste.".format(label, what, t))
        return None
    if w.art not in arten:
        res.errors.append("{}: T{} ist ein {} – hier wird ein {} gebraucht.".format(
            label, t, TOOL_ARTEN.get(w.art, w.art), " oder ".join(TOOL_ARTEN[a] for a in arten)))
        return None
    need = FEATURE_LAGE[fr.art]
    if w.lage and w.lage != need:
        res.errors.append("{}: T{} ist {} eingebaut, gebraucht wird ein {}es Werkzeug.".format(
            label, t, LAGEN[w.lage], need))
        return None
    if w.art in ("fraeser", "fase") and w.dm <= TOL:
        res.errors.append("{}: Durchmesser von T{} fehlt.".format(label, t))
        return None
    return w


def _flaeche_umriss(fr: Fraesung, rp: float, z_front: float, run_out: bool) -> List[Tuple[float, float, float]]:
    """Kanten einer Fläche als (y, z, r_kante) – lange Kanten, Bogen an der Wand, ggf. Stirnkante."""
    h = fr.mass / 2.0
    w2 = math.sqrt(max(0.0, rp * rp - h * h))
    z_hi, z_lo = max(fr.z1, fr.z2), min(fr.z1, fr.z2)

    def arc(zw: float, y0: float, y1: float) -> List[Tuple[float, float, float]]:
        n = 16
        return [(y, zw, math.sqrt(max(0.0, rp * rp - y * y))) for y in _lin(y0, y1, n + 1)]

    pts = [(-w2, z_front if run_out else z_hi, h)]
    pts += arc(z_lo, -w2, w2)
    if run_out:
        pts += [(w2, z_front, h), (-w2, z_front, h)]
    else:
        pts += arc(z_hi, w2, -w2)
    return pts


def _nut_umriss(fr: Fraesung, rp: float, rt: float, z_front: float, run_out: bool) -> List[Tuple[float, float, float]]:
    """Obere Kanten einer Nut (Rechteck mit Fräserradius in den Ecken) als (y, z, r_kante)."""
    b2 = fr.mass / 2.0
    z_hi, z_lo = max(fr.z1, fr.z2), min(fr.z1, fr.z2)
    rt = min(rt, b2)
    yz: List[Tuple[float, float]] = []

    def corner(cy: float, cz: float, a0: float, a1: float) -> None:
        for i in range(1, 7):
            a = math.radians(a0 + (a1 - a0) * i / 6)
            yz.append((cy + rt * math.cos(a), cz + rt * math.sin(a)))

    yz.append((-b2, z_front if run_out else z_hi - rt))
    yz.append((-b2, z_lo + rt))
    corner(-b2 + rt, z_lo + rt, 180, 270)
    yz.append((b2 - rt, z_lo))
    corner(b2 - rt, z_lo + rt, 270, 360)
    if run_out:
        yz.append((b2, z_front))
    else:
        yz.append((b2, z_hi - rt))
        corner(b2 - rt, z_hi - rt, 0, 90)
        yz.append((-b2 + rt, z_hi))
        corner(-b2 + rt, z_hi - rt, 90, 180)
    return [(y, z, math.sqrt(max(0.0, rp * rp - y * y))) for y, z in yz]


def _feature_body(teil: Teil, geom: Geometrie, nr: int, fr: Fraesung, w: Werkzeug, res: Ergebnis,
                  deburr: bool, pp: Postprozessor) -> Optional[Tuple[str, str, List[str], str, List[FMove]]]:
    """Bewegungen je Winkellage. Ergebnis: (Titel, Werkzeuglage radial/axial, Bewegungen, Rückzugsachse)."""
    R_s = teil.rohteil_d / 2.0
    s = max(0.5, teil.sicherheit)
    label = "Fräsung {} ({})".format(nr, FEATURES.get(fr.art, fr.art))
    z_front = 0.0
    m = _MillOut(pp)
    c = fr.fase
    rad = w.dm / 2.0

    if fr.art in ("flaeche", "nut"):
        z_hi, z_lo = max(fr.z1, fr.z2), min(fr.z1, fr.z2)
        if z_hi - z_lo < TOL:
            res.errors.append("{}: Z rechts und Z links sind gleich.".format(label))
            return None
        if z_lo < z_front - TOL and z_hi > z_front + TOL:
            z_hi = z_front
        run_out = z_hi >= z_front - TOL
        # Anfahren über dem größten Durchmesser im Bereich (inkl. Absätze an den Rändern),
        # Tiefe und Kanten vom Durchmesser, auf dem die Fräsung liegt
        rp_all = contour_radius(teil, geom, z_lo, min(z_hi, z_front))
        x_app = 2 * (rp_all + s)
        rp = contour_radius(teil, geom, z_lo + 0.01, min(z_hi, z_front) - 0.01)

        if fr.art == "flaeche":
            h = fr.mass / 2.0
            if h <= TOL or h >= rp - TOL:
                res.errors.append("{}: Schlüsselweite {} muss kleiner als der Durchmesser Ø{} sein.".format(
                    label, fmt(fr.mass), fmt(2 * rp)))
                return None
            title = "FRAESEN FLAECHE SW{}".format(fmt(fr.mass))
            if deburr:
                title = "ENTGRATEN FLAECHE SW{}".format(fmt(fr.mass))
                pts = _flaeche_umriss(fr, rp, z_front, run_out)
                if not _cone_clear(teil, geom, [(z, r - c) for _, z, r in pts], rad, rp):
                    res.errors.append(_cone_msg(label, w))
                    return None
                _edge_path(m, pts, c, x_app, w.f)
                return title, "radial", m.lines, "x", m.moves
            zc_min = z_lo + rad
            zc_max = max(zc_min, z_front + 1 - rad) if run_out else z_hi - rad
            if zc_max < zc_min - TOL:
                res.errors.append("{}: Fräser Ø{} ist zu groß für die Flächenlänge {}.".format(
                    label, fmt(w.dm), fmt(z_hi - z_lo)))
                return None
            nz = int(math.ceil((zc_max - zc_min) / (0.7 * w.dm) - 1e-9)) + 1 if zc_max - zc_min > TOL else 1
            zcs = _lin(zc_max, zc_min, nz)
            y_out = math.sqrt(max(0.0, rp_all * rp_all - h * h)) + rad + 1
            strat = strategie(fr)
            if strat in ("gleichlauf", "gegenlauf"):
                # immer in eine Richtung schneiden, zurück über dem Teil. Bezogen auf rechtsdrehendes
                # Werkzeug (M3): Material liegt bei -Z (Bahnen von rechts nach links) -> Gleichlauf = -Y
                d = -1.0 if strat == "gleichlauf" else 1.0
                for hi in _steps(rp, h, w.ap):
                    for zc in zcs:
                        m.g("G00", x=x_app)
                        m.g("G00", z=zc, y=-d * y_out)
                        m.g("G01", x=2 * hi, f=w.f)
                        m.g("G01", y=d * y_out)
                m.g("G00", x=x_app)
                return title, "radial", m.lines, "x", m.moves
            m.g("G00", z=zcs[0], y=-y_out)
            m.g("G00", x=x_app)
            side = -1
            for i, hi in enumerate(_steps(rp, h, w.ap)):
                order = zcs if i % 2 == 0 else list(reversed(zcs))
                m.g("G01", x=2 * hi, f=w.f)
                for k, zc in enumerate(order):
                    if k:
                        m.g("G01", z=zc)
                    side = -side
                    m.g("G01", y=side * y_out)
            m.g("G00", x=x_app)
            return title, "radial", m.lines, "x", m.moves

        # Nut
        b, t = fr.mass, fr.tiefe
        if t <= TOL or t >= rp:
            res.errors.append("{}: Tiefe {} passt nicht (Ø{}).".format(label, fmt(t), fmt(2 * rp)))
            return None
        if b <= TOL or b >= 2 * rp - TOL:
            res.errors.append("{}: Breite {} passt nicht.".format(label, fmt(b)))
            return None
        title = "FRAESEN NUT {}X{}".format(fmt(b), fmt(t))
        if deburr:
            nut_tool = teil.tool(fr.wz)
            corner_r = nut_tool.dm / 2.0 if nut_tool is not None and nut_tool.dm > 0 else 0.0
            pts = _nut_umriss(fr, rp, corner_r, z_front, run_out)
            if not _cone_clear(teil, geom, [(z, r - c) for _, z, r in pts], rad, rp):
                res.errors.append(_cone_msg(label, w))
                return None
            _edge_path(m, pts, c, x_app, w.f)
            return "ENTGRATEN NUT {}X{}".format(fmt(b), fmt(t)), "radial", m.lines, "x", m.moves
        if w.dm > b + TOL:
            res.errors.append("{}: Fräser Ø{} ist breiter als die Nut ({}).".format(label, fmt(w.dm), fmt(b)))
            return None
        zc_min = z_lo + rad
        zc_max = z_front + rad + 1 if run_out else z_hi - rad
        if zc_max < zc_min - TOL:
            res.errors.append("{}: Fräser Ø{} ist zu groß für die Nutlänge {}.".format(
                label, fmt(w.dm), fmt(z_hi - z_lo)))
            return None
        if b - w.dm <= TOL:
            ys = [0.0]
        else:
            ys = _lin(-(b - w.dm) / 2, (b - w.dm) / 2, int(math.ceil((b - w.dm) / (0.75 * w.dm) - 1e-9)) + 1)
        rb = rp - t
        f_plunge = max(1.0, round(w.f * 0.3))
        strat = strategie(fr)
        if strat == "trochoidal":
            if b < TROCHOID_MIN * w.dm - TOL:
                res.errors.append("{}: Für trochoidales Fräsen muss die Nut mindestens {:g} × so breit sein wie der "
                                  "Fräser (Ø{} -> ab {}).".format(label, TROCHOID_MIN, fmt(w.dm),
                                                                 fmt(TROCHOID_MIN * w.dm)))
                return None
            _trochoid(m, rp, rb, (b - w.dm) / 2, zc_min, zc_max, w, x_app, f_plunge)
            return title, "radial", m.lines, "x", m.moves
        m.g("G00", z=zc_max, y=ys[0])
        m.g("G00", x=x_app)
        at_start = True
        order = ys
        ramp = strat == "rampe" and zc_max - zc_min > TOL and not run_out
        if strat == "rampe" and not ramp and not run_out:
            res.warnings.append("{}: Nut so lang wie der Fräser – Rampe nicht möglich, es wird senkrecht "
                                "eingetaucht.".format(label))
        cur_x = rp + s
        for i, ri in enumerate(_steps(rp, rb, w.ap)):
            if ramp:  # schräg eintauchen: hin und her entlang der Nutmitte
                top = min(cur_x, rp + 0.5)
                if abs(top - cur_x) > TOL:
                    m.g("G01", x=2 * top, f=f_plunge)
                per_stroke = (zc_max - zc_min) * math.tan(math.radians(RAMPENWINKEL))
                strokes = max(1, int(math.ceil((top - ri) / per_stroke - 1e-9)))
                zpos = zc_max if at_start else zc_min
                for k in range(1, strokes + 1):
                    zpos = zc_min if zpos == zc_max else zc_max
                    m.g("G01", x=2 * (top - (top - ri) * k / strokes), z=zpos, f=w.f if k == 1 else None)
                if (zpos == zc_max) != at_start:
                    at_start = not at_start
                    m.g("G01", z=zc_max if at_start else zc_min)
            else:
                m.g("G01", x=2 * ri, f=f_plunge)
            cur_x = ri
            for k, y in enumerate(order):
                if k:
                    m.g("G01", y=y)
                m.g("G01", z=zc_min if at_start else zc_max, f=w.f if k == 0 else None)
                at_start = not at_start
            if len(ys) > 1 and zc_max - zc_min > TOL:
                # mehrere Bahnen: einmal um die Mittelpunktsbahn herum, sonst bleiben an den
                # Nutenden Rundungen zwischen den Bahnen stehen (Ende wieder an derselben Ecke)
                y_end, z_end = order[-1], (zc_max if at_start else zc_min)
                m.g("G01", y=order[0])
                m.g("G01", z=zc_min if z_end == zc_max else zc_max)
                m.g("G01", y=y_end)
                m.g("G01", z=z_end)
            order = list(reversed(order))
        m.g("G00", x=x_app)
        return title, "radial", m.lines, "x", m.moves

    if fr.art == "bohrung_radial":
        d, t, zb = fr.mass, fr.tiefe, fr.z1
        if d <= TOL or t <= TOL:
            res.errors.append("{}: Durchmesser und Tiefe angeben.".format(label))
            return None
        rp = contour_radius(teil, geom, zb - 0.001, zb + 0.001)  # Oberfläche in der Bohrungsachse
        if zb > z_front - d / 2 + TOL or rp <= TOL:
            res.errors.append("{}: Z{} liegt nicht auf dem Teil.".format(label, fmt(zb)))
            return None
        rp_max = contour_radius(teil, geom, zb - d / 2 + 0.01, zb + d / 2 - 0.01)
        if rp_max > rp + 0.5:
            res.errors.append("{}: Die Bohrung Ø{} bei Z{} liegt zu nah an einem Absatz.".format(
                label, fmt(d), fmt(zb)))
            return None
        x_app = 2 * (rp_max + s)
        m.g("G00", z=zb, y=0)
        m.g("G00", x=x_app)
        if deburr:
            if w.dm > 0 and d / 2 + c > rad + TOL:
                res.errors.append("{}: Fasenfräser T{} (Ø{}) ist für Ø{} + Fase {} zu klein.".format(
                    label, w.t, fmt(w.dm), fmt(d), fmt(c)))
                return None
            if not _cone_clear(teil, geom, [(zb, rp - d / 2 - c)], rad, rp):
                res.errors.append(_cone_msg(label, w))
                return None
            m.g("G01", x=2 * (rp - d / 2 - c), f=w.f)
            m.g("G00", x=x_app)
            return "ENTGRATEN BOHRUNG D{}".format(fmt(d)), "radial", m.lines, "x", m.moves
        if w.dm > 0 and abs(w.dm - d) > TOL:
            res.warnings.append("{}: Bohrer T{} hat Ø{}, die Bohrung Ø{}.".format(label, w.t, fmt(w.dm), fmt(d)))
        _peck(m, "x", x_app, [2 * v for v in _steps(rp, rp - t, w.ap if w.ap > TOL else t)], 1.0, w.f,
              strategie(fr))
        return "BOHREN D{} RADIAL".format(fmt(d)), "radial", m.lines, "x", m.moves

    if fr.art == "bohrung_axial":
        d, t, zf, lk = fr.mass, fr.tiefe, fr.z1, fr.teilkreis
        if d <= TOL or t <= TOL:
            res.errors.append("{}: Durchmesser und Tiefe angeben.".format(label))
            return None
        if lk < -TOL or lk / 2 + d / 2 > R_s + TOL:
            res.errors.append("{}: Lochkreis Ø{} passt nicht auf das Teil.".format(label, fmt(lk)))
            return None
        if zf > z_front + TOL:
            res.errors.append("{}: Z{} liegt vor der Stirnseite.".format(label, fmt(zf)))
            return None
        r_face = contour_radius(teil, geom, zf - 0.01, zf)
        if lk / 2 + d / 2 + (c if deburr else 0) > r_face + TOL:
            res.errors.append("{}: Die Bohrungen (Lochkreis Ø{}) liegen nicht auf der Planfläche bei Z{} "
                              "(Ø{}).".format(label, fmt(lk), fmt(zf), fmt(2 * r_face)))
            return None
        if zf < z_front - TOL:
            tool_r = max(d / 2, rad)
            r_front = contour_radius(teil, geom, zf + 0.01, z_front)
            if lk / 2 - tool_r < r_front - TOL:
                res.errors.append("{}: Das Werkzeug käme auf dem Weg zur Planfläche bei Z{} an den davor liegenden "
                                  "Durchmesser Ø{}.".format(label, fmt(zf), fmt(2 * r_front)))
                return None
        m.g("G00", x=lk, y=0)
        m.g("G00", z=zf + s)
        if deburr:
            if w.dm > 0 and d / 2 + c > rad + TOL:
                res.errors.append("{}: Fasenfräser T{} (Ø{}) ist für Ø{} + Fase {} zu klein.".format(
                    label, w.t, fmt(w.dm), fmt(d), fmt(c)))
                return None
            m.g("G01", z=zf - d / 2 - c, f=w.f)
            m.g("G00", z=zf + s)
            return "ENTGRATEN STIRNBOHRUNG D{}".format(fmt(d)), "axial", m.lines, "z", m.moves
        if w.dm > 0 and abs(w.dm - d) > TOL:
            res.warnings.append("{}: Bohrer T{} hat Ø{}, die Bohrung Ø{}.".format(label, w.t, fmt(w.dm), fmt(d)))
        _peck(m, "z", zf + s, _steps(zf, zf - t, w.ap if w.ap > TOL else t), 0.5, w.f, strategie(fr))
        return "BOHREN D{} STIRN".format(fmt(d)), "axial", m.lines, "z", m.moves

    res.errors.append("{}: unbekannte Art „{}“.".format(label, fr.art))
    return None


def _cone_clear(teil: Teil, geom: Geometrie, tips: List[Tuple[float, float]], rad: float, rp: float) -> bool:
    """Stößt der Kegel eines 90°-Fasenfräsers (Spitzen bei (z, r)) an einen größeren Absatz?

    Vorsichtig gerechnet: Der Kegel ist im Abstand d von der Achse um d höher als die Spitze;
    jeder Durchmesser größer als ``rp`` (der bearbeitete) im Umkreis von ``rad`` muss darunter bleiben.
    """
    if not tips or rad <= 0:
        return True
    z_min = min(z for z, _ in tips) - rad
    z_max = max(z for z, _ in tips) + rad
    n = max(2, int(math.ceil((z_max - z_min) / 0.1)))
    for i in range(n + 1):
        z = z_min + (z_max - z_min) * i / n
        big = contour_radius(teil, geom, z - 0.05, z + 0.05)
        if big <= rp + TOL:
            continue
        for zt, rt in tips:
            dz = abs(z - zt)
            if dz <= rad and big > rt + dz + TOL:
                return False
    return True


def _cone_msg(label: str, w: Werkzeug) -> str:
    return ("{}: Entgraten geht hier nicht – der Fasenfräser T{} (Ø{}) käme an einen größeren Durchmesser "
            "daneben. Entgraten ausschalten, kleineren Fasenfräser nehmen oder mehr Abstand zum Absatz."
            .format(label, w.t, fmt(w.dm)))


def _edge_path(m: _MillOut, pts: List[Tuple[float, float, float]], c: float, x_app: float, f: float) -> None:
    """Fasenfräser (Spitze) entlang der Kanten: Achse auf der Kante, Spitze um die Fase tiefer."""
    y0, z0, r0 = pts[0]
    m.g("G00", z=z0, y=y0)
    m.g("G00", x=x_app)
    m.g("G01", x=2 * (r0 - c), f=f)
    for y, z, r in pts[1:]:
        m.g("G01", x=2 * (r - c), y=y, z=z)
    m.g("G00", x=x_app)


def _peck(m: _MillOut, axis: str, safe: float, depths: List[float], back: float, f: float,
          art: str = "entspanen") -> None:
    """Bohren: „entspanen“ = nach jedem Schritt ganz raus, „spanbruch“ = kurz zurück, „einzug“ = am Stück."""
    key = axis.lower()
    if art == "einzug":
        depths = depths[-1:]
    prev = None
    for i, dv in enumerate(depths):
        if i:
            if art == "spanbruch":
                m.g("G00", **{key: prev + back})
            else:
                m.g("G00", **{key: safe})
                m.g("G00", **{key: prev + back})
        m.g("G01", **{key: dv}, f=f if i == 0 else None)
        prev = dv
    m.g("G00", **{key: safe})


def _trochoid(m: _MillOut, rp: float, rb: float, rl: float, zc_min: float, zc_max: float, w: Werkzeug,
              x_app: float, f_plunge: float) -> None:
    """Trochoidal: Fräsermitte läuft Kreise (Radius rl) und rückt dabei entlang der Nut vor;
    am Ende eine Runde an den Wänden entlang. Kreise als kurze Geraden (sicher in jeder Ebene)."""
    step = max(0.05 * w.dm, min(0.2 * w.dm, rl))
    rz = min(rl, max(0.0, zc_max - zc_min) / 2)  # kurze Nut: Schleifen in Längsrichtung flacher (Ellipse)
    c_hi, c_lo = zc_max - rz, zc_min + rz
    m.g("G00", z=c_hi + rz, y=0)
    m.g("G00", x=x_app)
    for ri in _steps(rp, rb, w.ap):
        m.g("G01", x=2 * ri, f=f_plunge)
        length = c_hi - c_lo
        loops = max(1, int(math.ceil(length / step - 1e-9))) if length > TOL else 1
        n = 24
        for k in range(loops * n + 1):
            th = 2 * math.pi * k / n
            c = c_hi - length * min(1.0, k / (loops * n)) if length > TOL else c_hi
            m.g("G01", y=rl * math.sin(th), z=c + rz * math.cos(th), f=w.f if k == 0 else None)
        # einmal ganz herum: die Fräsermitte fährt das Rechteck [-rl, rl] x [zc_min, zc_max] ab,
        # damit Wände und Ecken sauber werden; Ende am Anfang der nächsten Ebene
        m.g("G01", y=rl, z=zc_max)
        m.g("G01", z=zc_min)
        m.g("G01", y=-rl)
        m.g("G01", z=zc_max)
        m.g("G01", y=0)
    m.g("G00", x=x_app)


# ---------------------------------------------------------------------------
# Entgraten auf der Mantelfläche mit Zylinder-Interpolation (TRACYL / G07.1)
# ---------------------------------------------------------------------------


def _entgrat_kanten(teil: Teil, geom: Geometrie, nr: int, fr: Fraesung, w: Werkzeug, res: Ergebnis):
    """Kanten einer Fläche/Nut zum Entgraten: (Titel, Punkte (y, z, r), rp, X-Anfahrt, ausläufig, Fase)."""
    label = "Fräsung {} ({})".format(nr, FEATURES.get(fr.art, fr.art))
    z_front = 0.0
    z_hi, z_lo = max(fr.z1, fr.z2), min(fr.z1, fr.z2)
    if z_hi - z_lo < TOL:
        res.errors.append("{}: Z rechts und Z links sind gleich.".format(label))
        return None
    if z_lo < z_front - TOL and z_hi > z_front + TOL:
        z_hi = z_front
    run_out = z_hi >= z_front - TOL
    rp_all = contour_radius(teil, geom, z_lo, min(z_hi, z_front))
    x_app = 2 * (rp_all + max(0.5, teil.sicherheit))
    rp = contour_radius(teil, geom, z_lo + 0.01, min(z_hi, z_front) - 0.01)
    c = fr.fase
    if fr.art == "flaeche":
        h = fr.mass / 2.0
        if h <= TOL or h >= rp - TOL:
            res.errors.append("{}: Schlüsselweite {} muss kleiner als der Durchmesser Ø{} sein.".format(
                label, fmt(fr.mass), fmt(2 * rp)))
            return None
        pts = _flaeche_umriss(fr, rp, z_front, run_out)
        title = "ENTGRATEN FLAECHE SW{}".format(fmt(fr.mass))
    else:
        b, t = fr.mass, fr.tiefe
        if t <= TOL or t >= rp or b <= TOL or b >= 2 * rp - TOL:
            res.errors.append("{}: Breite/Tiefe passen nicht zu Ø{}.".format(label, fmt(2 * rp)))
            return None
        nut_tool = teil.tool(fr.wz)
        corner_r = nut_tool.dm / 2.0 if nut_tool is not None and nut_tool.dm > 0 else 0.0
        pts = _nut_umriss(fr, rp, corner_r, z_front, run_out)
        title = "ENTGRATEN NUT {}X{}".format(fmt(b), fmt(t))
    if not _cone_clear(teil, geom, [(z, r - c) for _, z, r in pts], w.dm / 2.0, rp):
        res.errors.append(_cone_msg(label, w))
        return None
    return title, pts, rp, x_app, run_out, c


def _norm_winkel(a: float) -> float:
    a = a % 360.0
    return 0.0 if abs(a - 360.0) < 1e-6 else a


def _zyl_entgraten(teil: Teil, geom: Geometrie, nr: int, fr: Fraesung, w: Werkzeug, res: Ergebnis,
                   out: _Out) -> None:
    """Kanten auf der Mantelfläche: C-Achse interpoliert mit X/Z (Siemens TRACYL, Fanuc G07.1).

    Alle Kanten einer Nut (und die Längskanten und Bögen einer Fläche) liegen auf dem Zylinder rp. Mit
    Zylinder-Interpolation steht der Fasenfräser immer radial und gleich tief (Spitze bei rp − Fase);
    die Steuerung dreht die Spindel mit. Je Winkellage: C und Z außerhalb positionieren, Zylinder ein,
    eintauchen, Kontur in (Umfang, Z), Zylinder aus, frei fahren. Bei einer zur Stirn auslaufenden Fläche
    ist die Kante an der Stirnseite gerade – die folgt danach wie bisher mit der Y-Achse.
    """
    pp = out.pp
    k = _entgrat_kanten(teil, geom, nr, fr, w, res)
    if k is None:
        return
    title, pts, rp, x_app, run_out, c = k
    stirn: List[Tuple[float, float, float]] = []
    mantel = pts
    if fr.art == "flaeche" and run_out:
        mantel, stirn = pts[:-1], pts[-2:]
    achse = (pp.zyl_achse.strip().upper() or "Y")[0]
    phis = [math.asin(max(-1.0, min(1.0, y / rp))) for y, _z, _r in mantel]
    # Eintauchtiefe je Punkt: Die Wände (Nut) bzw. die Fläche stehen nicht radial, sondern parallel zur
    # Y-Achse. Der radial stehende 90°-Kegel ist dort um beta zur Wand gekippt; damit die Fase an der Wand
    # trotzdem c lang wird, taucht er um c·(cos beta + sin beta) ein (beta = 0 an Wänden quer zur Achse).
    tiefen: List[float] = []
    for i, phi in enumerate(phis):
        ya, za, _ra = mantel[max(0, i - 1)]
        yb, zb, _rb = mantel[min(len(mantel) - 1, i + 1)]
        ln = math.hypot(yb - ya, zb - za)
        n_y = abs(zb - za) / ln if ln > EPS else 0.0  # Y-Anteil der Wand-Normalen (Tangente in y/z)
        beta = math.asin(min(1.0, abs(math.sin(phi)) * n_y))
        tiefen.append(c * (math.cos(beta) + math.sin(beta)))
    if not _cone_clear(teil, geom, [(z, rp - d) for (_y, z, _r), d in zip(mantel, tiefen)], w.dm / 2.0, rp):
        res.errors.append(_cone_msg("Fräsung {} ({})".format(nr, FEATURES.get(fr.art, fr.art)), w))
        return

    def lokal(r: float, phi: float, z: Optional[float] = None, kind: str = "G1") -> FMove:
        """Werkzeug radial unter phi (zur Mittellage der Winkellage) im Rahmen der Winkellage."""
        return FMove(kind, 2 * r * math.cos(phi), r * math.sin(phi), z)

    add = out.lines.extend
    out.raw("")
    out.raw(pp.comment("{} {}".format(title, w.name)))
    add(pp.tool_lines(w.t, w.d, pp.vorschub_min, pp.ebene_radial))
    add(pp.fill(pp.spindel_aus))
    add(pp.fill(pp.c_ein))
    starts: List[int] = []
    moves: List[FMove] = []
    for i, a in enumerate(fr.winkel):
        c0 = _norm_winkel(a - math.degrees(phis[0]))
        lines: List[str] = []
        mv: List[FMove] = []

        def zeile(text: str, move: Optional[FMove] = None) -> None:
            lines.append(text)
            mv.append(move or FMove("G0"))  # Zeile ohne Bewegung im Teil (Spindel, Umschalten)

        for ln in pp.fill(pp.c_pos, W=c0):
            zeile(ln, lokal(x_app / 2.0 if i else pp.wechsel_x / 2.0, phis[0], kind="G0"))
        if i == 0:
            for ln in pp.fill(pp.awz_ein, S=w.s):
                zeile(ln)
        z0 = mantel[0][1]
        zeile("G00 Z" + pp.num(z0), FMove("G0", z=z0))
        zeile("G00 X" + pp.num(x_app), lokal(x_app / 2.0, phis[0], kind="G0"))
        for ln in pp.fill(pp.zyl_ebene) + pp.fill(pp.zyl_ein, ZD=2 * rp, ZR=rp):
            zeile(ln)
        zeile("G01 X{} F{}".format(pp.num(2 * (rp - tiefen[0])), pp.num(w.f)), lokal(rp - tiefen[0], phis[0]))
        x_alt = 2 * (rp - tiefen[0])
        for (y, z, _r), phi, d in zip(mantel[1:], phis[1:], tiefen[1:]):
            cw = c0 + math.degrees(phis[0] - phi)  # stetig weiterzählen, nicht auf 0…360 kürzen
            u = cw if pp.zyl_grad else rp * math.radians(cw)
            x = 2 * (rp - d)
            wort_x = "X{} ".format(pp.num(x)) if abs(x - x_alt) > 0.0005 else ""
            x_alt = x
            zeile("G01 {}{}{} Z{}".format(wort_x, achse, pp.num(u), pp.num(z)), lokal(rp - d, phi, z))
        for ln in pp.fill(pp.zyl_aus):
            zeile(ln)
        zeile("G00 X" + pp.num(x_app), lokal(x_app / 2.0, phis[-1], kind="G0"))
        starts.append(len(out.lines))
        add(lines)
        if i == 0:
            moves = mv
    winkel = "/".join(fmt(a) for a in fr.winkel)
    res.fraes_ops.append(FraesOp(nr, w.t, "radial", list(fr.winkel), moves, starts, True,
                                 title + " (C-ACHSE)"))
    if stirn:  # gerade Kante an der Stirnseite: Spindel steht, Y-Achse
        m = _MillOut(pp)
        _edge_path(m, stirn, c, x_app, w.f)
        starts2: List[int] = []
        for a in fr.winkel:
            add(pp.fill(pp.c_pos, W=a))
            add(pp.fill(pp.klemmen))
            starts2.append(len(out.lines))
            add(m.lines)
            add(pp.fill(pp.loesen))
        res.fraes_ops.append(FraesOp(nr, w.t, "radial", list(fr.winkel), list(m.moves), starts2, True, title))
    out.raw("G00 Y" + pp.num(0))
    add(pp.fill(pp.awz_aus))
    add(pp.fill(pp.kuehl_aus))
    add(pp.fill(pp.c_aus))
    add(["G00 X" + pp.num(pp.wechsel_x), "G00 Z" + pp.num(pp.wechsel_z)])
    add(pp.fill(pp.ebene_drehen))
    res.arbeitsgaenge.append("{} entgraten (C-Achse interpoliert) · T{} · C {}°".format(
        FEATURES.get(fr.art, fr.art), w.t, winkel))


# ---------------------------------------------------------------------------
# Nut auf dem Mantel (Zylinder-Interpolation): Bahn in Z und C, z. B. umlaufend oder als Wendel
# ---------------------------------------------------------------------------


def _versatz(pts: List[Tuple[float, float]], o_s: float, o_z: float) -> List[Tuple[float, float, float]]:
    """Parallele zur Bahn in der Abwicklung (s, z): je Punkt (s, z, Normalen-s-Anteil).
    ``o_s``/``o_z``: Abstand in s- bzw. z-Richtung der Normalen (links positiv)."""
    n = len(pts)
    normals = []
    for i in range(n - 1):
        ds, dz = pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]
        ln = math.hypot(ds, dz)
        normals.append((-dz / ln, ds / ln))
    out = []
    for i in range(n):
        if i == 0:
            nv = normals[0]
            k = 1.0
        elif i == n - 1:
            nv = normals[-1]
            k = 1.0
        else:
            a, b = normals[i - 1], normals[i]
            m = (a[0] + b[0], a[1] + b[1])
            ml = math.hypot(*m)
            nv = (m[0] / ml, m[1] / ml) if ml > 1e-9 else b
            k = min(3.0, 1.0 / max(1e-3, nv[0] * b[0] + nv[1] * b[1]))  # Gehrung, begrenzt
        out.append((pts[i][0] + nv[0] * o_s * k, pts[i][1] + nv[1] * o_z * k, nv[0]))
    return out


def _mantel_bahn(fr: Fraesung, rp: float) -> Tuple[List[Tuple[float, float]], bool]:
    """Mittellinie in der Abwicklung (s in mm, z) und ob sie ganz herum läuft (Ringnut)."""
    pts = [(rp * math.radians(c), z) for z, c in fr.bahn]
    c0, c1 = fr.bahn[0][1], fr.bahn[-1][1]
    ring = (abs(fr.bahn[0][0] - fr.bahn[-1][0]) < TOL and abs(c1 - c0) >= 360 - 1e-6
            and abs(((c1 - c0) + 1e-6) % 360.0) < 2e-6)
    return pts, ring


def _mantelnut(teil: Teil, geom: Geometrie, nr: int, fr: Fraesung, w: Werkzeug, res: Ergebnis, out: _Out,
               deburr: bool) -> None:
    """Nut auf der Mantelfläche: Fräser radial, C-Achse interpoliert mit X/Z (TRACYL bzw. G07.1)."""
    pp = out.pp
    label = "Fräsung {} ({})".format(nr, FEATURES[fr.art])
    if not pp.zyl_ein.strip() or not pp.zyl_aus.strip():
        res.errors.append("{}: braucht Zylinder-Interpolation – im Postprozessor (Reiter „Mantel“) „Zylinder "
                          "ein/aus“ eintragen (Siemens TRACYL, Fanuc G07.1).".format(label))
        return
    bahn = [(float(p[0]), float(p[1])) for p in fr.bahn]
    if len(bahn) < 2:
        res.errors.append("{}: Bahn fehlt (mindestens Anfang und Ende in Z und C).".format(label))
        return
    for (za, ca), (zb, cb) in zip(bahn, bahn[1:]):
        if abs(za - zb) < TOL and abs(ca - cb) < 1e-6:
            res.errors.append("{}: zwei Bahnpunkte liegen aufeinander (Z{} C{}).".format(label, fmt(za), fmt(ca)))
            return
    b, t = fr.mass, fr.tiefe
    zs = [z for z, _c in bahn]
    z_lo, z_hi = min(zs) - b / 2, max(zs) + b / 2
    if z_hi > TOL:
        res.errors.append("{}: Die Nut ragt über die Stirnseite (bis Z{}).".format(label, fmt(z_hi)))
        return
    rp = contour_radius(teil, geom, z_lo, z_hi)
    rp_min = _min_radius(teil, geom, z_lo, z_hi)
    if rp - rp_min > 0.01:
        res.errors.append("{}: Die Nut liegt nicht auf einem Durchmesser (Ø{} … Ø{}) – auf dem Mantel nur auf "
                          "einem zylindrischen Stück.".format(label, fmt(2 * rp_min), fmt(2 * rp)))
        return
    if b <= TOL or t <= TOL or t >= rp - 0.5:
        res.errors.append("{}: Breite/Tiefe passen nicht (Ø{}).".format(label, fmt(2 * rp)))
        return
    s_sich = max(0.5, teil.sicherheit)
    x_app = 2 * (contour_radius(teil, geom, z_lo - 1, min(0.0, z_hi + 1)) + s_sich)
    mitte, ring = _mantel_bahn(fr, rp)
    achse = (pp.zyl_achse.strip().upper() or "Y")[0]

    def wort(sp: float, zp: float, x: Optional[float] = None) -> str:
        cdeg = math.degrees(sp / rp)
        u = cdeg if pp.zyl_grad else sp
        return "G01 {}{}{} Z{}".format("X{} ".format(pp.num(x)) if x is not None else "", achse, pp.num(u),
                                       pp.num(zp))

    lines: List[str] = []
    moves: List[FMove] = []

    def zeile(text: str, mv: Optional[FMove] = None) -> None:
        lines.append(text)
        moves.append(mv or FMove("G0"))

    def anfahren(sp: float, zp: float, x_tief: float, f_ein: float) -> None:
        """Außerhalb positionieren, Zylinder ein, eintauchen."""
        c_deg = math.degrees(sp / rp)
        for ln in pp.fill(pp.c_pos, W=_norm_winkel(c_deg)):
            zeile(ln, FMove("G0", c=c_deg))
        zeile("G00 Z" + pp.num(zp), FMove("G0", z=zp))
        zeile("G00 X" + pp.num(x_app), FMove("G0", x=x_app))
        for ln in pp.fill(pp.zyl_ebene) + pp.fill(pp.zyl_ein, ZD=2 * rp, ZR=rp):
            zeile(ln)
        zeile("G01 X{} F{}".format(pp.num(x_tief), pp.num(f_ein)), FMove("G1", x=x_tief))

    def abfahren() -> None:
        for ln in pp.fill(pp.zyl_aus):
            zeile(ln)
        zeile("G00 X" + pp.num(x_app), FMove("G0", x=x_app))

    def strecke(pts: List[Tuple[float, float]], x_von: float, x_bis: float, f: Optional[float]) -> None:
        """Bahnpunkte abfahren (ohne den ersten), X linear von x_von nach x_bis über die Länge.
        Lange Stücke werden in höchstens 90° Drehung geteilt (eindeutige Drehrichtung an jeder Steuerung)."""
        fein = [pts[0]]
        for a_, b_ in zip(pts, pts[1:]):
            n = max(1, int(math.ceil(abs(math.degrees((b_[0] - a_[0]) / rp)) / 90.0 - 1e-9)))
            for k in range(1, n + 1):
                fein.append((a_[0] + (b_[0] - a_[0]) * k / n, a_[1] + (b_[1] - a_[1]) * k / n))
        pts = fein
        total = sum(math.hypot(b_[0] - a_[0], b_[1] - a_[1]) for a_, b_ in zip(pts, pts[1:])) or 1.0
        done = 0.0
        for k, (a_, b_) in enumerate(zip(pts, pts[1:])):
            done += math.hypot(b_[0] - a_[0], b_[1] - a_[1])
            x = x_von + (x_bis - x_von) * done / total if abs(x_bis - x_von) > 1e-9 else None
            text = wort(b_[0], b_[1], x)
            if k == 0 and f is not None:
                text += " F" + pp.num(f)
            zeile(text, FMove("G1", x=x if x is not None else x_bis, z=b_[1], c=math.degrees(b_[0] / rp)))

    if deburr:
        c = fr.fase
        phi_e = math.asin(min(1.0, b / 2 / rp))
        # Kante: in s-Richtung um rp·asin(b/2/rp) (Wand parallel zur Fräserachse), in z um b/2
        links = _versatz(mitte, rp * phi_e, b / 2)
        rechts = _versatz(mitte, -rp * phi_e, -b / 2)

        def tiefe(n_s: float) -> float:
            beta = math.asin(min(1.0, math.sin(phi_e) * abs(n_s)))
            return c * (math.cos(beta) + math.sin(beta))

        if ring:
            schleifen = [links, rechts]
        else:
            def kappe(p: Tuple[float, float], von: Tuple[float, float, float], nach: Tuple[float, float, float],
                      aussen: Tuple[float, float]):
                """Halbkreis um das Bahnende von ``von`` nach ``nach`` – außen herum (Richtung ``aussen``)."""
                a0 = math.atan2(von[1] - p[1], von[0] - p[0])
                a1 = math.atan2(nach[1] - p[1], nach[0] - p[0])
                da = (a1 - a0) % (2 * math.pi)
                if abs(da) < 1e-6:
                    da = math.pi
                am = a0 + da / 2
                if math.cos(am) * aussen[0] + math.sin(am) * aussen[1] < 0:
                    da -= 2 * math.pi  # andersherum, sonst liefe der Bogen in die Nut hinein
                pts_k = []
                for i in range(1, 8):
                    a = a0 + da * i / 8
                    pts_k.append((p[0] + rp * phi_e * math.cos(a), p[1] + b / 2 * math.sin(a), math.cos(a)))
                return pts_k

            def richtung(a_: Tuple[float, float], b_: Tuple[float, float]) -> Tuple[float, float]:
                ln = math.hypot(b_[0] - a_[0], b_[1] - a_[1])
                return (b_[0] - a_[0]) / ln, (b_[1] - a_[1]) / ln

            loop = list(links) + kappe(mitte[-1], links[-1], rechts[-1], richtung(mitte[-2], mitte[-1]))
            loop += list(reversed(rechts))
            loop += kappe(mitte[0], rechts[0], links[0], richtung(mitte[1], mitte[0])) + [links[0]]
            schleifen = [loop]
        tips = [(z, rp - tiefe(n)) for lp in schleifen for _s, z, n in lp]
        if not _cone_clear(teil, geom, tips, w.dm / 2.0, rp):
            res.errors.append(_cone_msg(label, w))
            return
        title = "ENTGRATEN MANTELNUT {}X{}".format(fmt(b), fmt(t))
        for lp in schleifen:
            x0 = 2 * (rp - tiefe(lp[0][2]))
            anfahren(lp[0][0], lp[0][1], x0, w.f)
            for k, (sp, zp, n) in enumerate(lp[1:]):
                x = 2 * (rp - tiefe(n))
                zeile(wort(sp, zp, x), FMove("G1", x=x, z=zp, c=math.degrees(sp / rp)))
            abfahren()
    else:
        if w.dm > b + TOL:
            res.errors.append("{}: Fräser Ø{} ist breiter als die Nut ({}).".format(label, fmt(w.dm), fmt(b)))
            return
        if w.ap <= TOL:
            res.errors.append("{}: Zustellung (Tiefe je Schnitt) von T{} fehlt.".format(label, w.t))
            return
        title = "FRAESEN MANTELNUT {}X{}".format(fmt(b), fmt(t))
        rest = (b - w.dm) / 2
        lanes = [0.0]
        if rest > TOL:
            n_side = int(math.ceil(rest / (0.8 * w.dm) - 1e-9))
            for k in range(1, n_side + 1):
                lanes += [rest * k / n_side, -rest * k / n_side]
        bahnen = {o: [(p[0], p[1]) for p in _versatz(mitte, o, o)] if abs(o) > 1e-9 else list(mitte)
                  for o in lanes}
        tiefen = [2 * r for r in _steps(rp, rp - t, w.ap)]
        pendeln = strategie(fr) == "pendeln"
        f_ein = w.f * 0.5
        x_prev = 2 * rp
        if ring:
            # ganz herum: jede Runde schließt an die vorige an, C zählt weiter
            umfang = mitte[-1][0] - mitte[0][0]
            runde = [0]

            def lauf(lane: List[Tuple[float, float]], x_von: float, x_bis: float, f: Optional[float]) -> None:
                off = umfang * runde[0]
                pts = [(p[0] + off, p[1]) for p in lane]
                strecke(pts, x_von, x_bis, f)
                runde[0] += 1

            start = bahnen[0][0]
            anfahren(start[0], start[1], x_prev if pendeln else tiefen[0], f_ein)
            for x in tiefen:
                lauf(bahnen[0], x_prev if pendeln else x, x, w.f)
                for o in lanes[1:]:
                    off = umfang * runde[0]
                    p0 = bahnen[o][0]
                    zeile(wort(p0[0] + off, p0[1]), FMove("G1", z=p0[1], c=math.degrees((p0[0] + off) / rp)))
                    lauf(bahnen[o], x, x, None)
                    p1 = bahnen[0][0]
                    zeile(wort(p1[0] + umfang * runde[0], p1[1]),
                          FMove("G1", z=p1[1], c=math.degrees((p1[0] + umfang * runde[0]) / rp)))
                x_prev = x
            if pendeln:
                lauf(bahnen[0], x_prev, x_prev, None)  # Rampe der letzten Runde nachfräsen
            abfahren()
        else:
            vor = True
            start = bahnen[0][0]
            anfahren(start[0], start[1], x_prev if pendeln else tiefen[0], f_ein)
            for i, x in enumerate(tiefen):
                for j, o in enumerate(lanes):
                    lane = bahnen[o] if vor else list(reversed(bahnen[o]))
                    if j:  # seitlich auf die nächste Bahn (am selben Ende)
                        zeile(wort(lane[0][0], lane[0][1]),
                              FMove("G1", z=lane[0][1], c=math.degrees(lane[0][0] / rp)))
                    elif not pendeln and i:
                        zeile("G01 X{} F{}".format(pp.num(x), pp.num(f_ein)), FMove("G1", x=x))
                    strecke(lane, x_prev if (pendeln and j == 0) else x, x, w.f)
                    vor = not vor
                if len(lanes) > 1:  # zurück auf die Mittelbahn
                    lane = bahnen[0] if vor else list(reversed(bahnen[0]))
                    zeile(wort(lane[0][0], lane[0][1]), FMove("G1", z=lane[0][1], c=math.degrees(lane[0][0] / rp)))
                x_prev = x
            if pendeln:
                lane = bahnen[0] if vor else list(reversed(bahnen[0]))
                strecke(lane, x_prev, x_prev, None)  # Rampe der letzten Bahn nachfräsen
            abfahren()

    add = out.lines.extend
    out.raw("")
    out.raw(pp.comment("{} {}".format(title, w.name)))
    add(pp.tool_lines(w.t, w.d, pp.vorschub_min, pp.ebene_radial))
    add(pp.fill(pp.spindel_aus))
    add(pp.fill(pp.c_ein))
    # angetriebenes Werkzeug nach dem ersten Positionieren einschalten
    k_awz = next((i for i, ln in enumerate(lines) if ln.startswith("G00 Z")), 0)
    awz = pp.fill(pp.awz_ein, S=w.s)
    lines[k_awz:k_awz] = awz
    moves[k_awz:k_awz] = [FMove("G0") for _ in awz]
    start = len(out.lines)
    add(lines)
    add(pp.fill(pp.awz_aus))
    add(pp.fill(pp.kuehl_aus))
    add(pp.fill(pp.c_aus))
    add(["G00 X" + pp.num(pp.wechsel_x), "G00 Z" + pp.num(pp.wechsel_z)])
    add(pp.fill(pp.ebene_drehen))
    c_start = fr.bahn[0][1]
    res.fraes_ops.append(FraesOp(nr, w.t, "mantel", [c_start], moves, [start], deburr, title))
    res.arbeitsgaenge.append("{}{} · T{} · {}".format(FEATURES[fr.art], " entgraten" if deburr else "", w.t,
                                                     "umlaufend" if ring else "Bahn"))


def _milling(teil: Teil, geom: Geometrie, res: Ergebnis, out: _Out) -> None:
    pp = out.pp
    ops: List[Tuple[int, Fraesung, bool]] = []
    for nr, fr in enumerate(teil.fraesungen, start=1):
        ops.append((nr, fr, False))
    for nr, fr in enumerate(teil.fraesungen, start=1):
        if fr.entgraten:
            ops.append((nr, fr, True))
    for nr, fr, deburr in ops:
        if not fr.winkel:
            res.errors.append("Fräsung {}: Winkel fehlt.".format(nr))
            continue
        if deburr:
            if fr.fase <= TOL:
                res.errors.append("Fräsung {}: Fasengröße zum Entgraten fehlt.".format(nr))
                continue
            w = _check_tool(res, nr, fr, teil, fr.wz_entgraten, ("fase",), "Entgratwerkzeug")
        else:
            arten = ("bohrer", "fraeser") if fr.art.startswith("bohrung") else ("fraeser",)
            w = _check_tool(res, nr, fr, teil, fr.wz, arten, "Werkzeug")
        if w is None:
            continue
        if fr.art == "mantelnut":
            _mantelnut(teil, geom, nr, fr, w, res, out, deburr)
            continue
        if deburr and fr.art in ("flaeche", "nut") and pp.zyl_entgraten and pp.zyl_ein.strip():
            _zyl_entgraten(teil, geom, nr, fr, w, res, out)
            continue
        if w.ap <= TOL and not deburr and fr.art in ("flaeche", "nut"):
            res.errors.append("Fräsung {}: Zustellung (Tiefe je Schnitt) von T{} fehlt.".format(nr, w.t))
            continue
        body = _feature_body(teil, geom, nr, fr, w, res, deburr, pp)
        if body is None:
            continue
        title, lage, lines, axis, body_moves = body
        winkel = "/".join(fmt(a) for a in fr.winkel)
        add = out.lines.extend
        out.raw("")
        # Kommentar direkt über dem Werkzeug = Bearbeitung auf Einricht-/Korrekturblatt
        out.raw(pp.comment("{} {}".format(title, w.name)))
        add(pp.tool_lines(w.t, w.d, pp.vorschub_min, pp.ebene_radial if lage == "radial" else pp.ebene_axial))
        add(pp.fill(pp.spindel_aus))
        add(pp.fill(pp.c_ein))
        starts: List[int] = []
        for i, a in enumerate(fr.winkel):
            if i:
                add(pp.fill(pp.loesen))
            add(pp.fill(pp.c_pos, W=a))
            add(pp.fill(pp.klemmen))
            if i == 0:
                add(pp.fill(pp.awz_ein, S=w.s))
            starts.append(len(out.lines))
            add(lines)
        res.fraes_ops.append(FraesOp(nr, w.t, lage, list(fr.winkel), list(body_moves), starts, deburr, title))
        add(pp.fill(pp.loesen))
        out.raw("G00 Y" + pp.num(0))
        add(pp.fill(pp.awz_aus))
        add(pp.fill(pp.kuehl_aus))
        add(pp.fill(pp.c_aus))
        x, z = "G00 X" + pp.num(pp.wechsel_x), "G00 Z" + pp.num(pp.wechsel_z)
        add([x, z] if axis == "x" else [z, x])  # radial erst X weg, axial erst Z weg
        add(pp.fill(pp.ebene_drehen))
        res.arbeitsgaenge.append("{}{} · T{} · C {}°".format(
            FEATURES.get(fr.art, fr.art), " entgraten" if deburr else "", w.t, winkel))


_Z_WORT = re.compile(r"(?<![A-Z_])Z(-?\d+(?:\.\d*)?)")


def _z_verschieben(line: str, dz: float, pp: Postprozessor) -> str:
    """Z-Wörter einer Programmzeile um dz verschieben (Nullpunkt nicht an der Stirnseite). Kommentare
    bleiben; der Werkzeugwechselpunkt wandert mit (gleicher Abstand zur Stirnseite wie eingestellt)."""
    s = line.strip()
    if not s or s.startswith(";") or s.startswith("("):
        return line
    return _Z_WORT.sub(lambda m: "Z" + pp.num(float(m.group(1)) + dz), line)


def generate(teil: Teil, rahmen: bool = False, pp: Optional[Postprozessor] = None,
             maschine: str = "") -> Ergebnis:
    """Ganzes Programm erzeugen.

    ``rahmen``: mit Programmanfang/-ende aus dem Postprozessor (z. B. %_N_…_MPF … M30).
    ``pp``: Postprozessor der Maschine (ohne Angabe: Sinumerik, angetriebenes Werkzeug S3/M3=).
    """
    pp = pp or Postprozessor()
    res = Ergebnis()
    geom = build_geometry(teil)
    res.errors.extend(geom.errors)
    res.warnings.extend(geom.warnings)
    if res.errors:
        return res
    if teil.rohteil_l > TOL:
        stock_end = max(0.0, teil.planaufmass) - teil.rohteil_l
        z_last = min(p.z for p in teil.punkte)
        if z_last < stock_end - TOL:
            res.errors.append("Die Kontur ist {} lang, mit Planaufmaß {} – das Rohteil nur {}.".format(
                fmt(-z_last), fmt(-z_last + max(0.0, teil.planaufmass)), fmt(teil.rohteil_l)))
        for nr, fr in enumerate(teil.fraesungen, start=1):
            if fr.art in ("flaeche", "nut"):
                lo = min(fr.z1, fr.z2)
            elif fr.art == "mantelnut":
                lo = min([p[0] for p in fr.bahn] or [0.0]) - fr.mass / 2
            elif fr.art == "bohrung_radial":
                lo = fr.z1 - fr.mass / 2
            else:
                lo = fr.z1 - fr.tiefe
            if lo < stock_end - TOL:
                res.errors.append("Fräsung {}: reicht bis Z{}, hinter das Rohteil (Ende Z{}).".format(
                    nr, fmt(lo), fmt(stock_end)))
        if res.errors:
            return res
    if not geom.first and not teil.fraesungen and not teil.innen and not _bohren_aktiv(teil):
        res.errors.append("Es ist nichts zu bearbeiten: alle Elemente stehen auf „Nicht drehen“ und es gibt "
                          "keine Fräsung.")
        return res
    pp_err, pp_warn = pp.pruefen(mit_fraesen=bool(teil.fraesungen))
    res.errors.extend(pp_err)
    res.warnings.extend(pp_warn)
    if pp.wechsel_x / 2.0 < teil.rohteil_d / 2.0 + 1:
        res.errors.append("Werkzeugwechselpunkt X{} (Postprozessor) liegt nicht außerhalb des Rohteils.".format(
            fmt(pp.wechsel_x)))
    if pp.wechsel_z < teil.planaufmass + 5:
        res.errors.append("Werkzeugwechselpunkt Z{} (Postprozessor) liegt zu nah an der Stirnseite.".format(
            fmt(pp.wechsel_z)))
    if res.errors:
        return res
    gin = build_innen(teil)
    res.errors.extend(gin.errors)
    if not res.errors:
        _innen_pruefen(teil, geom, gin, res)
    if res.errors:
        return res
    out = _Out(pp)
    out.start = (pp.wechsel_z, pp.wechsel_x / 2.0)

    def innen_vor() -> None:
        if _bohren_aktiv(teil):
            _mitte_bohren(teil, res, out)
        if gin.elems:
            _innen_schruppen(teil, gin, res, out)

    def innen_fertig() -> None:
        if gin.elems:
            _innen_schlichten(teil, gin, res, out)

    _turning(teil, geom, res, out, innen_vor, innen_fertig)
    _milling(teil, geom, res, out)
    if res.errors:
        return res
    name = nc_name(teil.name)
    head: List[str] = []
    if rahmen:
        head += pp.fill(pp.kopf, NAME=name, NR=program_number(teil.name))
    head.append(pp.comment("KONTUR-ASSISTENT TEIL {}".format(name)))
    if maschine:
        head.append(pp.comment("MASCHINE {}".format(maschine)))
    roh = "D{} L{}".format(fmt(teil.rohteil_d), fmt(teil.rohteil_l)) if teil.rohteil_l > TOL else \
        "D{}".format(fmt(teil.rohteil_d))
    if abs(teil.z_null) > TOL:
        null = "NULLPUNKT MITTE, STIRNSEITE BEI Z{}".format(fmt(teil.z_null))
    else:
        null = "NULLPUNKT MITTE STIRNSEITE Z0"
    head += [pp.comment("ROHTEIL {} {}".format(roh, null)),
             pp.comment("VOR DEM ERSTEN LAUF SIMULIEREN UND PRUEFEN")]
    if rahmen:
        head += pp.fill(pp.start)
    body = list(out.lines)
    if abs(teil.z_null) > TOL:
        body = [_z_verschieben(ln, teil.z_null, pp) for ln in body]
    for mv in out.moves:
        mv.line += len(head)
    for op in res.fraes_ops:
        op.zeilen = [z + len(head) for z in op.zeilen]
    tail = [""] + pp.fill(pp.ende) if rahmen else []
    res.code = "\n".join(head + body + tail) + "\n"
    res.moves = out.moves
    return res
