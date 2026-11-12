"""Simulation der Drehbearbeitung mit echter Plattenform (für Programme aus dem Kontur-Assistenten).

Modell
------
* Schnitt durch die Drehachse, z nach rechts, r (Radius) nach oben. Das Werkstück ist
  rotationssymmetrisch; in jeder Spalte (Breite ``dz``) bleibt Material von r = 0 bis ``m[i]``.
* Die Platte (Umriss mit Eckenradius, Lage aus dem Halter) entfernt in jeder Spalte, die sie
  überstreicht, alles oberhalb ihrer Unterkante. Ihr Bezugspunkt ist der Mittelpunkt S des
  Schneidenradius.
* Ohne Schneidenradiuskorrektur fährt die theoretische Spitze P die programmierte Bahn ab
  (P = S + Versatz je Schneidenlage). Mit G41/G42 fährt S – wie bei der Steuerung – auf der um den
  Eckenradius versetzten Bahn (Anfahren NORM, Ecken außen mit Kreisbogen, innen mit Schnittpunkt).

* Innen (Bohrung): je Spalte zusätzlich der Bohrungsradius ``h[i]`` – Material liegt zwischen
  ``h[i]`` und ``m[i]``. Bohrer und Bohrstange entfernen alles unterhalb ihrer Oberkante. Die
  Bohrstange (bzw. das Bohrfutter) muss im Schnitt auf beiden Seiten der Achse in die Bohrung
  passen: Oberkante ≤ h und Unterkante ≥ −h.

Gemeldet werden: Eilgang ins Material, Platte schneidet in die Fertigkontur, Halter/Bohrstange
stößt an, und am Ende Restmaterial (z. B. Innenecken mit dem Eckenradius der Platte).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .kontur import Ergebnis, Teil, Werkzeug, build_geometry, build_innen, dreh_geometrie, polyline
from . import werkzeugkatalog as wk

Point = Tuple[float, float]
INF = float("inf")
DZ = 0.05
TOL_R = 0.012  # Toleranz für „Kontur verletzt“ / „ins Material“ in mm


@dataclass
class Ereignis:
    art: str  # "eilgang" | "kontur" | "halter" | "rest" | "ueberschnitt" | "radius" | "hinweis"
    text: str
    z: float = 0.0
    r: float = 0.0
    schritt: int = -1
    zeile: int = -1
    t: int = 0

    @property
    def schwer(self) -> bool:
        return self.art in ("eilgang", "kontur", "halter", "rest", "ueberschnitt")


@dataclass
class Schritt:
    """Kurzes Stück Bahn des Schneidenradius-Mittelpunkts S."""

    a: Point
    b: Point
    t: int
    eil: bool
    zeile: int
    move: int


@dataclass
class Teilinfo:
    """Bezeichnung für Werkzeuge ohne Halter/Platte aus dem Katalog (Bohrer)."""

    code: str
    beschreibung: str
    radius: float = 0.0


class Werkzeugform:
    """Platte und Halter eines Werkzeugs, relativ zu S = (0, 0), mit Hüllkurven je Spalte.

    ``innen``: das Werkzeug schneidet nach oben weg (Bohrstange, Bohrer in Drehmitte).
    """

    STEP = DZ / 2

    def __init__(self, t: int, halter, platte, innen: Optional[bool] = None,
                 platte_rel: Optional[List[Point]] = None, halter_rel: Optional[List[Point]] = None,
                 off: Optional[Point] = None) -> None:
        self.t = t
        self.halter = halter
        self.platte = platte
        self.innen = (halter.art == "innen") if innen is None else innen
        self.off = off if off is not None else wk.spitze_versatz(halter, platte)  # S -> P
        self.platte_rel = platte_rel or wk.platten_umriss(halter, platte, (0.0, 0.0), steps=10)
        self.halter_rel = halter_rel or wk.halter_umriss(halter, platte, (0.0, 0.0))
        self.p0, self.plow, self.phigh = _envelope(self.platte_rel, self.STEP)
        self.h0, self.hlow, self.hhigh = _envelope(self.halter_rel, self.STEP)
        self.pmin = _SparseMin(self.plow)
        self.hmin = _SparseMin(self.hlow)
        self.pmax = _SparseMin([-v for v in self.phigh])  # Minimum der negierten Oberkante
        self.hmax = _SparseMin([-v for v in self.hhigh])

    @property
    def radius(self) -> float:
        return self.platte.radius

    def umriss(self, s: Point) -> Tuple[List[Point], List[Point]]:
        """(Platte, Halter) als Polygone bei S."""
        return ([(s[0] + x, s[1] + y) for x, y in self.platte_rel],
                [(s[0] + x, s[1] + y) for x, y in self.halter_rel])


def bohrerform(w: Werkzeug) -> Werkzeugform:
    """Stehender Bohrer in Drehmitte: Spitze = programmierter Punkt, dahinter Bohrfutter."""
    rw = wk.rundwerkzeug(w.halter)
    d = w.dm if w.dm > 0 else (rw.dm if rw is not None else 10.0)
    winkel = rw.spitzenwinkel if rw is not None else 118.0
    rd = d / 2.0
    kegel = rd / math.tan(math.radians(winkel / 2)) if winkel < 179 else 0.0
    lang = max(rw.schneidenlaenge if rw is not None else 0.0, 2 * d) + kegel
    spitze: List[Point] = [(0.0, 0.0), (kegel, rd), (lang, rd), (lang, -rd), (kegel, -rd)] if kegel > 0.01 \
        else [(0.0, rd), (lang, rd), (lang, -rd), (0.0, -rd)]
    rf = rd + 8.0
    futter: List[Point] = [(lang, rf), (lang + 45.0, rf), (lang + 45.0, -rf), (lang, -rf)]
    name = rw.code if rw is not None else (w.name or "BOHRER D{:g}".format(d))
    platte = Teilinfo(name, rw.beschreibung if rw is not None else "Bohrer Ø{:g}".format(d))
    halter = Teilinfo("BOHRFUTTER", "stehend in Drehmitte, Futter Ø{:g}".format(2 * rf))
    return Werkzeugform(w.t, halter, platte, innen=True, platte_rel=spitze, halter_rel=futter, off=(0.0, 0.0))


def _envelope(poly: Sequence[Point], step: float) -> Tuple[float, List[float], List[float]]:
    """Unter-/Oberkante eines Polygons je Spalte (Abstand ``step``) ab x0."""
    x0 = min(x for x, _ in poly)
    x1 = max(x for x, _ in poly)
    n = int(math.ceil((x1 - x0) / step)) + 1
    low = [INF] * n
    high = [-INF] * n
    m = len(poly)
    for k in range(n):
        x = min(x0 + k * step, x1 - 1e-9) if k == n - 1 else x0 + k * step
        lo, hi = INF, -INF
        for j in range(m):
            (ax, ay), (bx, by) = poly[j - 1], poly[j]
            if (ax <= x <= bx) or (bx <= x <= ax):
                if abs(bx - ax) < 1e-12:
                    lo, hi = min(lo, ay, by), max(hi, ay, by)
                else:
                    y = ay + (by - ay) * (x - ax) / (bx - ax)
                    lo, hi = min(lo, y), max(hi, y)
        low[k], high[k] = lo, hi
    return x0, low, high


class _SparseMin:
    """Minimum über beliebige Bereiche in O(1) (für Längsschnitte am Stück)."""

    def __init__(self, values: List[float]) -> None:
        self.t = [list(values)]
        j = 1
        while (1 << j) <= len(values):
            prev = self.t[-1]
            half = 1 << (j - 1)
            self.t.append([min(prev[i], prev[i + half]) for i in range(len(values) - (1 << j) + 1)])
            j += 1

    def query(self, lo: int, hi: int) -> float:
        if hi < lo:
            return INF
        j = (hi - lo + 1).bit_length() - 1
        row = self.t[j]
        return min(row[lo], row[hi - (1 << j) + 1])


# --------------------------------------------------------------------------
# Bahn mit Schneidenradiuskorrektur
# --------------------------------------------------------------------------


def _unit(a: Point, b: Point) -> Optional[Point]:
    dx, dy = b[0] - a[0], b[1] - a[1]
    ln = math.hypot(dx, dy)
    return (dx / ln, dy / ln) if ln > 1e-9 else None


def _cross(a: Point, b: Point) -> float:
    return a[0] * b[1] - a[1] * b[0]


def _intersect(p1: Point, d1: Point, p2: Point, d2: Point) -> Optional[Point]:
    den = _cross(d1, d2)
    if abs(den) < 1e-12:
        return None
    t = _cross((p2[0] - p1[0], p2[1] - p1[1]), d2) / den
    return p1[0] + d1[0] * t, p1[1] + d1[1] * t


def _move_points(mv) -> List[Point]:
    """Programmierte Punkte einer Bewegung (Bögen fein zerlegt), ohne Startpunkt."""
    pts = mv.points()
    if mv.kind in ("G2", "G3") and len(pts) > 2:
        return pts[1:]
    return [(mv.z1, mv.r1)]


def kompensierte_bahn(run, rho: float, rechts: bool) -> List[Tuple[Point, int]]:
    """Bahn von S für eine Folge von Sätzen mit G42 (``rechts``) bzw. G41.

    Ergebnis: Punkte mit Satznummer (Index in ``run``). Erster Punkt: Ende des Anfahrsatzes,
    senkrecht zu dessen Richtung versetzt (NORM).
    """

    def normal(d: Point) -> Point:
        return (d[1], -d[0]) if rechts else (-d[1], d[0])

    act = run[0]
    d0 = _unit((act.z0, act.r0), (act.z1, act.r1)) or (-1.0, 0.0)
    q = (act.z1, act.r1)
    n0 = normal(d0)
    out: List[List] = [[(q[0] + n0[0] * rho, q[1] + n0[1] * rho), 0]]
    prev_d = d0
    prev_start = (out[0][0][0] - d0[0], out[0][0][1] - d0[1])  # gedachter Anfang des letzten Stücks
    cur = q
    for idx, mv in enumerate(run[1:], start=1):
        for p in _move_points(mv):
            d = _unit(cur, p)
            if d is None:
                continue
            n = normal(d)
            a = (cur[0] + n[0] * rho, cur[1] + n[1] * rho)
            b = (p[0] + n[0] * rho, p[1] + n[1] * rho)
            turn = _cross(prev_d, d)
            convex = turn > 1e-9 if rechts else turn < -1e-9
            concave = turn < -1e-9 if rechts else turn > 1e-9
            if convex:  # außen um die Ecke: Kreisbogen um den Konturpunkt
                a0 = math.atan2(out[-1][0][1] - cur[1], out[-1][0][0] - cur[0])
                a1 = math.atan2(a[1] - cur[1], a[0] - cur[0])
                da = (a1 - a0 + math.pi) % (2 * math.pi) - math.pi
                steps = max(1, int(abs(math.degrees(da)) / 10))
                for k in range(1, steps + 1):
                    ang = a0 + da * k / steps
                    out.append([(cur[0] + rho * math.cos(ang), cur[1] + rho * math.sin(ang)), idx])
                prev_start = a
                out.append([b, idx])
            elif concave:  # innen: Schnittpunkt der versetzten Strecken
                while True:
                    x = _intersect(prev_start, prev_d, a, d)
                    if x is None:
                        out.append([b, idx])
                        break
                    # liegt der Schnittpunkt vor dem Anfang des letzten Stücks? -> Stück fällt weg
                    if len(out) >= 2 and ((x[0] - out[-2][0][0]) * prev_d[0] + (x[1] - out[-2][0][1]) * prev_d[1]) < -1e-9:
                        out.pop()
                        if len(out) >= 2:
                            pd = _unit(out[-2][0], out[-1][0])
                            if pd is not None:
                                prev_d, prev_start = pd, out[-2][0]
                                continue
                        out.append([b, idx])
                        break
                    out[-1][0] = x
                    out.append([b, idx])
                    break
                prev_start = a
            else:
                if math.hypot(a[0] - out[-1][0][0], a[1] - out[-1][0][1]) > 1e-9:
                    out.append([a, idx])
                out.append([b, idx])
                prev_start = a
            prev_d = d
            cur = p
    return [(tuple(p), i) for p, i in out]


# --------------------------------------------------------------------------
# Simulation
# --------------------------------------------------------------------------


class DrehSimulation:
    def __init__(self, teil: Teil, res: Ergebnis, dz: float = DZ) -> None:
        self.teil = teil
        self.res = res
        self.dz = dz
        self.R = teil.rohteil_d / 2.0
        self.hinweise: List[Ereignis] = []
        geom = build_geometry(teil)
        self.geom = geom
        self.gin = build_innen(teil)
        pts_z = [p.z for p in teil.punkte] + [p.z for p in teil.innen] or [0.0]
        self.front = max(0.0, teil.planaufmass)
        self.stock_end = self.front - teil.rohteil_l if teil.rohteil_l > 0 else min(pts_z) - 12.0
        self.z_lo = min(self.stock_end, min(pts_z)) - 3.0
        self.z_hi = self.front + 6.0
        self.n = int(math.ceil((self.z_hi - self.z_lo) / dz))
        self.zc = [self.z_lo + (i + 0.5) * dz for i in range(self.n)]
        self.ziel = self._ziel(geom)
        # für „Kontur verletzt“: kleinstes Zielmaß in der Nachbarschaft (Absätze nicht zu streng)
        self.ziel_min = [min(self.ziel[max(0, i - 1):i + 2]) for i in range(self.n)]
        self.ziel_innen = self._ziel_innen()
        self.ziel_innen_max = [max(self.ziel_innen[max(0, i - 1):i + 2]) for i in range(self.n)]
        self.formen: Dict[int, Werkzeugform] = {}
        for mv in res.moves:
            if mv.t not in self.formen:
                w = teil.tool(mv.t)
                if w is not None and w.art == "bohrer" and w.lage == "mitte":
                    self.formen[mv.t] = bohrerform(w)
                    continue
                g = dreh_geometrie(w) if w is not None else None
                if g is None:
                    continue
                h, p, notes = g
                self.formen[mv.t] = Werkzeugform(mv.t, h, p)
                for txt in notes:
                    self.hinweise.append(Ereignis("hinweis", txt, t=mv.t))
        self.schritte: List[Schritt] = self._schritte()
        self.reset()

    # -- Aufbau ---------------------------------------------------------------

    def _ziel(self, geom) -> List[float]:
        """Fertigteil-Radius je Spalte (vor der Stirnseite 0, hinter der Kontur Rohteil/Stange)."""
        pl = polyline(geom.elems, 1.0) if geom.elems else []
        z_last = pl[-1][0] if pl else 0.0
        out = []
        j = 0
        for z in self.zc:
            if z > 0 or not pl:
                out.append(-1.0 if z > 0 else self.R)
                continue
            if z < z_last:
                out.append(self.R if z >= self.stock_end else -1.0)
                continue
            best = -1.0
            for k in range(1, len(pl)):
                (za, ra), (zb, rb) = pl[k - 1], pl[k]
                lo, hi = min(za, zb), max(za, zb)
                if lo - 1e-9 <= z <= hi + 1e-9:
                    if hi - lo < 1e-9:
                        best = max(best, ra, rb)
                    else:
                        best = max(best, ra + (rb - ra) * (z - za) / (zb - za))
            out.append(best if best >= 0 else self.R)
            j += 1
        return out

    def _ziel_innen(self) -> List[float]:
        """Fertiger Bohrungsradius je Spalte (INF vor der Stirnseite; hinter der Innenkontur die Vorbohrung)."""
        t = self.teil
        pl = polyline(self.gin.elems, 1.0) if self.gin.elems else []
        z_last = pl[-1][0] if pl else 0.0
        rd = t.bohr_d / 2.0 if (t.bohr_d > 0 and t.bohr_tiefe > 0) else 0.0
        drill = t.tool(t.wz_bohren)
        rw = wk.rundwerkzeug(drill.halter) if drill is not None else None
        winkel = rw.spitzenwinkel if rw is not None else 118.0
        steig = math.tan(math.radians(winkel / 2)) if winkel < 179 else INF
        out = []
        for z in self.zc:
            if z > 0:
                out.append(INF)
                continue
            if pl and z >= z_last:
                best = 0.0
                for k in range(1, len(pl)):
                    (za, ra), (zb, rb) = pl[k - 1], pl[k]
                    lo, hi = min(za, zb), max(za, zb)
                    if lo - 1e-9 <= z <= hi + 1e-9:
                        best = max(best, ra, rb) if hi - lo < 1e-9 else max(best, ra + (rb - ra) * (z - za) / (zb - za))
                out.append(best)
                continue
            tiefer = -t.bohr_tiefe - z
            out.append(rd if tiefer <= 0 else max(0.0, rd - tiefer * steig))
        return out

    def _schritte(self) -> List[Schritt]:
        moves = self.res.moves
        out: List[Schritt] = []
        cur: Optional[Point] = None
        last_t = None
        i = 0
        while i < len(moves):
            mv = moves[i]
            form = self.formen.get(mv.t)
            if form is None:
                i += 1
                continue
            if mv.t != last_t:
                cur, last_t = None, mv.t
            off = form.off
            if not mv.comp:
                start = cur if cur is not None else (mv.z0 - off[0], mv.r0 - off[1])
                pts = [(z - off[0], r - off[1]) for z, r in _move_points(mv)]
                for p in pts:
                    out.extend(self._chunks(start, p, mv.t, mv.kind == "G0", mv.line, i))
                    start = p
                cur = start
                i += 1
                continue
            j = i
            while j < len(moves) and moves[j].comp == mv.comp and moves[j].t == mv.t:
                j += 1
            run = moves[i:j]
            path = kompensierte_bahn(run, form.radius, mv.comp == "G42")
            start = cur if cur is not None else (mv.z0 - off[0], mv.r0 - off[1])
            for p, k in path:
                out.extend(self._chunks(start, p, mv.t, run[k].kind == "G0", run[k].line, i + k))
                start = p
            cur = start
            i = j
        return out

    @staticmethod
    def _chunks(a: Point, b: Point, t: int, eil: bool, line: int, move: int) -> List[Schritt]:
        ln = math.hypot(b[0] - a[0], b[1] - a[1])
        if ln < 1e-9:
            return []
        n = max(1, int(math.ceil(ln / (6.0 if eil else 1.0))))
        out = []
        for k in range(n):
            p = (a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n)
            q = (a[0] + (b[0] - a[0]) * (k + 1) / n, a[1] + (b[1] - a[1]) * (k + 1) / n)
            out.append(Schritt(p, q, t, eil, line, move))
        return out

    # -- Ablauf ---------------------------------------------------------------

    def reset(self) -> None:
        self.m = [self.R if self.stock_end <= z <= self.front else -1.0 for z in self.zc]
        self.h = [0.0] * self.n  # Bohrungsradius
        self.i = 0
        self.ereignisse: List[Ereignis] = list(self.hinweise)
        self._gemeldet: set = set()
        self.fertig = not self.schritte
        if self.fertig:
            self._rest()

    def weiter(self, anzahl: int = 1) -> int:
        """``anzahl`` Schritte rechnen; Ergebnis: neuer Schrittindex."""
        end = min(len(self.schritte), self.i + max(0, anzahl))
        while self.i < end:
            self._schritt(self.i, self.schritte[self.i])
            self.i += 1
        if self.i >= len(self.schritte) and not self.fertig:
            self.fertig = True
            self._rest()
        return self.i

    def bis_ende(self) -> None:
        self.weiter(len(self.schritte))

    def gehe_zu(self, index: int) -> None:
        """Zu einem Schritt springen (rückwärts: von vorn neu rechnen)."""
        if index < self.i:
            self.reset()
        self.weiter(index - self.i)

    @property
    def position(self) -> Optional[Tuple[Werkzeugform, Point]]:
        """Werkzeug und S nach dem zuletzt gerechneten Schritt."""
        if not self.schritte:
            return None
        st = self.schritte[max(0, min(self.i, len(self.schritte)) - 1)]
        return self.formen[st.t], (st.b if self.i > 0 else st.a)

    @property
    def zeile(self) -> int:
        if not self.schritte or self.i == 0:
            return -1
        return self.schritte[min(self.i, len(self.schritte)) - 1].zeile

    # -- ein Schritt ------------------------------------------------------------

    def _melde(self, art: str, idx: int, st: Schritt, z: float, r: float, text: str, key=None) -> None:
        key = key or (art, st.move)
        if key in self._gemeldet:
            return
        self._gemeldet.add(key)
        self.ereignisse.append(Ereignis(art, text, z, r, idx, st.zeile, st.t))

    def _schritt(self, idx: int, st: Schritt) -> None:
        f = self.formen[st.t]
        quer = self._quer_innen if f.innen else self._quer
        (za, ra), (zb, rb) = st.a, st.b
        if abs(rb - ra) < 1e-9:
            quer(idx, st, f, min(za, zb), max(za, zb), ra)
        elif abs(zb - za) < 1e-9:
            quer(idx, st, f, za, za, min(ra, rb))
            if f.innen:  # innen schneidet die Oberkante – und die Stange braucht unten Platz
                quer(idx, st, f, za, za, max(ra, rb))
        else:
            ln = math.hypot(zb - za, rb - ra)
            n = max(1, int(math.ceil(ln / (0.5 if st.eil else 0.08))))
            for k in range(1, n + 1):
                z = za + (zb - za) * k / n
                r = ra + (rb - ra) * k / n
                quer(idx, st, f, z, z, r)

    def _quer(self, idx: int, st: Schritt, f: Werkzeugform, z_from: float, z_to: float, r: float) -> None:
        """Werkzeug bei Radius r von z_from bis z_to (waagerecht) überstreichen lassen."""
        step = f.STEP
        dz, z_lo = self.dz, self.z_lo
        m, ziel_min = self.m, self.ziel_min
        lo_rel = min(f.p0, f.h0)
        hi_rel = max(f.p0 + len(f.plow) * step, f.h0 + len(f.hlow) * step)
        i0 = max(0, int((z_from + lo_rel - z_lo) / dz))
        i1 = min(self.n, int((z_to + hi_rel - z_lo) / dz) + 2)
        np_, nh = len(f.plow), len(f.hlow)
        for i in range(i0, i1):
            mi = m[i]
            if mi < 0:
                continue  # Spalte leer (vor der Stirnseite, hinter dem Rohteil oder schon ganz abgedreht)
            zc = self.zc[i]
            # Platte: Unterkante über den überstrichenen Bereich
            k_lo = int(math.ceil((zc - z_to - f.p0) / step - 1e-9))
            k_hi = int(math.floor((zc - z_from - f.p0) / step + 1e-9))
            if k_hi < k_lo:
                k_lo = k_hi = int(round((zc - z_from - f.p0) / step))
            k_lo, k_hi = max(0, k_lo), min(np_ - 1, k_hi)
            low = r + f.pmin.query(k_lo, k_hi) if k_lo <= k_hi else INF
            if low < mi:
                if ziel_min[i] >= 0 and low < ziel_min[i] - TOL_R:
                    self._melde("kontur", idx, st, zc, low,
                                "T{}: Platte schneidet in die Fertigkontur (Z{:.2f}, {:.2f} mm zu tief).".format(
                                    st.t, zc, ziel_min[i] - low))
                if st.eil and mi - low > TOL_R:
                    self._melde("eilgang", idx, st, zc, low,
                                "T{}: Eilgang ins Material bei Z{:.2f}.".format(st.t, zc))
                if low <= self.h[i]:  # bis in die Bohrung: Spalte ist leer
                    m[i] = -1.0
                    continue
                m[i] = low
                mi = low
            # Halter: darf kein Material berühren
            h_lo = int(math.ceil((zc - z_to - f.h0) / step - 1e-9))
            h_hi = int(math.floor((zc - z_from - f.h0) / step + 1e-9))
            if h_hi < h_lo:
                h_lo = h_hi = int(round((zc - z_from - f.h0) / step))
            h_lo, h_hi = max(0, h_lo), min(nh - 1, h_hi)
            if h_lo <= h_hi and r + f.hmin.query(h_lo, h_hi) < mi - TOL_R:
                self._melde("halter", idx, st, zc, r,
                            "T{}: Halter {} stößt bei Z{:.2f} an das Werkstück.".format(st.t, f.halter.code, zc))

    def _quer_innen(self, idx: int, st: Schritt, f: Werkzeugform, z_from: float, z_to: float, r: float) -> None:
        """Bohrstange/Bohrer bei Radius r von z_from bis z_to: unterhalb der Oberkante ist danach Bohrung."""
        step = f.STEP
        dz, z_lo = self.dz, self.z_lo
        m, h, grenze = self.m, self.h, self.ziel_innen_max
        lo_rel = min(f.p0, f.h0)
        hi_rel = max(f.p0 + len(f.phigh) * step, f.h0 + len(f.hhigh) * step)
        i0 = max(0, int((z_from + lo_rel - z_lo) / dz))
        i1 = min(self.n, int((z_to + hi_rel - z_lo) / dz) + 2)
        np_, nh = len(f.phigh), len(f.hhigh)
        for i in range(i0, i1):
            mi = m[i]
            if mi < 0:
                continue
            zc = self.zc[i]
            k_lo = int(math.ceil((zc - z_to - f.p0) / step - 1e-9))
            k_hi = int(math.floor((zc - z_from - f.p0) / step + 1e-9))
            if k_hi < k_lo:
                k_lo = k_hi = int(round((zc - z_from - f.p0) / step))
            k_lo, k_hi = max(0, k_lo), min(np_ - 1, k_hi)
            high = r - f.pmax.query(k_lo, k_hi) if k_lo <= k_hi else -INF
            hi_ = h[i]
            if hi_ <= 1e-9 and high > TOL_R and k_lo <= k_hi and r + f.pmin.query(k_lo, k_hi) > TOL_R:
                # Platte ganz über der Achse in vollem Material: nur ein Bohrer kann hier anfangen
                self._melde("halter", idx, st, zc, r,
                            "T{}: fährt bei Z{:.2f} ins volle Material – Bohrung fehlt (vorbohren?).".format(
                                st.t, zc), key=("voll", st.t))
            if high > hi_:
                if high > grenze[i] + TOL_R:
                    self._melde("kontur", idx, st, zc, high,
                                "T{}: schneidet innen in die Fertigkontur (Z{:.2f}, {:.2f} mm zu weit).".format(
                                    st.t, zc, high - grenze[i]))
                if st.eil and high - hi_ > TOL_R:
                    self._melde("eilgang", idx, st, zc, high,
                                "T{}: Eilgang ins Material bei Z{:.2f}.".format(st.t, zc))
                if high >= mi:  # Wand ganz weg
                    m[i] = -1.0
                    continue
                h[i] = hi_ = high
            if k_lo <= k_hi and r + f.pmin.query(k_lo, k_hi) < -hi_ - TOL_R:
                self._melde("halter", idx, st, zc, r,
                            "T{}: Platte stößt bei Z{:.2f} unterhalb der Achse an die Bohrungswand.".format(st.t, zc),
                            key=("unten", st.t))
            # Stange bzw. Bohrfutter muss in die Bohrung passen (oben und unterhalb der Achse)
            h_lo = int(math.ceil((zc - z_to - f.h0) / step - 1e-9))
            h_hi = int(math.floor((zc - z_from - f.h0) / step + 1e-9))
            if h_hi < h_lo:
                h_lo = h_hi = int(round((zc - z_from - f.h0) / step))
            h_lo, h_hi = max(0, h_lo), min(nh - 1, h_hi)
            if h_lo <= h_hi:
                top = r - f.hmax.query(h_lo, h_hi)
                bot = r + f.hmin.query(h_lo, h_hi)
                if top > hi_ + TOL_R or bot < -hi_ - TOL_R:
                    self._melde("halter", idx, st, zc, r,
                                "T{}: {} stößt bei Z{:.2f} an das Werkstück (Bohrung Ø{:.2f}).".format(
                                    st.t, f.halter.code, zc, 2 * hi_), key=("halter", st.t))

    # -- Restmaterial -----------------------------------------------------------

    def _gruppen(self, fehler: List[float]) -> List[Tuple[int, int, float]]:
        groups: List[Tuple[int, int, float]] = []
        start, worst = -1, 0.0
        for i, e in enumerate(fehler):
            if e > 0.02:
                if start < 0:
                    start, worst = i, e
                worst = max(worst, e)
            elif start >= 0:
                groups.append((start, i - 1, worst))
                start = -1
        if start >= 0:
            groups.append((start, self.n - 1, worst))
        return groups

    def _rest(self) -> None:
        if not self.formen:
            return
        elems = self.geom.turn_elems()
        if elems:
            z_end = min(e.z1 for e in elems)
            fine = self.formen.get(self.teil.wz_schlichten)  # Restmaterial bleibt vom Schlichten
            rho = fine.radius if fine is not None else max(f.radius for f in self.formen.values())
            fehler = []
            for i, z in enumerate(self.zc):
                ziel = self.ziel[i]
                ok_zone = z_end + self.dz < z < -self.dz and ziel >= 0
                near_face = max(self.ziel[max(0, i - 1):i + 2]) - min(self.ziel[max(0, i - 1):i + 2]) > 0.05
                fehler.append(self.m[i] - ziel if ok_zone and not near_face else 0.0)
            self._melde_rest(self._gruppen(fehler), rho, self.ziel, 1.0, "Innenecke", "Restmaterial")
        innen = self.formen.get(self.teil.wz_innen_schlichten) or self.formen.get(self.teil.wz_innen_schruppen)
        if self.gin.elems and innen is not None:
            z_end = min(e.z1 for e in self.gin.elems)
            rho = innen.radius
            zi = self.ziel_innen
            fehler = []
            for i, z in enumerate(self.zc):
                ok_zone = z_end + self.dz < z < -self.dz and self.m[i] >= 0
                stufe = max(zi[max(0, i - 1):i + 2]) - min(zi[max(0, i - 1):i + 2]) > 0.05
                fehler.append(zi[i] - self.h[i] if ok_zone and not stufe else 0.0)
            self._melde_rest(self._gruppen(fehler), rho, zi, -1.0, "Ecke in der Bohrung",
                             "Restmaterial in der Bohrung")

    def _melde_rest(self, groups, rho: float, ziel: List[float], sign: float, ecke: str, rest: str) -> None:
        for a, b, worst in groups:
            za, zb = self.zc[b], self.zc[a]
            width = (b - a + 1) * self.dz
            if worst <= rho + 0.03 and width <= rho * 1.3 + 0.1:
                self.ereignisse.append(Ereignis(
                    "radius", "{} bei Z{:.2f}: bleibt mit dem Eckenradius der Platte (r{:g}) gerundet."
                    .format(ecke, (za + zb) / 2, rho), (za + zb) / 2, ziel[a] + sign * worst))
            else:
                self.ereignisse.append(Ereignis(
                    "rest", "{} Z{:.2f} … Z{:.2f}: bis {:.2f} mm stehen geblieben.".format(
                        rest, za, zb, worst), (za + zb) / 2, ziel[a] + sign * worst))

    # -- Anzeige ----------------------------------------------------------------

    def kontur_jetzt(self, schritt: float = 0.2) -> List[Point]:
        """Aktuelle Außenkontur (z, r) von rechts nach links, ausgedünnt."""
        pts: List[Point] = []
        every = max(1, int(round(schritt / self.dz)))
        prev = None
        for i in range(self.n - 1, -1, -1):
            r = self.m[i]
            if r < 0:
                if pts and pts[-1][1] != 0.0:
                    pts.append((self.zc[i], 0.0))
                prev = None
                continue
            if prev is None or i % every == 0 or abs(r - prev) > 0.05:
                if prev is not None and abs(r - prev) > 0.05:
                    pts.append((self.zc[i], prev))
                pts.append((self.zc[i], r))
                prev = r
        return pts

    def bohrung_jetzt(self, schritt: float = 0.2) -> List[Point]:
        """Aktuelle Bohrung (z, h) von rechts nach links, ausgedünnt; leer ohne Bohrung."""
        if not any(v > 0 for v in self.h):
            return []
        every = max(1, int(round(schritt / self.dz)))
        pts: List[Point] = []
        prev = None
        for i in range(self.n - 1, -1, -1):
            hv = self.h[i] if self.m[i] >= 0 else 0.0
            if prev is None or i % every == 0 or abs(hv - prev) > 0.05:
                if prev is not None and abs(hv - prev) > 0.05:
                    pts.append((self.zc[i], prev))
                pts.append((self.zc[i], hv))
                prev = hv
        return pts

    def zusammenfassung(self) -> str:
        schwer = [e for e in self.ereignisse if e.schwer]
        info = [e for e in self.ereignisse if not e.schwer]
        if not self.fertig:
            return "Schritt {} von {}".format(self.i, len(self.schritte))
        if schwer:
            return "✕  {} Problem(e): {}".format(len(schwer), schwer[0].text)
        if info:
            return "✓  Keine Kollision. {} Hinweis(e): {}".format(len(info), info[0].text)
        return "✓  Keine Kollision, kein Restmaterial."
