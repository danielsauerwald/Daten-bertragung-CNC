"""Frässtrategien und Fräs-Simulation: jede Strategie erzeugt die Soll-Form, Fehler werden erkannt."""

import math
import re

import pytest

from cnc_dnc import kontur as k
from cnc_dnc.fraessim import FraesSimulation
from cnc_dnc.kontur import FMove, Fraesung as F

FAELLE = [
    ("flaeche", F("flaeche", [0, 180], 0, -10, 17, 0, 0, 5)),
    ("flaeche_innen", F("flaeche", [90], -17, -28, 22, 0, 0, 5)),
    ("nut", F("nut", [0, 120, 240], -16, -25, 10, 2, 0, 5)),
    ("nut_vorn", F("nut", [0], 0, -12, 8, 1.5, 0, 5)),
    ("nut_breit", F("nut", [45], -16, -29, 14, 2.5, 0, 5)),
    ("bohrung", F("bohrung_radial", [90, 270], -22.5, 0, 5, 9, 0, 6)),
]


def _sim(frs, **kw):
    t = k.beispiel_teil()
    t.fraesungen = frs
    for key, v in kw.items():
        setattr(t, key, v)
    res = k.generate(t, rahmen=True)
    assert res.ok, res.errors
    sim = FraesSimulation(t, res)
    return t, res, sim


@pytest.mark.parametrize("name,fr", FAELLE, ids=[n for n, _ in FAELLE])
def test_every_strategy_produces_the_nominal_shape(name, fr):
    for strat in k.STRATEGIEN[fr.art]:
        if strat == "trochoidal" and fr.mass < k.TROCHOID_MIN * 8:
            continue
        fr.strategie = strat
        _t, res, sim = _sim([fr])
        sim.bis_ende()
        probleme = [e.text for e in sim.ereignisse if e.schwer]
        assert not probleme, (strat, probleme)
        assert sim.fertig and len(sim.schritte) > 3


def test_strategies_differ_as_described():
    fr = F("flaeche", [0], 0, -10, 17, 0, 0, 5)

    def y_cuts(strat):
        fr.strategie = strat
        _t, res, _s = _sim([fr])
        moves = res.fraes_ops[0].moves
        out, y = [], 0.0
        for mv in moves:
            if mv.kind == "G1" and mv.y is not None and mv.x is None and mv.z is None:
                out.append(mv.y - y)
            if mv.y is not None:
                y = mv.y
        return out

    assert all(d < 0 for d in y_cuts("gleichlauf"))  # M3, Material bei -Z: Gleichlauf schneidet nach -Y
    assert all(d > 0 for d in y_cuts("gegenlauf"))
    zz = y_cuts("zickzack")
    assert any(d > 0 for d in zz) and any(d < 0 for d in zz)
    # Rampe: kein senkrechtes Eintauchen unter die Oberfläche
    nut = F("nut", [0], -16, -25, 8, 2, 0, 5, strategie="rampe")
    t, res, _s = _sim([nut])
    x = z = None
    for mv in res.fraes_ops[0].moves:
        if mv.kind == "G1" and mv.x is not None and x is not None and mv.x < x - 1e-9 and mv.x / 2 < 13:
            assert mv.z is not None and mv.z != z, "senkrecht eingetaucht"
            assert math.degrees(math.atan((x - mv.x) / 2 / abs(mv.z - z))) <= k.RAMPENWINKEL + 0.01
        x = mv.x if mv.x is not None else x
        z = mv.z if mv.z is not None else z
    # Bohren: Spanbruch zieht nur kurz zurück, in einem Zug gar nicht
    b = F("bohrung_radial", [0], -22.5, 0, 5, 9, 0, 6, strategie="spanbruch")
    _t, res, _s = _sim([b])
    xs = [mv.x for mv in res.fraes_ops[0].moves if mv.kind == "G0" and mv.x is not None]
    assert xs.count(30) == 2  # nur Anfahren und Ende ganz raus
    b.strategie = "einzug"
    _t, res, _s = _sim([b])
    assert len([mv for mv in res.fraes_ops[0].moves if mv.kind == "G1"]) == 1


