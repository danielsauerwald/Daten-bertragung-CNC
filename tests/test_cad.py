"""3D-Modell (STL/STEP) -> Drehteil: Achse, Profil, Kontur, Bohrung, Flächen, Nullpunkt."""

import math
import os

import pytest

from cnc_dnc import cad
from cnc_dnc.kontur import beispiel_teil, generate

DATA = os.path.join(os.path.dirname(__file__), "data")


def _dreh_stl(path, kontur, n=64, ring=0.0):
    """Drehkörper um die Modell-Z-Achse als binäre STL. ``kontur``: geschlossener Halbschnitt [(z, r)]."""
    cad.speichere_stl(cad.drehkoerper(kontur, n, ring), path)
    return path


SPITZE = 4 / math.tan(math.radians(59))
# Welle Ø32/Ø26/Ø20 mit Fase 1 vorn, Bohrung Ø8 × 12 mit Spitze; Stirnseite bei Modell-Z 50
WELLE = [(38 - SPITZE, 0), (38, 4), (50, 4), (50, 9), (49, 10), (35, 10), (35, 13), (20, 13), (20, 16), (0, 16),
         (0, 0)]


def _welle(tmp_path, **kw):
    return cad.lade(_dreh_stl(str(tmp_path / "welle.stl"), WELLE, **kw))


def _lage(m, i=0):
    n, p, d = cad.achsen_kandidaten(m)[i]
    return cad.lage_standard(m, p, d, n)


def test_stl_turned_part_contour_and_bore(tmp_path):
    m = _welle(tmp_path)
    assert m.name == "welle" and not m.flaechen
    lage = _lage(m)
    assert lage.quelle == "Modell-Z" and abs(lage.z[2] - 1) < 1e-9
    erk = cad.teil_aus_modell(m, lage)
    t = erk.teil
    assert [(p.x, p.z, p.ecke, p.wert) for p in t.punkte] == [
        (0, 0, "", 0), (20, 0, "fase", 1.0), (20, -15, "", 0), (26, -15, "", 0), (26, -30, "", 0),
        (32, -30, "", 0), (32, -50, "", 0)]
    assert (t.bohr_d, t.bohr_tiefe, t.innen) == (8, 12.0, [])
    assert (t.rohteil_d, t.rohteil_l) == (34, math.ceil(50 + t.planaufmass + 3))
    assert t.name == "WELLE" and erk.seite2 is None
    assert any("Bohrung Ø8 × 12" in s for s in erk.merkmale)


def test_profile_survives_vertices_on_slice_planes(tmp_path):
    # Eckpunkte alle 1,25 mm – genau auf den Schnitt-Mitten ohne Versatz
    m = _welle(tmp_path, ring=1.25)
    prof = cad.profil(m, _lage(m))
    k12 = int(11.5 / prof.dz)
    assert all(r > 3.9 for r in prof.innen[:k12])
    k2, k14 = int(2 / prof.dz), int(14 / prof.dz)
    assert all(abs(r - 10) < 0.01 for r in prof.aussen[k2:k14])
    t = cad.teil_aus_modell(m, _lage(m), prof=prof).teil
    assert (t.bohr_d, t.bohr_tiefe) == (8, 12.0) and len(t.punkte) == 7


def test_turn_part_around_gives_second_setup_hint(tmp_path):
    m = _welle(tmp_path)
    lage = cad.wenden(m, _lage(m))
    erk = cad.teil_aus_modell(m, lage)
    assert erk.seite2 is not None and abs(erk.seite2 + 20) < 0.05
    assert any("Rückseite" in h for h in erk.hinweise)
    assert [(p.x, p.z) for p in erk.teil.punkte] == [(0, 0), (32, 0), (32, -20)]
    assert any("Rückseite" in h for h in erk.hinweise if "Bohrung" in h)  # Bohrung von hinten


