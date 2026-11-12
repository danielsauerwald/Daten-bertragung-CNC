"""Werkzeugkatalog: Wendeschneidplatten, Außenhalter, Bohrstangen, Bohrer und Fräser.

Die Bezeichnungen folgen den ISO-Normen und sind herstellerübergreifend gleich:

* Platten nach ISO 1832, z. B. ``DCMT 11T304``: Form D (Raute 55°), Freiwinkel C (7°),
  Toleranz M, Typ T, Größe 11, Dicke T3, Eckenradius 04 = 0,4 mm.
* Außenhalter nach ISO 5608, z. B. ``PDJNR 2020K15``: Klemmung P (Kniehebel), Plattenform D,
  Bauform J (Einstellwinkel 93°), Freiwinkel N, rechts, Schaft 20×20, Länge K = 125 mm, Plattengröße 15.
* Bohrstangen nach ISO 6261, z. B. ``A16R-SCLCR09``: Stahlschaft mit Kühlung, Ø16, Länge R = 200 mm,
  Klemmung S, Plattenform C, Bauform L (95°), Freiwinkel C, rechts, Plattengröße 09.

Maße wie kleinste Bohrung (Dmin) und Abstand Achse–Schneidenecke (f1) sind Richtwerte aus
Herstellerkatalogen (Sandvik, Walter, Pramet, Kennametal …); je nach Hersteller weichen sie um
1–2 mm ab. Für die Simulation reicht das – vor dem Kauf bitte das Datenblatt prüfen.

Geometrie (für die Simulation) in der Zeichenebene (z, r): z nach rechts, r (Radius) nach oben.
Ein rechter Außenhalter dreht zum Futter hin (−Z); seine Schneidenecke zeigt nach links unten.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

Point = Tuple[float, float]

# --------------------------------------------------------------------------
# Wendeschneidplatten (ISO 1832)
# --------------------------------------------------------------------------

# Plattenform: (Name, Eckenwinkel der Schneidecke, Eckenwinkel ringsum für den Umriss)
FORMEN: Dict[str, Tuple[str, float, Tuple[float, ...]]] = {
    "C": ("Raute 80°", 80.0, (80.0, 100.0, 80.0, 100.0)),
    "D": ("Raute 55°", 55.0, (55.0, 125.0, 55.0, 125.0)),
    "V": ("Raute 35°", 35.0, (35.0, 145.0, 35.0, 145.0)),
    "W": ("Trigon 80°", 80.0, (80.0, 160.0, 80.0, 160.0, 80.0, 160.0)),
    "T": ("Dreieck 60°", 60.0, (60.0, 60.0, 60.0)),
    "S": ("Quadrat 90°", 90.0, (90.0, 90.0, 90.0, 90.0)),
    "R": ("Rund", 180.0, ()),
}
FREIWINKEL = {"N": 0, "B": 5, "C": 7, "P": 11, "E": 20, "D": 15, "A": 3, "F": 25, "G": 30}

# Größenkennzahl -> Inkreis-Ø (mm) je Form
_IC: Dict[str, Dict[str, float]] = {
    "C": {"06": 6.35, "09": 9.525, "12": 12.7, "16": 15.875, "19": 19.05},
    "D": {"07": 6.35, "11": 9.525, "15": 12.7, "19": 15.875},
    "V": {"11": 6.35, "16": 9.525, "22": 12.7},
    "W": {"04": 6.35, "06": 9.525, "08": 12.7},
    "T": {"11": 6.35, "16": 9.525, "22": 12.7, "27": 15.875},
    "S": {"06": 6.35, "09": 9.525, "12": 12.7, "15": 15.875, "19": 19.05},
}

_PLATTE_RE = re.compile(r"^\s*([CDVWTSR])([A-Z])([A-Z])([A-Z])\s*(\d{2})\s*([0-9T]\d)\s*(\d{2}|M0|00)\s*(?:-?\s*(\S+))?\s*$",
                        re.IGNORECASE)


@dataclass(frozen=True)
class Platte:
    code: str  # z. B. "DCMT 11T304"
    form: str  # C, D, V, W, T, S, R
    freiwinkel: str  # N, C, B, P …
    groesse: str  # z. B. "11"
    ic: float  # Inkreis-Ø in mm (bei runden Platten der Durchmesser)
    radius: float  # Eckenradius in mm (rund: halber Durchmesser)
    sorte: str = ""  # Spanbrecher/Einsatz, z. B. "M"

    @property
    def winkel(self) -> float:
        return FORMEN[self.form][1]

    @property
    def schneidenlaenge(self) -> float:
        """Kantenlänge wie im Katalog (z. B. CNMG 12 -> 12,9 mm)."""
        if self.form == "R":
            return self.ic
        if self.form == "T":
            return self.ic * math.sqrt(3)
        if self.form == "W":
            r = self.ic / 2
            return r / math.tan(math.radians(40)) + r / math.tan(math.radians(80))
        return self.ic / math.sin(math.radians(self.winkel))

    @property
    def negativ(self) -> bool:
        return self.freiwinkel == "N"

    @property
    def beschreibung(self) -> str:
        art = "negativ" if self.negativ else "positiv {}°".format(FREIWINKEL.get(self.freiwinkel, "?"))
        if self.form == "R":
            return "Rund Ø{:g}, {}".format(self.ic, art)
        return "{}, {}, r{:g}".format(FORMEN[self.form][0], art, self.radius)


def parse_platte(code: str) -> Optional[Platte]:
    """ISO-Kurzzeichen lesen: „VCMT 160404“, „CNMG120408-M“, „DCMT 11T304“, „RCMT 1204M0“."""
    m = _PLATTE_RE.match(str(code or ""))
    if not m:
        return None
    form, frei, _tol, typ, size, dicke, rad, sorte = m.groups()
    form, frei = form.upper(), frei.upper()
    if form == "R":
        ic = float(int(size))
        radius = ic / 2
    else:
        ic = _IC.get(form, {}).get(size, 0.0)
        if not ic:
            return None
        radius = 0.0 if rad.upper() in ("M0", "00") else int(rad) / 10.0
    name = "{}{}{}{} {}{}{}".format(form, frei, _tol.upper(), typ.upper(), size, dicke.upper(), rad.upper())
    if sorte:
        name += "-" + sorte.upper()
    return Platte(name, form, frei, size, ic, radius, (sorte or "").upper())


# Auswahl gängiger Platten (Spanbrecher: F = Schlichten, M = Mittel, R = Schruppen, AL = Aluminium)
PLATTEN: List[str] = [
    "CNMG 090304-F", "CNMG 090308-M", "CNMG 120404-F", "CNMG 120408-M", "CNMG 120412-R", "CNMG 160608-R",
    "CCMT 060202-F", "CCMT 060204-F", "CCMT 09T304-M", "CCMT 09T308-M", "CCMT 120404-M", "CCMT 120408-M",
    "CCGT 09T304-AL",
    "DNMG 110404-F", "DNMG 110408-M", "DNMG 150404-F", "DNMG 150408-M", "DNMG 150412-R",
    "DNMG 150604-F", "DNMG 150608-M", "DNMG 150612-R",
    "DCMT 070202-F", "DCMT 070204-M", "DCMT 11T302-F", "DCMT 11T304-F", "DCMT 11T308-M", "DCGT 11T304-AL",
    "VNMG 160404-F", "VNMG 160408-M", "VNMG 160412-R",
    "VCMT 110302-F", "VCMT 110304-F", "VCMT 160404-F", "VCMT 160408-M", "VCGT 160404-AL",
    "VBMT 110304-F", "VBMT 160404-F", "VBMT 160408-M",
    "WNMG 060404-F", "WNMG 060408-M", "WNMG 080404-F", "WNMG 080408-M", "WNMG 080412-R",
    "TNMG 160404-F", "TNMG 160408-M", "TCMT 110204-F", "TCMT 16T304-M",
    "SNMG 120408-M", "SCMT 09T304-M", "SCMT 120408-M",
    "RCMT 0803M0", "RCMT 1204M0",
]

# --------------------------------------------------------------------------
# Halter und Bohrstangen
# --------------------------------------------------------------------------

LAENGEN = {"F": 80, "H": 100, "K": 125, "M": 150, "P": 170, "Q": 180, "R": 200, "S": 250, "T": 300,
           "U": 350, "V": 400, "W": 450, "Y": 500}
# Bauform (3. Stelle) -> Einstellwinkel in Grad; außen und innen
BAUFORMEN = {"A": 90, "B": 75, "D": 45, "E": 60, "F": 91, "G": 91, "H": 107.5, "J": 93, "K": 75, "L": 95,
             "N": 62.5, "Q": 107.5, "R": 75, "S": 45, "T": 60, "U": 93, "V": 72.5, "X": 117.5, "Y": 85}


@dataclass(frozen=True)
class Halter:
    code: str
    art: str  # "aussen" | "innen"
    kappa: float  # Einstellwinkel κr in Grad
    hand: str  # "R" | "L" | "N"
    form: str  # Plattenform
    freiwinkel: str  # Freiwinkel der passenden Platte
    groesse: str  # Plattengröße
    b: float  # Außenhalter: Schaftbreite; Bohrstange: Schaft-Ø
    laenge: float
    dmin: float = 0.0  # Bohrstange: kleinste Bohrung
    f1: float = 0.0  # Außen: Schaftrücken bis Spitze (Z); innen: Stangenachse bis Spitze (X)

    @property
    def schneidenlage(self) -> int:
        """Lage der Schneide nach DIN/Sinumerik (3 = außen rechts, 2 = innen rechts, 8 = neutral …)."""
        if self.art == "innen":
            return 2 if self.hand != "L" else 1
        if self.hand == "N" or self.form == "R":
            return 8
        return 3 if self.hand == "R" else 4

    @property
    def beschreibung(self) -> str:
        if self.art == "innen":
            return "Bohrstange Ø{:g}, ab Bohrung Ø{:g}, κ {:g}°".format(self.b, self.dmin, self.kappa)
        return "Außenhalter {:g}°, Schaft {:g} mm".format(self.kappa, self.b)


def _aussen(code: str, kappa: float, f1: float) -> Halter:
    m = re.match(r"^([CDMPS])([CDVWTSR])([A-Z])([A-Z])([RLN])\s*(\d{2})(\d{2})([A-Z])(\d{2})$", code.replace(" ", ""))
    assert m, code
    _klemm, form, _bau, frei, hand, h, b, laenge, size = m.groups()
    return Halter(code, "aussen", kappa, hand, form, frei, size, float(b), LAENGEN[laenge], 0.0, f1)


def _innen(code: str, kappa: float, dmin: float, f1: float) -> Halter:
    m = re.match(r"^[ASCEH](\d{2})([A-Z])-([CDMPS])([CDVWTSR])([A-Z])([A-Z])([RL])(\d{2})$", code.replace(" ", ""))
    assert m, code
    d, laenge, _klemm, form, _bau, frei, hand, size = m.groups()
    return Halter(code, "innen", kappa, hand, form, frei, size, float(d), LAENGEN[laenge], dmin, f1)


HALTER: List[Halter] = [
    # Außenhalter (ISO 5608): Kniehebel P für negative, Schraube S für positive Platten
    _aussen("PCLNR 2020K09", 95, 25), _aussen("PCLNR 2020K12", 95, 25), _aussen("PCLNR 2525M12", 95, 32),
    _aussen("PCLNR 3232P16", 95, 40), _aussen("PCLNL 2020K12", 95, 25),
    _aussen("SCLCR 1616H06", 95, 20), _aussen("SCLCR 2020K09", 95, 25), _aussen("SCLCR 2525M12", 95, 32),
    _aussen("PDJNR 2020K11", 93, 25), _aussen("PDJNR 2020K15", 93, 25), _aussen("PDJNR 2525M15", 93, 32),
    _aussen("PDJNL 2525M15", 93, 32),
    _aussen("SDJCR 1616H07", 93, 20), _aussen("SDJCR 2020K11", 93, 25), _aussen("SDJCR 2525M11", 93, 32),
    _aussen("PVJNR 2020K16", 93, 25), _aussen("PVJNR 2525M16", 93, 32),
    _aussen("SVJCR 2020K11", 93, 25), _aussen("SVJCR 2020K16", 93, 25), _aussen("SVJBR 2525M16", 93, 32),
    _aussen("SVVBN 2020K16", 72.5, 10), _aussen("SVHBR 2020K16", 107.5, 25),
    _aussen("PWLNR 2020K06", 95, 25), _aussen("PWLNR 2020K08", 95, 25), _aussen("PWLNR 2525M08", 95, 32),
    _aussen("PDNNR 2525M15", 63, 32), _aussen("PDNNN 2525M15", 62.5, 12.5),
    _aussen("PTGNR 2020K16", 91, 25), _aussen("STGCR 2020K16", 91, 25),
    _aussen("PSBNR 2525M12", 75, 32), _aussen("PSSNR 2525M12", 45, 32),
    _aussen("SRDCN 2020K08", 0, 10),
    # Bohrstangen (ISO 6261): Ø, kleinste Bohrung Dmin, f1 = Achse bis Schneidenecke
    _innen("A08H-SCLCR06", 95, 10, 6), _innen("A10K-SCLCR06", 95, 13, 7), _innen("A12M-SCLCR06", 95, 16, 9),
    _innen("A16R-SCLCR09", 95, 20, 11), _innen("A20S-SCLCR09", 95, 25, 13), _innen("A25T-SCLCR12", 95, 32, 17),
    _innen("A10K-SDUCR07", 93, 13, 7), _innen("A12M-SDUCR07", 93, 16, 9), _innen("A16R-SDUCR07", 93, 20, 11),
    _innen("A20S-SDUCR11", 93, 25, 13), _innen("A25T-SDUCR11", 93, 32, 17),
    _innen("A16R-SVUCR11", 93, 22, 11), _innen("A20S-SVUCR11", 93, 25, 13), _innen("A25T-SVUBR16", 93, 32, 17),
    _innen("S20S-PCLNR09", 95, 25, 13), _innen("S25T-PCLNR12", 95, 32, 17), _innen("S32U-PCLNR12", 95, 40, 22),
    _innen("S40T-PCLNR12", 95, 50, 27),
    _innen("S25T-PDUNR11", 93, 32, 17), _innen("S32U-PDUNR15", 93, 40, 22),
    _innen("S25T-PWLNR06", 95, 32, 17), _innen("S32U-PWLNR08", 95, 40, 22),
    _innen("S25T-PTFNR16", 91, 32, 17),
]
_HALTER_BY_CODE = {h.code: h for h in HALTER}


def halter(code: str) -> Optional[Halter]:
    return _HALTER_BY_CODE.get(str(code or "").strip())


def passende_platten(h: Halter) -> List[str]:
    """Platten aus dem Katalog, die in den Halter passen (Form, Freiwinkel, Größe)."""
    out = []
    for code in PLATTEN:
        p = parse_platte(code)
        if p and p.form == h.form and p.groesse == h.groesse and (p.freiwinkel == h.freiwinkel or h.form == "R"):
            out.append(code)
    return out


def platte_passt(h: Halter, p: Platte) -> bool:
    return p.form == h.form and p.groesse == h.groesse and (p.freiwinkel == h.freiwinkel or h.form == "R")


# --------------------------------------------------------------------------
# Bohrer, Fräser, Fasenfräser
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Rundwerkzeug:
    code: str
    art: str  # "bohrer" | "fraeser" | "fase"
    dm: float
    zaehne: int = 2
    schneidenlaenge: float = 0.0
    spitzenwinkel: float = 118.0  # Bohrer/Fasenfräser (90°)

    @property
    def beschreibung(self) -> str:
        if self.art == "bohrer":
            return "Bohrer Ø{:g}, {:g}°".format(self.dm, self.spitzenwinkel)
        if self.art == "fase":
            return "Fasenfräser Ø{:g}, {:g}°".format(self.dm, self.spitzenwinkel)
        return "Schaftfräser Ø{:g}, Z{}".format(self.dm, self.zaehne)


RUNDWERKZEUGE: List[Rundwerkzeug] = (
    [Rundwerkzeug("VHM-BOHRER D{:g}".format(d), "bohrer", d, 2, d * 5, 140)
     for d in (3, 3.3, 4.2, 5, 6, 6.8, 8, 8.5, 10, 10.2, 12, 14)]
    + [Rundwerkzeug("HSS-BOHRER D{:g}".format(d), "bohrer", d, 2, d * 8, 118) for d in (16, 18, 20, 22, 25)]
    + [Rundwerkzeug("WSP-BOHRER D{:g}".format(d), "bohrer", d, 2, d * 3, 180) for d in (16, 20, 25, 32)]
    + [Rundwerkzeug("NC-ANBOHRER D{:g} 90".format(d), "fase", d, 2, d / 2, 90) for d in (6, 10, 12)]
    + [Rundwerkzeug("VHM-FRAESER D{:g} Z{}".format(d, z), "fraeser", d, z, d * 2)
       for d, z in ((3, 3), (4, 3), (5, 3), (6, 3), (6, 4), (8, 3), (8, 4), (10, 4), (12, 4), (16, 4), (20, 4))]
    + [Rundwerkzeug("FASENFRAESER D{:g} 90".format(d), "fase", d, 3, d / 2, 90) for d in (6, 10, 16)]
)
_RUND_BY_CODE = {w.code: w for w in RUNDWERKZEUGE}


def rundwerkzeug(code: str) -> Optional[Rundwerkzeug]:
    return _RUND_BY_CODE.get(str(code or "").strip())


# --------------------------------------------------------------------------
# Geometrie für die Simulation
# --------------------------------------------------------------------------


def _tangential(winkel: Sequence[float], r: float) -> List[Point]:
    """Polygon mit Inkreis r und den gegebenen Innenwinkeln (Grad), Ecke 0 auf der −y-Achse."""
    pts = []
    theta = -math.pi / 2
    for i, a in enumerate(winkel):
        if i:
            prev = math.radians(winkel[i - 1])
            theta += (math.pi - prev) / 2 + (math.pi - math.radians(a)) / 2
        dist = r / math.sin(math.radians(a) / 2)
        pts.append((dist * math.cos(theta), dist * math.sin(theta)))
    return pts


def _round_corners(poly: List[Point], rho: float, steps: int = 6) -> Tuple[List[Point], Point]:
    """Ecken mit Radius rho abrunden. Ergebnis: Umriss und Mittelpunkt des Radius an Ecke 0."""
    if rho <= 1e-9:
        return list(poly), poly[0]
    out: List[Point] = []
    center0 = poly[0]
    n = len(poly)
    for i in range(n):
        p, a, b = poly[i], poly[i - 1], poly[(i + 1) % n]
        u = _norm((a[0] - p[0], a[1] - p[1]))
        v = _norm((b[0] - p[0], b[1] - p[1]))
        half = math.acos(max(-1.0, min(1.0, u[0] * v[0] + u[1] * v[1]))) / 2
        t = rho / math.tan(half)
        bis = _norm((u[0] + v[0], u[1] + v[1]))
        c = (p[0] + bis[0] * rho / math.sin(half), p[1] + bis[1] * rho / math.sin(half))
        s, e = (p[0] + u[0] * t, p[1] + u[1] * t), (p[0] + v[0] * t, p[1] + v[1] * t)
        a0 = math.atan2(s[1] - c[1], s[0] - c[0])
        a1 = math.atan2(e[1] - c[1], e[0] - c[0])
        da = (a1 - a0 + math.pi) % (2 * math.pi) - math.pi
        for k in range(steps + 1):
            ang = a0 + da * k / steps
            out.append((c[0] + rho * math.cos(ang), c[1] + rho * math.sin(ang)))
        if i == 0:
            center0 = c
    return out, center0


def _norm(v: Point) -> Point:
    ln = math.hypot(v[0], v[1]) or 1.0
    return v[0] / ln, v[1] / ln


def schneiden_richtungen(h: Halter, p: Platte) -> Tuple[float, float]:
    """Richtungen (Grad, ab +Z gegen den Uhrzeiger) der beiden Schneidkanten ab der Schneidenecke.

    Rechter Außenhalter mit κr = 95° und C-Platte (80°): 85° (Hauptschneide, steht 5° vom Planabsatz
    frei) und 5° (Nebenschneide, 5° Freiraum zum Zylinder). Die Platte liegt zwischen beiden Kanten.
    """
    eps = 180.0 if p.form == "R" else p.winkel
    if h.hand == "N" or p.form == "R":
        a, b = 90.0 + eps / 2, 90.0 - eps / 2  # neutral: Ecke zeigt senkrecht zur Achse
    else:
        a = 180.0 - h.kappa
        b = a - eps
    if h.hand == "L":  # gespiegelt: dreht zur Gegenseite (+Z)
        a, b = 180.0 - b, 180.0 - a
    if h.art == "innen":  # Bohrstange: Ecke zeigt nach außen (+X)
        a, b = -b, -a
    return a, b


def spitze_versatz(h: Halter, p: Platte) -> Point:
    """Vektor vom Schneidenradius-Mittelpunkt S zur theoretischen Spitze P (Schneidenlage 1–4, 8)."""
    rho = p.radius
    return {1: (rho, rho), 2: (-rho, rho), 3: (-rho, -rho), 4: (rho, -rho), 8: (0.0, -rho)}.get(
        h.schneidenlage, (-rho, -rho))


def platten_umriss(h: Halter, p: Platte, s: Point, steps: int = 6) -> List[Point]:
    """Umriss der Platte (z, r) mit dem Schneidenradius-Mittelpunkt in ``s``."""
    if p.form == "R":
        rr = p.ic / 2
        return [(s[0] + rr * math.cos(2 * math.pi * k / 36), s[1] + rr * math.sin(2 * math.pi * k / 36))
                for k in range(36)]
    poly = _tangential(FORMEN[p.form][2], p.ic / 2)
    rounded, c0 = _round_corners(poly, p.radius, steps)
    a, b = schneiden_richtungen(h, p)
    # Alle Formen sind zur Winkelhalbierenden der Schneidecke symmetrisch: drehen genügt.
    want = math.radians((a + b) / 2)  # von der Ecke in die Platte hinein
    v0 = poly[0]
    rot = want - math.atan2(-v0[1], -v0[0])
    cr, sr = math.cos(rot), math.sin(rot)
    return [(s[0] + (x - c0[0]) * cr - (y - c0[1]) * sr, s[1] + (x - c0[0]) * sr + (y - c0[1]) * cr)
            for x, y in rounded]


def halter_umriss(h: Halter, p: Platte, s: Point) -> List[Point]:
    """Vereinfachter Umriss von Halterkopf und Schaft bzw. Bohrstange (z, r)."""
    off = spitze_versatz(h, p)
    tip = (s[0] + off[0], s[1] + off[1])
    size = max(p.ic, 6.0)
    if h.art == "innen":  # runde Stange, Achse um f1 unter der Schneidenecke
        rb = h.b / 2
        axis = tip[1] - h.f1
        z0 = tip[0] + size * 0.6
        z1 = tip[0] + max(4 * h.b, 60.0)
        return [(z0, axis - rb), (z1, axis - rb), (z1, axis + rb), (z0, axis + rb)]
    sign = -1.0 if h.hand == "L" else 1.0
    back = tip[0] + sign * h.f1  # Schaftrücken
    front = back - sign * h.b
    top = tip[1] + max(60.0, 2.5 * h.b)
    head = tip[1] + size * 0.9
    if h.hand == "N":
        front, back = tip[0] - h.b / 2, tip[0] + h.b / 2
    return [(front, head), (front, top), (back, top), (back, head),
            (tip[0] + sign * size * 0.9, tip[1] + size * 0.35)]