def test_trochoidal_needs_wide_slot():
    res = k.generate(k.Teil.from_dict(dict(k.beispiel_teil().to_dict(), fraesungen=[
        F("nut", [0], -16, -25, 9, 2, 0, 5, strategie="trochoidal").__dict__])))
    assert not res.ok and "1.25" in res.errors[0]


def test_simulation_detects_wrong_milling_paths():
    fr = F("nut", [0], -16, -25, 10, 2, 0, 5)
    # letzte Tiefe weglassen -> Restmaterial
    t, res, _s = _sim([fr])
    op = res.fraes_ops[0]
    deepest = min(mv.x for mv in op.moves if mv.x is not None)
    idx = next(i for i, mv in enumerate(op.moves) if mv.x == deepest)
    op.moves[idx:-1] = []
    sim = FraesSimulation(t, res)
    sim.bis_ende()
    assert any(e.art == "rest" for e in sim.ereignisse)
    # zu tief -> zu viel weggefräst
    t, res, _s = _sim([fr])
    op = res.fraes_ops[0]
    deepest = min(mv.x for mv in op.moves if mv.x is not None)
    op.moves = [FMove(mv.kind, (mv.x - 1.0) if mv.x == deepest else mv.x, mv.y, mv.z) for mv in op.moves]
    sim = FraesSimulation(t, res)
    sim.bis_ende()
    assert any(e.art == "ueberschnitt" for e in sim.ereignisse)
    # Eilgang durch das Teil
    t, res, _s = _sim([fr])
    op = res.fraes_ops[0]
    op.moves.insert(2, FMove("G0", 20.0, None, None))
    op.moves.insert(3, FMove("G0", None, None, -5.0))
    sim = FraesSimulation(t, res)
    sim.bis_ende()
    assert any(e.art == "eilgang" for e in sim.ereignisse)


def test_hexagon_cross_section():
    """Sechskant SW17 auf Ø20: Schnitt hat innen 8,5 mm Abstand zu den Flächen, Ecken 9,81 mm."""
    fr = F("flaeche", k.gleich_verteilt(6), 0, -10, 17, 0, 0, 5)
    _t, _res, sim = _sim([fr])
    sim.bis_ende()
    assert not [e for e in sim.ereignisse if e.schwer]
    pts = sim.schnitt(-5.0, 360)
    rad = [math.hypot(x, y) for x, y in pts]
    assert min(rad) == pytest.approx(8.5, abs=0.15)
    assert max(rad) == pytest.approx(8.5 / math.cos(math.radians(30)), abs=0.15)
    # außerhalb der Fläche bleibt der Zylinder Ø20
    assert min(math.hypot(x, y) for x, y in sim.schnitt(-12.0, 90)) == pytest.approx(10.0, abs=0.05)


def test_face_holes_are_tracked_and_lines_map():
    fr = F("bohrung_axial", [0, 90, 180, 270], 0, 0, 4, 6, 12, 8)
    _t, res, sim = _sim([fr])
    sim.bis_ende()
    assert len(sim.bohrungen) == 4 and all(b[3] == pytest.approx(-6) for b in sim.bohrungen)
    code = res.code.split("\n")
    lines = {st.zeile for st in sim.schritte}
    assert all(re.match(r"G0[01] ", code[n]) for n in lines)
    sim.gehe_zu(5)
    op, w, winkel, pos = sim.position
    assert op.lage == "axial" and w.r == 2.5 and winkel == 0


def test_section_keeps_slot_walls_vertical():
    """Querschnitt einer Nut: senkrechte Wände (Raster 0,2 mm), nicht schräg wie bei Strahlen vom Mittelpunkt."""
    t = k.beispiel_teil()
    t.tool(5).dm = 5
    t.fraesungen = [k.Fraesung("nut", [0.0], -17, -25, 6, 3, 0, 5)]
    sim = FraesSimulation(t, k.generate(t, rahmen=True))
    sim.bis_ende()
    polys = sim.abtrag(-21.0)
    assert len(polys) == 1
    pts = polys[0]
    # C0: Nut oben (y), quer x. Unten Boden bei r10, Wände bei x = ±3 (auf das Raster 0,2 genau)
    boden = [p for p in pts if abs(p[1] - 10.0) < 1e-6]
    assert boden and min(x for x, _ in boden) == pytest.approx(-3.0, abs=0.21)
    assert max(x for x, _ in boden) == pytest.approx(3.0, abs=0.21)
    oben = [p for p in pts if p[1] > 12.5]
    assert oben and min(x for x, _ in oben) == pytest.approx(-3.0, abs=0.21)
    assert max(x for x, _ in oben) == pytest.approx(3.0, abs=0.21)
    assert sim.abtrag(-10.0) == []  # dort ist nichts gefräst


