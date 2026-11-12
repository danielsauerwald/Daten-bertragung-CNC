"""Werkzeugkatalog: ISO-Plattencodes, Halter, Bohrstangen und Geometrie."""

import math

import pytest

from cnc_dnc import werkzeugkatalog as wk


@pytest.mark.parametrize("code,form,ic,radius,laenge", [
    ("CNMG 120408-M", "C", 12.7, 0.8, 12.9),
    ("CCMT 09T304", "C", 9.525, 0.4, 9.7),
    ("DNMG 150608", "D", 12.7, 0.8, 15.5),
    ("DCMT 11T304-F", "D", 9.525, 0.4, 11.6),
    ("VNMG 160404", "V", 9.525, 0.4, 16.6),
    ("vcmt110302", "V", 6.35, 0.2, 11.1),
    ("WNMG 080408", "W", 12.7, 0.8, 8.7),
    ("WNMG 060404", "W", 9.525, 0.4, 6.5),
    ("TNMG 160408", "T", 9.525, 0.8, 16.5),
    ("SNMG 120408", "S", 12.7, 0.8, 12.7),
    ("RCMT 1204M0", "R", 12.0, 6.0, 12.0),
])
def test_iso_insert_codes(code, form, ic, radius, laenge):
    p = wk.parse_platte(code)
    assert p is not None and p.form == form
    assert p.ic == pytest.approx(ic) and p.radius == pytest.approx(radius)
    assert p.schneidenlaenge == pytest.approx(laenge, abs=0.06)


def test_invalid_codes():
    for code in ("", "XNMG 120408", "CNMG 1204", "CNMG 990408", "VCMT"):
        assert wk.parse_platte(code) is None


def test_catalog_is_complete_and_consistent():
    assert all(wk.parse_platte(c) for c in wk.PLATTEN)
    families = {c[:4] for c in wk.PLATTEN}
    for fam in ("WNMG", "DCMT", "VCMT", "DNMG", "VNMG", "CCMT", "CNMG"):  # gewünschte Plattenformen
        assert fam in families
    outside = [h for h in wk.HALTER if h.art == "aussen"]
    inside = [h for h in wk.HALTER if h.art == "innen"]
    assert len(outside) >= 25 and len(inside) >= 20
    for h in wk.HALTER:
        plates = wk.passende_platten(h)
        assert plates, h.code
        assert all(wk.platte_passt(h, wk.parse_platte(c)) for c in plates)
    assert len({h.code for h in wk.HALTER}) == len(wk.HALTER)
    h = wk.halter("PDJNR 2020K15")
    assert (h.kappa, h.form, h.groesse, h.b, h.laenge, h.hand) == (93, "D", "15", 20, 125, "R")
    assert "DNMG 150408-M" in wk.passende_platten(h) and "DCMT 11T304-F" not in wk.passende_platten(h)


def test_boring_bars_fit_their_minimum_bore():
    """Stange (Ø d) mit Spitze im Abstand f1 von der Achse muss in die kleinste Bohrung passen."""
    for h in wk.HALTER:
        if h.art != "innen":
            continue
        assert h.schneidenlage == 2
        R = h.dmin / 2
        axis = R - h.f1  # Spitze an der Bohrungswand
        assert abs(axis - h.b / 2) <= R + 1e-9 and abs(axis + h.b / 2) <= R + 1e-9, h.code
        assert h.f1 > h.b / 2  # Spitze steht über die Stange hinaus


def test_cutting_edge_directions():
    c = wk.parse_platte("CNMG 120408")
    assert wk.schneiden_richtungen(wk.halter("PCLNR 2020K12"), c) == (85, 5)
    assert wk.schneiden_richtungen(wk.halter("PCLNL 2020K12"), c) == (175, 95)
    v = wk.parse_platte("VBMT 160404")
    assert wk.schneiden_richtungen(wk.halter("SVVBN 2020K16"), v) == (107.5, 72.5)  # neutral
    assert wk.schneiden_richtungen(wk.halter("A16R-SCLCR09"), wk.parse_platte("CCMT 09T304")) == (-5, -85)


@pytest.mark.parametrize("hcode,pcode", [
    ("PCLNR 2020K12", "CNMG 120408-M"), ("PDJNR 2020K15", "DNMG 150408-M"), ("SVJCR 2020K16", "VCMT 160404-F"),
    ("PWLNR 2020K08", "WNMG 080408-M"), ("PTGNR 2020K16", "TNMG 160408-M"), ("A16R-SCLCR09", "CCMT 09T304-M"),
    ("A20S-SDUCR11", "DCMT 11T304-F"), ("SVVBN 2020K16", "VBMT 160404-F"),
])
def test_insert_outline_touches_theoretical_tip(hcode, pcode):
    """Die Platte berührt die Achsparallelen durch die theoretische Spitze P (wie beim Vermessen)."""
    h, p = wk.halter(hcode), wk.parse_platte(pcode)
    s = (10.0, 20.0)
    off = wk.spitze_versatz(h, p)
    tip = (s[0] + off[0], s[1] + off[1])
    pts = wk.platten_umriss(h, p, s, steps=24)
    rho = p.radius
    if h.art == "innen":
        assert max(y for _, y in pts) == pytest.approx(tip[1], abs=0.01)
    else:
        assert min(y for _, y in pts) == pytest.approx(tip[1], abs=0.01)
    if h.hand != "N":
        assert min(x for x, _ in pts) == pytest.approx(tip[0], abs=0.01)
    # alle Punkte mindestens rho vom Mittelpunkt entfernt (Radius ist die Ecke)
    assert min(math.hypot(x - s[0], y - s[1]) for x, y in pts) == pytest.approx(rho, abs=0.01)
    # Inkreis passt in den Umriss: Fläche etwa wie erwartet
    area = abs(sum(pts[i - 1][0] * pts[i][1] - pts[i][0] * pts[i - 1][1] for i in range(len(pts)))) / 2
    assert area > math.pi * (p.ic / 2) ** 2


def test_round_tools():
    b = wk.rundwerkzeug("VHM-BOHRER D6.8")
    assert b.art == "bohrer" and b.dm == 6.8
    f = wk.rundwerkzeug("VHM-FRAESER D10 Z4")
    assert f.art == "fraeser" and f.zaehne == 4
    assert wk.rundwerkzeug("FASENFRAESER D10 90").spitzenwinkel == 90
    assert wk.rundwerkzeug("gibt es nicht") is None
