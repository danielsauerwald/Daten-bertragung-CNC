"""Simulation mit Plattenform: richtige Werkzeuge ohne Meldung, falsche werden erkannt."""

import pytest

from cnc_dnc import kontur as k
from cnc_dnc.kontur import KPunkt, Move
from cnc_dnc.postpro import vorlage
from cnc_dnc.simulation import DrehSimulation, kompensierte_bahn

from test_kontur import welle  # gleiche Testteile wie bei der Code-Prüfung


def _sim(t, pp=None):
    res = k.generate(t, rahmen=True, pp=pp)
    assert res.ok, res.errors
    sim = DrehSimulation(t, res)
    sim.bis_ende()
    return sim


def _teile():
    out = {"welle": welle(), "beispiel": k.beispiel_teil()}
    t = welle(planaufmass=3.0)
    t.tool(1).ap = 0.7
    out["welle_ap07"] = t
    out["ohne_aufmass"] = welle(aufmass_x=0.0, aufmass_z=0.0, planaufmass=0.0)
    t = k.neues_teil()
    t.punkte = [KPunkt(0, 0), KPunkt(20, -15, "radius", 2), KPunkt(20, -25), KPunkt(32, -25)]
    out["spitze"] = t
    t = welle(planaufmass=0.0)
    t.punkte[1].op = "frei"
    out["stirn_frei"] = t
    t = welle()
    for p in t.punkte[5:]:
        p.op = "frei"
    out["ende_frei"] = t
    for t in out.values():
        t.fraesungen = []
    return out


@pytest.mark.parametrize("name", sorted(_teile()))
@pytest.mark.parametrize("werkzeuge", ["standard", "cnmg_dnmg", "wnmg_dcmt"])
def test_suitable_tools_give_no_collision(name, werkzeuge):
    t = _teile()[name]
    if werkzeuge == "cnmg_dnmg":
        t.tool(1).halter, t.tool(1).platte = "PCLNR 2525M12", "CNMG 120408-M"
        t.tool(2).halter, t.tool(2).platte = "PDJNR 2020K15", "DNMG 150404-F"
    elif werkzeuge == "wnmg_dcmt":
        t.tool(1).halter, t.tool(1).platte = "PWLNR 2020K08", "WNMG 080408-M"
        t.tool(2).halter, t.tool(2).platte = "SDJCR 2020K11", "DCMT 11T304-F"
    sim = _sim(t)
    schwer = [e.text for e in sim.ereignisse if e.schwer]
    assert not schwer, schwer[:3]
    assert sim.fertig and sim.zusammenfassung().startswith("✓")


def test_inner_corner_keeps_nose_radius():
    t = k.beispiel_teil()
    t.fraesungen = []
    sim = _sim(t)
    radius = [e for e in sim.ereignisse if e.art == "radius"]
    assert len(radius) == 1 and "r0.4" in radius[0].text and -30.1 < radius[0].z < -29.5
    # mit größerem Eckenradius bleibt mehr stehen, immer noch als Radius erkannt
    t.tool(2).platte = "VCMT 160408-M"
    sim = _sim(t)
    assert [e for e in sim.ereignisse if e.art == "radius"][0].text.count("r0.8") == 1


@pytest.mark.parametrize("halter,platte,art", [
    ("SVVBN 2020K16", "VBMT 160404-F", "kontur"),  # 72,5° neutral: nicht an Absätze heran
    ("PCLNL 2020K12", "CNMG 120408-M", "halter"),  # Linkshalter auf der falschen Seite
    ("SRDCN 2020K08", "RCMT 0803M0", "kontur"),  # Rundplatte macht keine scharfen Absätze
])
def test_unsuitable_finishing_tool_is_detected(halter, platte, art):
    t = k.beispiel_teil()
    t.fraesungen = []
    t.tool(2).halter, t.tool(2).platte = halter, platte
    sim = _sim(t)
    arten = {e.art for e in sim.ereignisse}
    assert art in arten and sim.zusammenfassung().startswith("✕")
    assert all(e.t == 2 for e in sim.ereignisse if e.schwer)