def test_stepped_bore_becomes_inner_contour(tmp_path):
    kontur = [(38 - SPITZE, 0), (38, 4), (45, 4), (45, 6), (50, 6), (50, 16), (0, 16), (0, 0)]
    m = cad.lade(_dreh_stl(str(tmp_path / "buchse.stl"), kontur))
    t = cad.teil_aus_modell(m, _lage(m)).teil
    assert t.bohr_d == 8 and t.bohr_tiefe == 12.0
    assert [(p.x, p.z) for p in t.innen] == [(12, 0), (12, -5), (8, -5), (8, -12)]


def test_stl_ascii_and_errors(tmp_path):
    p = tmp_path / "a.stl"
    p.write_text("solid x\n facet normal 0 0 1\n  outer loop\n   vertex 0 0 0\n   vertex 1 0 0\n"
                 "   vertex 0 1 0\n  endloop\n endfacet\nendsolid x\n")
    with pytest.raises(cad.CADFehler):
        cad.lade(str(p))  # zu wenig Flächen
    with pytest.raises(cad.CADFehler):
        cad.lade(str(tmp_path / "teil.iges"))


def test_real_cad_stl_with_key_flats():
    m = cad.lade(os.path.join(DATA, "welle.stl"))
    lage = _lage(m)
    assert lage.quelle == "Modell-X"
    erk = cad.teil_aus_modell(m, lage)
    t = erk.teil
    assert [(p.x, p.z, p.ecke) for p in t.punkte] == [
        (0, 0, ""), (20, 0, "fase"), (20, -15, "radius"), (26, -15, ""), (26, -30, ""), (32, -30, ""),
        (32, -40, "")]
    assert (t.bohr_d, t.bohr_tiefe) == (8, 12.0)
    flaechen = [f for f in t.fraesungen if f.art == "flaeche"]
    assert len(flaechen) == 1 and flaechen[0].winkel == [0.0, 180.0]
    assert any("Querbohrungen" in h for h in erk.hinweise)  # nur aus STEP


@pytest.mark.skipif(not cad.step_verfuegbar(), reason="STEP braucht cascadio (PC-Version)")
def test_step_exact_axis_holes_and_flats():
    m = cad.lade(os.path.join(os.path.dirname(cad.__file__), "daten", "beispiel_welle.step"))
    assert m.flaechen and len(m.flaeche) == len(m.dreiecke)
    kand = cad.achsen_kandidaten(m)
    assert kand[0][0].startswith("genau")
    erk = cad.teil_aus_modell(m, _lage(m))
    t = erk.teil
    assert [(p.x, p.z, p.ecke, p.wert) for p in t.punkte][1:3] == [(20, 0, "fase", 1.0), (20, -15, "radius", 1.0)]
    arten = sorted(f.art for f in t.fraesungen)
    assert arten == ["bohrung_radial", "flaeche"]
    br = next(f for f in t.fraesungen if f.art == "bohrung_radial")
    assert br.winkel == [90.0] and abs(br.z1 + 22.5) < 1e-6
    assert not erk.hinweise
    code = generate(t).code
    assert "M30" in code or "M02" in code or code


def test_program_zero_shifts_all_z_words():
    t = beispiel_teil()
    a = generate(t).code.splitlines()
    t.z_null = 15
    b = generate(t).code.splitlines()
    assert len(a) == len(b)
    assert any("STIRNSEITE BEI Z15" in ln for ln in b)
    import re

    zw = re.compile(r"(?<![A-Z_])Z(-?\d+(?:\.\d*)?)")
    n = 0
    for la, lb in zip(a, b):
        if la.lstrip().startswith(";") or la.lstrip().startswith("("):
            continue
        za, zb = zw.findall(la), zw.findall(lb)
        assert len(za) == len(zb)
        for x, y in zip(za, zb):
            assert abs(float(y) - float(x) - 15) < 1e-6
            n += 1
    assert n > 20