def test_tracyl_deburr_simulates_with_chamfer_on_walls():
    t = k.beispiel_teil()
    t.tool(5).dm = 5
    t.fraesungen = [k.Fraesung("nut", [0.0], -17, -25, 6, 3, 0, 5, True, 7, 0.3)]
    res = k.generate(t, rahmen=True)
    assert res.ok, res.errors
    op = [o for o in res.fraes_ops if o.entgraten][0]
    assert op.titel.endswith("(C-ACHSE)") and len(op.moves) == len(
        [ln for ln in res.code.split("\n")[op.zeilen[0]:op.zeilen[0] + len(op.moves)]])
    sim = FraesSimulation(t, res)
    sim.bis_ende()
    assert not [e for e in sim.ereignisse if e.schwer], [e.text for e in sim.ereignisse]
    iz = int((-21.0 - sim.z0) / sim.g)
    row, orig = sim.h[0.0][iz], sim.orig[0.0][iz]
    fase = {round(sim.yc[j], 1): orig[j] - row[j] for j in range(sim.ny) if 3.0 < abs(sim.yc[j]) < 3.6}
    assert fase[3.1] > 0.1 and fase[-3.1] > 0.1  # Kante gebrochen
    assert fase[3.5] < 0.01  # aber keine große Fase


def test_mantel_groove_simulation():
    """Kurvennut ganz herum (Z-20 → Z-35 → Z-20) und offene Nut: Tiefe und Breite im Mantel-Raster."""
    t = k.neues_teil()
    t.rohteil_d, t.rohteil_l = 32.0, 70.0
    t.punkte = [k.KPunkt(0, 0), k.KPunkt(30, 0, "fase", 1), k.KPunkt(30, -60)]
    t.tool(5).dm = 5
    t.fraesungen = [k.Fraesung("mantelnut", [0.0], 0, 0, 5, 2, 0, 5, True, 7, 0.3,
                               bahn=[[-20, 0], [-35, 180], [-20, 360]]),
                    k.Fraesung("mantelnut", [30.0], 0, 0, 6, 1, 0, 5, bahn=[[-45, 30], [-50, 120]])]
    res = k.generate(t, rahmen=True)
    assert res.ok, res.errors
    assert [o.lage for o in res.fraes_ops] == ["mantel", "mantel", "mantel"]
    sim = FraesSimulation(t, res)
    sim.bis_ende()
    assert not [e for e in sim.ereignisse if e.schwer], [e.text for e in sim.ereignisse]

    def tiefe(z, c):
        iz = int((z - sim.z0) / sim.g)
        kc = int(c / sim.dc) % sim.nc
        return sim.mh_orig[iz][kc] - sim.mh[iz][kc]

    assert tiefe(-20, 0) == pytest.approx(2.0, abs=0.02)
    assert tiefe(-35, 180) == pytest.approx(2.0, abs=0.02)
    assert tiefe(-27.5, 90) == pytest.approx(2.0, abs=0.02)
    assert tiefe(-23.2, 0) == pytest.approx(0.0, abs=0.02)  # neben der Nut (Breite 5)
    assert tiefe(-47.5, 75) == pytest.approx(1.0, abs=0.02)
    assert tiefe(-55, 75) == pytest.approx(0.0, abs=0.02)
    # Querschnitt zeigt die Nut, Werkzeugposition mit C
    assert sim.abtrag(-27.5)
    op, w, winkel, (x, y, z) = sim.position
    assert op.lage == "mantel" and y == 0.0
