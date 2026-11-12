"""Simulation Fräsen und Bohren mit angetriebenem Werkzeug (C-/Y-Achse).

Modell
------
* Je Winkellage C ein Höhenraster in der Fräsebene: lokale Achsen x (radial, Werkzeugachse eines
  radialen Werkzeugs), y (quer, Y-Achse) und z (längs). Gespeichert wird je Zelle (y, z) die Höhe x
  der Oberfläche – am Anfang das fertig gedrehte Teil (Zylinder sqrt(R(z)² − y²)).
* Werkzeuge: Schaftfräser (ebene Stirn), Bohrer (Spitze 118°/140°), Fasenfräser (90°-Kegel).
  Unter dem Werkzeug wird die Höhe auf dessen Unterkante begrenzt.
* Stirnbohrungen (axial) werden als Bohrungen im Stirnschnitt mitgeführt.

Gemeldet werden Eilgang ins Material und – nach jedem Arbeitsgang – Abweichungen von der
Soll-Form (Restmaterial, zu tief oder außerhalb gefräst).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .kontur import Ergebnis, FraesOp, Teil, build_geometry, contour_radius
from .simulation import Ereignis
from . import werkzeugkatalog as wk

G = 0.2  # Rastermaß in mm
STUECK_G1 = 0.5  # Länge der Bahnstücke beim Abspielen
STUECK_G0 = 3.0
TOL = 0.06
NONE = -1e9  # kein Material in der Zelle
P3 = Tuple[float, float, float]


@dataclass
class FSchritt:
    op: int
    wi: int  # Index der Winkellage im Arbeitsgang
    a: P3  # (x, y, z), x als Radius
    b: P3
    eil: bool
    zeile: int

    @property
    def laenge(self) -> float:
        return math.sqrt(sum((self.b[k] - self.a[k]) ** 2 for k in range(3)))


class FWerkzeug:
    def __init__(self, teil: Teil, op: FraesOp) -> None:
        w = teil.tool(op.t)
        rw = wk.rundwerkzeug(w.halter) if w is not None else None
        dm = (w.dm if w is not None and w.dm > 0 else (rw.dm if rw else 6.0))
        self.t = op.t
        self.art = w.art if w is not None else "fraeser"
        self.r = dm / 2.0
        winkel = rw.spitzenwinkel if rw is not None and self.art != "fraeser" else (90.0 if self.art == "fase" else 118.0)
        self.k = 0.0 if self.art == "fraeser" else 1.0 / math.tan(math.radians(winkel / 2))
        self.name = w.name if w is not None else "T{}".format(op.t)


def richtung(winkel: float) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    """Radialrichtung d und Querrichtung e einer Winkellage (Stirnansicht, 0° oben, C+ gegen Uhrzeiger)."""
    a = math.radians(winkel)
    return (-math.sin(a), math.cos(a)), (math.cos(a), math.sin(a))


class FraesSimulation:
    def __init__(self, teil: Teil, res: Ergebnis, g: float = G) -> None:
        self.teil = teil
        self.res = res
        self.g = g
        self.ops: List[FraesOp] = list(res.fraes_ops)
        self.geom = build_geometry(teil)
        self.R = teil.rohteil_d / 2.0
        self.werkzeuge = [FWerkzeug(teil, op) for op in self.ops]
        self.pp_wechsel = (150.0, 0.0, 130.0)
        zs = [mv.z for op in self.ops for mv in op.moves if mv.z is not None and mv.kind == "G1"]
        rmax = max([w.r for w in self.werkzeuge] + [1.0])
        z_part = min(p.z for p in teil.punkte)
        self.z0 = max(min(zs + [0.0]) - rmax - 2, z_part - 2) if zs else -1.0
        z1 = min(max(zs + [z_part]) + rmax + 2, 1.0) if zs else 0.0
        self.nz = max(1, int(math.ceil((z1 - self.z0) / g)))
        self.y0 = -self.R - 1
        self.ny = int(math.ceil(2 * (self.R + 1) / g))
        self.zc = [self.z0 + (i + 0.5) * g for i in range(self.nz)]
        self.yc = [self.y0 + (j + 0.5) * g for j in range(self.ny)]
        self.rz = [self._r(z) for z in self.zc]
        self.winkel = sorted({round(a, 3) for op in self.ops if op.lage == "radial" for a in op.winkel})
        self.soll = [self._sollwerte(op) for op in self.ops]
        self.schritte = self._schritte()
        self.reset()

    def _r(self, z: float) -> float:
        if z > 0:
            return 0.0
        return contour_radius(self.teil, self.geom, z - self.g / 2, z + self.g / 2)

    # -- Bahn --------------------------------------------------------------------

    def _schritte(self) -> List[FSchritt]:
        out: List[FSchritt] = []
        for oi, op in enumerate(self.ops):
            x, y, z = self.pp_wechsel[0] / 2, 0.0, self.pp_wechsel[2]
            for wi, _a in enumerate(op.winkel):
                for mi, mv in enumerate(op.moves):
                    nx = x if mv.x is None else mv.x / 2.0
                    ny = y if mv.y is None else mv.y
                    nzz = z if mv.z is None else mv.z
                    line = op.zeilen[wi] + mi if wi < len(op.zeilen) else -1
                    ln = math.sqrt((nx - x) ** 2 + (ny - y) ** 2 + (nzz - z) ** 2)
                    if ln > 1e-9:
                        n = max(1, int(math.ceil(ln / (STUECK_G0 if mv.kind == "G0" else STUECK_G1) - 1e-9)))
                        for k in range(n):
                            a = (x + (nx - x) * k / n, y + (ny - y) * k / n, z + (nzz - z) * k / n)
                            b = (x + (nx - x) * (k + 1) / n, y + (ny - y) * (k + 1) / n, z + (nzz - z) * (k + 1) / n)
                            out.append(FSchritt(oi, wi, a, b, mv.kind == "G0", line))
                    x, y, z = nx, ny, nzz
        return out

    # -- Ablauf ------------------------------------------------------------------

    def reset(self) -> None:
        self.h: Dict[float, List[List[float]]] = {}
        for a in self.winkel:
            grid = []
            for iz in range(self.nz):
                r = self.rz[iz]
                grid.append([math.sqrt(r * r - y * y) if abs(y) < r else NONE for y in self.yc])
            self.h[a] = grid
        self.orig = {a: [row[:] for row in grid] for a, grid in self.h.items()}
        self.bohrungen: List[Tuple[float, float, float, float]] = []  # (winkel, x, r, tiefste z)
        self._vorher: Dict[Tuple[int, int], List[List[float]]] = {}
        # geänderter Z-Bereich (Rasterindex von/bis) je Winkellage – die Anzeige malt nur den neu
        self.geaendert: Dict[float, List[int]] = {}
        self.generation = getattr(self, "generation", 0) + 1
        self.i = 0
        self.ereignisse: List[Ereignis] = []
        self._gemeldet: set = set()
        self.fertig = not self.schritte

    def weiter(self, anzahl: int = 1) -> int:
        end = min(len(self.schritte), self.i + max(0, anzahl))
        while self.i < end:
            st = self.schritte[self.i]
            nxt = self.schritte[self.i + 1] if self.i + 1 < len(self.schritte) else None
            self._schritt(self.i, st)
            self.i += 1
            if nxt is None or (nxt.op, nxt.wi) != (st.op, st.wi):
                self._pruefe(st.op, st.wi)
        if self.i >= len(self.schritte):
            self.fertig = True
        return self.i

    def bis_ende(self) -> None:
        self.weiter(len(self.schritte))

    def gehe_zu(self, index: int) -> None:
        if index < self.i:
            self.reset()
        self.weiter(index - self.i)

    @property
    def zeile(self) -> int:
        if not self.schritte or self.i == 0:
            return -1
        return self.schritte[min(self.i, len(self.schritte)) - 1].zeile

    @property
    def position(self) -> Optional[Tuple[FraesOp, FWerkzeug, float, P3]]:
        if not self.schritte:
            return None
        st = self.schritte[max(0, min(self.i, len(self.schritte)) - 1)]
        op = self.ops[st.op]
        return op, self.werkzeuge[st.op], op.winkel[st.wi], (st.b if self.i > 0 else st.a)

    # -- Abtrag ------------------------------------------------------------------

    def _melde(self, art: str, idx: int, st: FSchritt, z: float, x: float, text: str) -> None:
        key = (art, st.op, st.wi, st.zeile)
        if key in self._gemeldet:
            return
        self._gemeldet.add(key)
        self.ereignisse.append(Ereignis(art, text, z, x, idx, st.zeile, self.ops[st.op].t))

    def _schritt(self, idx: int, st: FSchritt) -> None:
        op = self.ops[st.op]
        w = self.werkzeuge[st.op]
        a = round(op.winkel[st.wi], 3)
        if op.lage == "axial":
            if not st.eil and st.b[2] < st.a[2]:
                self._bohrung(a, st.b[0], w.r, st.b[2])
            return
        key = (st.op, st.wi)
        if key not in self._vorher:
            self._vorher[key] = [row[:] for row in self.h[a]]
        (xa, ya, za), (xb, yb, zb) = st.a, st.b
        if min(xa, xb) >= self.R + 0.01:
            return  # ganz über dem Teil
        grid = self.h[a]
        lo = max(0, int((min(za, zb) - w.r - self.z0) / self.g) - 1)
        hi = min(self.nz - 1, int((max(za, zb) + w.r - self.z0) / self.g) + 1)
        g_alt = self.geaendert.get(a)
        self.geaendert[a] = [lo, hi] if g_alt is None else [min(g_alt[0], lo), max(g_alt[1], hi)]
        if abs(xb - xa) < 1e-9 and w.k == 0:
            if abs(zb - za) < 1e-9:
                self._streifen(idx, st, grid, w, xa, (ya, yb), za, quer=True)
                return
            if abs(yb - ya) < 1e-9:
                self._streifen(idx, st, grid, w, xa, (za, zb), ya, quer=False)
                return
        if abs(ya - yb) < 1e-9 and abs(za - zb) < 1e-9:  # senkrecht ein-/ausfahren
            self._abdruck(idx, st, grid, w, min(xa, xb), ya, za)
            return
        ln = math.sqrt((xb - xa) ** 2 + (yb - ya) ** 2 + (zb - za) ** 2)
        n = max(1, int(math.ceil(ln / (self.g if w.k else self.g / 2))))
        for k in range(1, n + 1):
            t = k / n
            self._abdruck(idx, st, grid, w, xa + (xb - xa) * t, ya + (yb - ya) * t, za + (zb - za) * t)

    def _streifen(self, idx: int, st: FSchritt, grid, w: FWerkzeug, x: float, span: Tuple[float, float],
                  fest: float, quer: bool) -> None:
        """Schaftfräser fährt geradeaus (quer = in Y bei festem Z, sonst in Z bei festem Y)."""
        g, r = self.g, w.r
        lo, hi = min(span), max(span)
        if quer:
            i0 = max(0, int((fest - r - self.z0) / g))
            i1 = min(self.nz, int((fest + r - self.z0) / g) + 2)
            for iz in range(i0, i1):
                d = self.zc[iz] - fest
                if abs(d) > r:
                    continue
                half = math.sqrt(r * r - d * d)
                j0 = max(0, int(math.ceil((lo - half - self.y0) / g - 0.5)))
                j1 = min(self.ny, int(math.floor((hi + half - self.y0) / g - 0.5)) + 1)
                self._zeile(idx, st, grid[iz], j0, j1, x, self.zc[iz])
        else:
            j0 = max(0, int((fest - r - self.y0) / g))
            j1 = min(self.ny, int((fest + r - self.y0) / g) + 2)
            for j in range(j0, j1):
                d = self.yc[j] - fest
                if abs(d) > r:
                    continue
                half = math.sqrt(r * r - d * d)
                i0 = max(0, int(math.ceil((lo - half - self.z0) / g - 0.5)))
                i1 = min(self.nz, int(math.floor((hi + half - self.z0) / g - 0.5)) + 1)
                for iz in range(i0, i1):
                    hv = grid[iz][j]
                    if hv > x:
                        if st.eil and hv - x > TOL:
                            self._eil(idx, st, self.zc[iz], x)
                        grid[iz][j] = x

    def _zeile(self, idx: int, st: FSchritt, row: List[float], j0: int, j1: int, x: float, z: float) -> None:
        for j in range(j0, j1):
            hv = row[j]
            if hv > x:
                if st.eil and hv - x > TOL:
                    self._eil(idx, st, z, x)
                row[j] = x

    def _abdruck(self, idx: int, st: FSchritt, grid, w: FWerkzeug, x: float, y: float, z: float) -> None:
        g, r, k = self.g, w.r, w.k
        i0 = max(0, int((z - r - self.z0) / g))
        i1 = min(self.nz, int((z + r - self.z0) / g) + 2)
        j0 = max(0, int((y - r - self.y0) / g))
        j1 = min(self.ny, int((y + r - self.y0) / g) + 2)
        r2 = r * r
        for iz in range(i0, i1):
            dz2 = (self.zc[iz] - z) ** 2
            if dz2 > r2:
                continue
            row = grid[iz]
            for j in range(j0, j1):
                d2 = dz2 + (self.yc[j] - y) ** 2
                if d2 > r2:
                    continue
                bottom = x + k * math.sqrt(d2) if k else x
                hv = row[j]
                if hv > bottom:
                    if st.eil and hv - bottom > TOL:
                        self._eil(idx, st, self.zc[iz], bottom)
                    row[j] = bottom

    def _eil(self, idx: int, st: FSchritt, z: float, x: float) -> None:
        self._melde("eilgang", idx, st, z, x, "T{}: Eilgang ins Material (C{:g}, Z{:.2f}).".format(
            self.ops[st.op].t, self.ops[st.op].winkel[st.wi], z))

    def _bohrung(self, a: float, x: float, r: float, z: float) -> None:
        for n, (wa, wx, wr, wz) in enumerate(self.bohrungen):
            if abs(wa - a) < 1e-6 and abs(wx - x) < 1e-6 and abs(wr - r) < 1e-6:
                if z < wz:
                    self.bohrungen[n] = (wa, wx, wr, z)
                return
        self.bohrungen.append((a, x, r, z))

    # -- Soll-Form prüfen ------------------------------------------------------------

    def _pruefe(self, oi: int, wi: int) -> None:
        op = self.ops[oi]
        if op.lage != "radial" or op.entgraten or not (0 < op.nr <= len(self.teil.fraesungen)):
            return
        fr = self.teil.fraesungen[op.nr - 1]
        a = round(op.winkel[wi], 3)
        before = self._vorher.get((oi, wi))
        if before is None:
            return
        grid = self.h[a]
        w = self.werkzeuge[oi]
        werte = self.soll[oi]
        g = self.g
        if not werte:
            return
        worst_rest, worst_over = (0.0, None), (0.0, None)
        for iz, z in enumerate(self.zc):
            for j, y in enumerate(self.yc):
                b = before[iz][j]
                if b <= NONE / 2:
                    continue
                soll = self._soll(fr, w, werte, y, z, b, g)
                if soll is None:
                    continue
                hv = grid[iz][j]
                if hv > soll + TOL and hv - soll > worst_rest[0]:
                    worst_rest = (hv - soll, (z, y))
                if hv < soll - TOL and soll - hv > worst_over[0]:
                    worst_over = (soll - hv, (z, y))
        st = self.schritte[self.i - 1]
        if worst_rest[1] is not None:
            z, y = worst_rest[1]
            self._melde("rest", self.i - 1, st, z, 0.0, "Fräsung {} bei C{:g}: Restmaterial bis {:.2f} mm "
                        "(Z{:.1f}, Y{:.1f}).".format(op.nr, op.winkel[wi], worst_rest[0], z, y))
        if worst_over[1] is not None:
            z, y = worst_over[1]
            self._melde("ueberschnitt", self.i - 1, st, z, 0.0, "Fräsung {} bei C{:g}: {:.2f} mm zu viel "
                        "weggefräst (Z{:.1f}, Y{:.1f}).".format(op.nr, op.winkel[wi], worst_over[0], z, y))

    def _sollwerte(self, op: FraesOp) -> Dict[str, float]:
        """Soll-Maße einer Fräsung, gerechnet wie im Code-Erzeuger."""
        if not (0 < op.nr <= len(self.teil.fraesungen)):
            return {}
        fr = self.teil.fraesungen[op.nr - 1]
        if fr.art == "flaeche":
            return {"h": fr.mass / 2}
        if fr.art == "nut":
            z_hi, z_lo = min(0.0, max(fr.z1, fr.z2)), min(fr.z1, fr.z2)
            return {"rb": contour_radius(self.teil, self.geom, z_lo + 0.01, z_hi - 0.01) - fr.tiefe}
        if fr.art == "bohrung_radial":
            return {"tip": contour_radius(self.teil, self.geom, fr.z1 - 0.001, fr.z1 + 0.001) - fr.tiefe}
        return {}

    def _soll(self, fr, w: FWerkzeug, werte: Dict[str, float], y: float, z: float, vorher: float,
              g: float) -> Optional[float]:
        """Soll-Höhe einer Zelle nach dem Arbeitsgang; None = Randzone, nicht prüfen."""
        band = g * 1.5
        if fr.art == "flaeche":
            z_hi, z_lo = min(0.0, max(fr.z1, fr.z2)), min(fr.z1, fr.z2)
            if z_lo + band < z < z_hi - band:
                return min(vorher, werte["h"])
            if z < z_lo - band or z > max(fr.z1, fr.z2) + band:
                return vorher
            return None
        if fr.art == "nut":
            z_hi, z_lo = max(fr.z1, fr.z2), min(fr.z1, fr.z2)
            b2, rt = fr.mass / 2, min(w.r, fr.mass / 2)
            dy = max(0.0, abs(y) - (b2 - rt))
            if z_hi >= -1e-6:  # läuft vorn aus: keine Stirnwand
                dz = max(0.0, (z_lo + rt) - z)
            else:
                dz = max(0.0, (z_lo + rt) - z, z - (z_hi - rt))
            dist = math.hypot(dy, dz)
            if dist < rt - band:
                return min(vorher, werte["rb"])
            if dist > rt + band:
                return vorher
            return None
        if fr.art == "bohrung_radial":
            rho = math.hypot(y, z - fr.z1)
            if rho < fr.mass / 2 - band:
                return min(vorher, werte["tip"] + w.k * rho)
            if rho > fr.mass / 2 + band:
                return vorher
            return None
        return None

    # -- Anzeige --------------------------------------------------------------------

    def schnitt(self, z: float, strahlen: int = 180) -> List[Tuple[float, float]]:
        """Querschnitt bei z in der Stirnansicht (x nach rechts, y nach oben)."""
        iz = int((z - self.z0) / self.g)
        r_max = self._r(z) if not (0 <= iz < self.nz) else self.rz[iz]
        if r_max <= 0:
            return []
        frames = []
        if 0 <= iz < self.nz:
            for a, grid in self.h.items():
                d, e = richtung(a)
                frames.append((d, e, grid[iz], self.orig[a][iz]))
        pts = []
        for k in range(strahlen):
            phi = 2 * math.pi * k / strahlen
            ux, uy = math.cos(phi), math.sin(phi)
            lo, hi = 0.0, r_max
            if self._material(hi, ux, uy, frames):
                rho = hi
            else:
                for _ in range(14):
                    mid = (lo + hi) / 2
                    if self._material(mid, ux, uy, frames):
                        lo = mid
                    else:
                        hi = mid
                rho = lo
            pts.append((rho * ux, rho * uy))
        return pts

    def _material(self, rho: float, ux: float, uy: float, frames) -> bool:
        px_, py_ = rho * ux, rho * uy
        for d, e, row, orig in frames:
            xl = px_ * d[0] + py_ * d[1]
            yl = px_ * e[0] + py_ * e[1]
            j = int((yl - self.y0) / self.g)
            # nur bearbeitete Zellen begrenzen den Schnitt (am Zylinderrand ist das Raster zu grob)
            if 0 <= j < self.ny and row[j] < orig[j] - 1e-6 and xl > row[j] + 1e-9:
                return False
        return True