def test_45_degree_roughing_cuts_into_shoulder():
    t = k.beispiel_teil()
    t.fraesungen = []
    t.tool(1).halter, t.tool(1).platte = "PSSNR 2525M12", "SNMG 120408-M"
    sim = _sim(t)
    assert any(e.art == "kontur" and e.t == 1 for e in sim.ereignisse)


def test_rapid_into_material_is_detected():
    t = k.beispiel_teil()
    t.fraesungen = []
    res = k.generate(t, rahmen=True)
    # nach dem Schruppen absichtlich im Eilgang längs durch das Ø32-Rohteil
    i = max(n for n, m in enumerate(res.moves) if m.t == 1)
    last = res.moves[i]
    res.moves.insert(i + 1, Move("G0", last.z1, last.r1, last.z1, 14.0, t=1, line=last.line))
    res.moves.insert(i + 2, Move("G0", last.z1, 14.0, -35.0, 14.0, t=1, line=last.line))
    sim = DrehSimulation(t, res)
    sim.bis_ende()
    assert any(e.art == "eilgang" and e.t == 1 for e in sim.ereignisse)


def test_compensated_path_at_face_and_corner():
    """G42: Anfahren an X0 Z0, Planfläche hoch, Außenecke mit Kreisbogen, Zylinder."""
    run = [Move("G1", 2, 0, 0, 0, comp="G42"), Move("G1", 0, 0, 0, 10, comp="G42"),
           Move("G1", 0, 10, -10, 10, comp="G42")]
    path = [p for p, _ in kompensierte_bahn(run, 0.4, True)]
    assert path[0] == pytest.approx((0.4, 0.4))  # Innenecke Anfahren/Planfläche: Schnittpunkt
    assert (0.4, 10.0) == pytest.approx(path[1])
    arc = path[2:-2]
    assert all(abs(((z - 0) ** 2 + (r - 10) ** 2) ** 0.5 - 0.4) < 1e-9 for z, r in arc)
    assert path[-2] == pytest.approx((0.0, 10.4)) and path[-1] == pytest.approx((-10.0, 10.4))
    # G41 spiegelt die Seite
    left = [p for p, _ in kompensierte_bahn([Move("G1", 0, 0, -10, 0, comp="G41"),
                                              Move("G1", -10, 0, -20, 0, comp="G41")], 0.5, False)]
    assert left[-1] == pytest.approx((-20.0, -0.5))


def test_small_inner_radius_does_not_break_compensation():
    t = k.neues_teil()
    t.punkte = [KPunkt(0, 0), KPunkt(20, 0), KPunkt(20, -15, "radius", 0.2), KPunkt(30, -15), KPunkt(30, -25)]
    t.tool(2).platte = "VCMT 160408-M"  # Eckenradius 0.8 > Konturradius 0.2
    sim = _sim(t)
    assert not [e for e in sim.ereignisse if e.schwer]
    assert any(e.art == "radius" for e in sim.ereignisse)


def test_fanuc_program_simulates_the_same():
    t = k.beispiel_teil()
    t.fraesungen = []
    a, b = _sim(t), _sim(t, vorlage("fanuc"))
    assert [e.art for e in a.ereignisse] == [e.art for e in b.ereignisse]
    assert len(a.schritte) == len(b.schritte)


def test_jump_back_and_forth():
    t = k.beispiel_teil()
    t.fraesungen = []
    sim = _sim(t)
    end_material = list(sim.m)
    sim.gehe_zu(10)
    assert sim.i == 10 and not sim.fertig and sim.zeile >= 0
    form, s = sim.position
    assert form.t == 1
    sim.bis_ende()
    assert sim.m == end_material
    outline = sim.kontur_jetzt()
    assert outline[0][0] > outline[-1][0] and max(r for _, r in outline) <= 16.0 + 1e-9


def test_old_tools_without_catalog_get_defaults():
    t = k.beispiel_teil()
    t.fraesungen = []
    for w in t.werkzeuge:
        w.halter = w.platte = ""
    sim = _sim(t)
    assert any(e.art == "hinweis" and "VNMG" in e.text for e in sim.ereignisse)
    assert not [e for e in sim.ereignisse if e.schwer]
