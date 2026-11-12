"""Kontur-Assistent: Geometrie, Prüfungen und der erzeugte NC-Code.

Der Drehcode wird hier tatsächlich „abgefahren“: Ein einfaches Abtragsmodell (je Z-Spalte der
größte noch vorhandene Radius; ein Außendrehmeißel nimmt alles rechts von und über seiner
Spitze mit) prüft, dass

* kein Eilgang (G00) ins Material fährt,
* das Schruppen nicht in die Fertigkontur schneidet und Aufmaß stehen lässt,
* nach dem Schlichten genau die Kontur übrig bleibt.
"""

import math
import re

import pytest

from cnc_dnc import kontur as k
from cnc_dnc.kontur import Fraesung, KPunkt, Teil, Werkzeug
from cnc_dnc.postpro import vorlage

# --------------------------------------------------------------------------
# Hilfen: einfaches Abtragsmodell für Außendrehen
# --------------------------------------------------------------------------

DZ = 0.01


class Sim:
    def __init__(self, teil: Teil, z_min: float):
        self.z0 = z_min
        self.n = int(round((teil.planaufmass + 20 - z_min) / DZ))
        self.zs = [z_min + (i + 0.5) * DZ for i in range(self.n)]
        R = teil.rohteil_d / 2
        self.m = [R if z <= teil.planaufmass else -1.0 for z in self.zs]

    def index(self, z: float) -> int:
        return max(0, min(self.n, int(math.ceil((z - self.z0) / DZ - 0.5))))

    def segment(self, a, b, rapid: bool, part=None, part_tol=0.0, where=""):
        """Strecke a->b (z, r). Rückgabe: Liste von Fehlern."""
        (za, ra), (zb, rb) = a, b
        lo, hi = (a, b) if za <= zb else (b, a)
        errs = []
        r_lo_end = min(ra, rb) if abs(za - zb) < 1e-9 else lo[1]
        rmin = min(ra, rb)
        for i in range(self.index(lo[0] + 0.002), self.n):
            z = self.zs[i]
            if z <= hi[0] and abs(hi[0] - lo[0]) > 1e-9:
                rz = lo[1] + (hi[1] - lo[1]) * (z - lo[0]) / (hi[0] - lo[0])
                clamp = min(r_lo_end, rz)
            else:
                clamp = rmin
            if rapid:
                if self.m[i] > clamp + 0.003:
                    errs.append("Eilgang ins Material bei Z{:.3f} (Material r={:.3f}, Werkzeug r={:.3f}) {}".format(
                        z, self.m[i], clamp, where))
                    break
            else:
                if part is not None and clamp < part[i] + part_tol - 0.003:
                    errs.append("Kontur verletzt bei Z{:.3f}: Werkzeug r={:.3f}, Kontur r={:.3f} {}".format(
                        z, clamp, part[i], where))
                    break
                if clamp < self.m[i]:
                    self.m[i] = clamp
        return errs


def part_profile(teil: Teil, sim: Sim):
    geom = k.build_geometry(teil)
    pl = k.polyline(geom.elems, 0.5)
    prof = []
    for z in sim.zs:
        if z > 0:
            prof.append(-1.0)
            continue
        best = None
        for (za, ra), (zb, rb) in zip(pl, pl[1:]):
            lo, hi = min(za, zb), max(za, zb)
            if lo - 1e-9 <= z <= hi + 1e-9 and hi - lo > 1e-9:
                r = ra + (rb - ra) * (z - za) / (zb - za)
                best = r if best is None else max(best, r)
        prof.append(teil.rohteil_d / 2 if best is None else best)
    return prof


_WORD = re.compile(r"(CR=|[XZR])(-?[\d.]+)")


def turning_moves(code: str):
    """(Art, Start, Ende, Zeile) für alle Bewegungen in Dreh-Arbeitsgängen (Vorschub je Umdrehung).

    Versteht Sinumerik (T01D1 G95 … , CR=) und Fanuc (T0101 / G99, R, Dezimalpunkt).
    """
    moves = []
    turning = False
    pos = None
    modal = "G00"
    for line in code.split("\n"):
        s = line.strip()
        if not s or s.startswith(";") or s.startswith("("):
            continue
        if re.match(r"T\d+", s):
            pos = None
        if re.search(r"\bG9[59]\b", s):
            turning = True
        if re.search(r"\bG9[48]\b", s):
            turning = False
        if re.match(r"T\d+", s) or not turning:
            continue
        g = re.search(r"\bG0?([0-3])\b", s)
        if g:
            modal = "G0" + g.group(1)
        vals = dict((m.group(1), float(m.group(2))) for m in _WORD.finditer(s))
        if "X" not in vals and "Z" not in vals:
            continue
        if pos is None:
            pos = (vals.get("Z", 130.0), vals.get("X", 150.0) / 2)
            moves.append(("G00", (130.0, 75.0), pos, s))
            continue
        new = (vals.get("Z", pos[0]), vals.get("X", 2 * pos[1]) / 2)
        if modal in ("G02", "G03"):
            moves.extend(_arc_pieces(modal, pos, new, vals.get("CR=", vals.get("R")), s))
        else:
            moves.append((modal, pos, new, s))
        pos = new
    return moves


def _arc_pieces(modal, a, b, radius, line):
    (za, ra), (zb, rb) = a, b
    mz, mr = (za + zb) / 2, (ra + rb) / 2
    dz, dr = zb - za, rb - ra
    chord = math.hypot(dz, dr)
    h = math.sqrt(max(0.0, radius * radius - chord * chord / 4))
    nz, nr = -dr / chord, dz / chord  # links der Sehne
    sign = 1 if modal == "G03" else -1
    cz, cr = mz + sign * h * nz, mr + sign * h * nr
    a0 = math.atan2(ra - cr, za - cz)
    a1 = math.atan2(rb - cr, zb - cz)
    da = a1 - a0
    if modal == "G03" and da < 0:
        da += 2 * math.pi
    if modal == "G02" and da > 0:
        da -= 2 * math.pi
    pts = [(cz + radius * math.cos(a0 + da * i / 20), cr + radius * math.sin(a0 + da * i / 20)) for i in range(21)]
    pts[0], pts[-1] = a, b
    return [("G01", p, q, line) for p, q in zip(pts, pts[1:])]


def check_turning(teil: Teil, code: str):
    """Abtrag simulieren. Gibt (Fehler, Sim, Profil) zurück."""
    z_min = min(p.z for p in teil.punkte) - 5
    sim = Sim(teil, z_min)
    prof = part_profile(teil, sim)
    errs = []
    ax2, az = teil.aufmass_x / 2, teil.aufmass_z
    rough_tol = min(ax2, az) * 0.5
    for kind, a, b, line in turning_moves(code):
        errs += sim.segment(a, b, kind == "G00", prof, 0.0, "[{}]".format(line))
    # Schruppen getrennt prüfen: darf nicht näher als das halbe Aufmaß an die Kontur
    sim2 = Sim(teil, z_min)
    rough_code = re.split(r"[;(]FERTIGDREHEN", code)[0]
    for kind, a, b, line in turning_moves(rough_code):
        if kind != "G00":
            errs += sim2.segment(a, b, False, prof, rough_tol, "[Schruppen: {}]".format(line))
    return errs, sim, prof


def assert_contour_finished(teil, sim, prof, z_from, z_to):
    """Zwischen z_from (rechts) und z_to (links) muss genau die Kontur übrig sein."""
    bad = []
    for i, z in enumerate(sim.zs):
        if not (z_to + 0.05 < z < z_from - 0.05):
            continue
        # Sprungstellen (Absätze) auslassen
        if abs(prof[max(0, i - 3)] - prof[min(len(prof) - 1, i + 3)]) > 0.05:
            continue
        if abs(sim.m[i] - prof[i]) > 0.01:
            bad.append((round(z, 3), round(sim.m[i], 3), round(prof[i], 3)))
    assert not bad, "Material bleibt stehen/fehlt: {}".format(bad[:5])


def welle(**kw) -> Teil:
    t = k.neues_teil()
    t.name = "WELLE"
    t.rohteil_d = 40
    t.planaufmass = 1.5
    t.punkte = [
        KPunkt(0, 0),
        KPunkt(16, 0, "fase", 1),
        KPunkt(16, -12, "radius", 2),
        KPunkt(24, -12, "entgraten"),
        KPunkt(24, -20),
        KPunkt(30, -28, "radius", 3),  # Kegel
        KPunkt(30, -40, "fase", 0.5),
        KPunkt(36, -40),
        KPunkt(36, -46),
    ]
    for key, v in kw.items():
        setattr(t, key, v)
    return t


# --------------------------------------------------------------------------
# Eingaben / Formatierung
# --------------------------------------------------------------------------


def test_fmt_and_parsing():
    assert k.fmt(20) == "20" and k.fmt(-0.0001) == "0" and k.fmt(1.23456) == "1.235" and k.fmt(-14.9) == "-14.9"
    assert k.parse_num("0,5") == 0.5 and k.parse_num(" 12.75 ") == 12.75
    with pytest.raises(ValueError):
        k.parse_num("")
    assert k.parse_winkel("0 180") == [0, 180]
    assert k.parse_winkel("0, 120, 240") == [0, 120, 240]
    assert k.parse_winkel("0;90;180;270;360") == [0, 90, 180, 270]
    assert k.parse_winkel("-90 22.5") == [270, 22.5]
    with pytest.raises(ValueError):
        k.parse_winkel("  ")
    assert k.gleich_verteilt(3, 30) == [30, 150, 270]
    assert k.nc_text("Fräser Ø8 – Fläche ä") == "FRAESER D8 FLAECHE AE"
    assert k.nc_name("1. Welle/Ø20") == "T1_WELLE_D20"


def test_teil_roundtrip_through_dict():
    t = k.beispiel_teil()
    t2 = Teil.from_dict(t.to_dict())
    assert t2 == t
    # kaputte / alte Daten: Vorgaben statt Absturz
    t3 = Teil.from_dict({"rohteil_d": "abc", "punkte": [{"x": 5, "z": 3}], "fraesungen": [{"winkel": ["x"]}]})
    assert t3.rohteil_d == 32 and t3.punkte[0].x == 0 and t3.punkte[0].z == 0
    assert t3.fraesungen[0].winkel == [0.0]


# --------------------------------------------------------------------------
# Geometrie
# --------------------------------------------------------------------------


def test_geometry_chamfer_and_radius_directions():
    g = k.build_geometry(welle())
    assert not g.errors, g.errors
    arcs = [e for e in g.elems if e.arc]
    # Hohlkehle (Zylinder -> Absatz nach oben) = G02, Außenradius (Kegel -> Zylinder) = G03?
    hohlkehle = [e for e in arcs if e.corner == 2][0]
    assert not hohlkehle.ccw
    kegel = [e for e in arcs if e.corner == 5][0]
    # Kegel steigt, danach Zylinder: Richtungswechsel nach links -> konvex -> G03
    assert kegel.ccw
    fase = [e for e in g.elems if e.corner == 1][0]
    assert (fase.z0, fase.r0, fase.z1, fase.r1) == (0, 7, -1, 8)
    # Radius 2 an 90°-Ecke: Tangentenlänge 2
    assert hohlkehle.z0 == pytest.approx(-10) and hohlkehle.r1 == pytest.approx(10)


@pytest.mark.parametrize("punkte,msg", [
    ([KPunkt(0, 0), KPunkt(20, 0), KPunkt(20, -10), KPunkt(16, -10), KPunkt(16, -20)], "nicht kleiner werden"),
    ([KPunkt(0, 0), KPunkt(20, 0), KPunkt(20, 5)], "nach links verlaufen"),
    ([KPunkt(0, 0), KPunkt(50, 0)], "größer als das Rohteil"),
    ([KPunkt(0, 0), KPunkt(20, 0), KPunkt(20, 0)], "liegt auf Punkt"),
    ([KPunkt(0, 0), KPunkt(20, 0, "fase", 15), KPunkt(20, -10)], "passt nicht"),
    ([KPunkt(0, 0), KPunkt(0, -5)], "Mittelachse"),
    ([KPunkt(0, 0), KPunkt(20, 0), KPunkt(20, -10, op="frei"), KPunkt(30, -10)], "Nicht drehen"),
    ([KPunkt(0, 0), KPunkt(20, 0, op="frei"), KPunkt(20, -10, op="frei"), KPunkt(30, -10)], "Stirnfläche"),
    ([KPunkt(0, 0), KPunkt(20, 0, "radius", 0), KPunkt(20, -10)], "Größe für Radius fehlt"),
])
def test_geometry_rejects_bad_contours(punkte, msg):
    t = k.neues_teil()
    t.punkte = punkte
    res = k.generate(t)
    assert not res.ok and any(msg in e for e in res.errors), res.errors


def test_contour_radius():
    t = welle()
    g = k.build_geometry(t)
    assert k.contour_radius(t, g, -5, -3) == pytest.approx(8)
    assert k.contour_radius(t, g, -15, -13) == pytest.approx(12)
    assert k.contour_radius(t, g, -24, -24) == pytest.approx(13.5)  # Mitte Kegel
    assert k.contour_radius(t, g, -60, -50) == pytest.approx(20)  # hinter der Kontur: Rohteil
    assert k.contour_radius(t, g, 5, 10) == 0


# --------------------------------------------------------------------------
# Drehen: Code abfahren
# --------------------------------------------------------------------------


@pytest.mark.parametrize("post", ["sin_s3", "fanuc"])
@pytest.mark.parametrize("variante", ["welle", "welle_ap07", "ohne_aufmass", "beispiel", "spitze",
                                      "stirn_frei", "ende_frei", "nur_zylinder"])
def test_turning_is_safe_and_complete(variante, post):
    if variante == "welle":
        t = welle()
    elif variante == "welle_ap07":
        t = welle(planaufmass=3.0)
        t.tool(1).ap = 0.7
    elif variante == "ohne_aufmass":
        t = welle(aufmass_x=0.0, aufmass_z=0.0, planaufmass=0.0)
    elif variante == "beispiel":
        t = k.beispiel_teil()
        t.fraesungen = []
    elif variante == "spitze":  # Kegelspitze ohne Stirnfläche
        t = k.neues_teil()
        t.punkte = [KPunkt(0, 0), KPunkt(20, -15, "radius", 2), KPunkt(20, -25), KPunkt(32, -25)]
    elif variante == "stirn_frei":
        t = welle(planaufmass=0.0)
        t.punkte[1].op = "frei"
    elif variante == "ende_frei":  # nur bis Ø24 drehen, Rest bleibt Rohteil
        t = welle()
        for p in t.punkte[5:]:
            p.op = "frei"
    else:
        t = k.neues_teil()
        t.punkte = [KPunkt(0, 0), KPunkt(30, 0), KPunkt(30, -20)]
    res = k.generate(t, rahmen=True, pp=vorlage(post))
    assert res.ok, res.errors
    errs, sim, prof = check_turning(t, res.code)
    assert not errs, errs[:5]
    geom = k.build_geometry(t)
    elems = geom.turn_elems()
    z_end = elems[-1].z1
    assert_contour_finished(t, sim, prof, 0.0, z_end)
    # Stirnseite: vor Z0 bleibt nichts stehen (außer bei „Stirnfläche nicht drehen“)
    if t.punkte[1].op != "frei":
        assert all(m <= 0.003 for z, m in zip(sim.zs, sim.m) if z > 0.003)


def test_simulation_detects_mistakes():
    """Gegenprobe: Das Abtragsmodell meldet Eilgang ins Material und Konturverletzung."""
    t = welle()
    code = k.generate(t).code
    # Eilgang auf Ø30 längs ins volle Material
    bad = code.replace(";FERTIGDREHEN", "G00 X30\nG00 Z-30\n;FERTIGDREHEN", 1)
    errs, _, _ = check_turning(t, bad)
    assert any("Eilgang ins Material" in e for e in errs)
    # Schlichtschnitt 0.5 mm zu tief
    bad = code.replace("G01 Z-20\n", "G01 X23 Z-20\n", 1)
    assert bad != code
    errs, _, _ = check_turning(t, bad)
    assert any("Kontur verletzt" in e for e in errs)


def test_turning_code_style():
    res = k.generate(welle())
    code = res.code
    assert ";VORDREHEN VNMG08\nT01D1 G95 G18 S2300 M3 M8\n" in code
    assert ";FERTIGDREHEN VCMT04\nT02D1 G95 G18 S2500 M3 M8\n" in code
    assert "G42 G01 Z0 F0.1" in code and "G40 G00 X150" in code
    assert "G02 X20 Z-12 CR=2" in code  # Hohlkehle
    assert re.search(r"G03 X30 Z-\d+(\.\d+)? CR=3", code)
    # Planen in zwei Schnitten (1.5 mm Aufmaß, Zustellung 1.5, Schlichtaufmaß 0.1)
    assert code.count("G01 X-1.6") == 1
    # nur ASCII, keine Umlaute
    assert all(ord(ch) < 128 for ch in code)
    # jede Zeile höchstens einmal je Achse
    for line in code.split("\n"):
        for axis in "XYZ":
            assert len(re.findall(r"(?<![A-Z])" + axis + r"-?\d", line)) <= 1, line


def test_program_frame_and_tools_for_setup_sheet():
    from cnc_dnc.setupsheet import extract_tools
    from cnc_dnc.textutil import analyze_program

    res = k.generate(k.beispiel_teil(), rahmen=True)
    assert res.ok, res.errors
    lines = res.code.split("\n")
    assert lines[0] == "%_N_BEISPIEL_MPF" and lines[1] == ";$PATH=/_N_MPF_DIR"
    assert res.code.rstrip().endswith("M30")
    info = analyze_program(res.code)
    assert info.name == "BEISPIEL" and info.dialect == "sinumerik"
    tools = extract_tools(res.code)
    assert [t.tool for t in tools] == ["T1", "T2", "T5", "T6", "T7", "T7"]
    assert tools[0].description == "VORDREHEN VNMG08"
    assert tools[2].description.startswith("FRAESEN FLAECHE SW17")


# --------------------------------------------------------------------------
# Fräsen auf Winkellagen
# --------------------------------------------------------------------------


def _block(code: str, title: str) -> str:
    """Arbeitsgang ab der Kommentarzeile mit ``title`` bis zur nächsten Leerzeile."""
    i = code.rfind("\n", 0, code.index(title)) + 1
    j = code.find("\n\n", i)
    return code[i:] if j < 0 else code[i:j]


def _vals(block: str, axis: str):
    code = "\n".join(ln for ln in block.split("\n") if not ln.startswith(";"))
    return [float(v) for v in re.findall(r"(?<![A-Z])" + axis + r"(-?[\d.]+)", code)]


def test_flat_two_sides():
    t = k.beispiel_teil()
    t.fraesungen = [Fraesung("flaeche", [0, 180], 0, -10, 17, 0, 0, 5)]
    res = k.generate(t)
    assert res.ok, res.errors
    b = _block(res.code, "FRAESEN FLAECHE SW17")
    assert "T05D1 G94 G19 M8" in b
    assert b.count("SPOS=DC(0)") == 1 and b.count("SPOS=DC(180)") == 1
    assert b.index("SPOS=DC(0)") < b.index("S3=3000 M3=3") < b.index("SPOS=DC(180)")
    assert min(_vals(b, "X")) == pytest.approx(17)  # Fläche bei 8.5 von der Mitte
    w2 = math.sqrt(10 ** 2 - 8.5 ** 2)
    assert max(_vals(b, "Y")) == pytest.approx(w2 + 4 + 1, abs=0.002)
    zs = _vals(b, "Z")
    assert max(zs[:-1]) <= 1 - 4 + 1e-6 and min(zs) == pytest.approx(-6)  # Fräser Ø8: Z-10+4
    assert "M3=5" in b and b.rstrip().endswith("G18")
    # Eintauchen nur außerhalb des Teils (Y außen)
    y = None
    for line in b.split("\n"):
        ys = _vals(line, "Y")
        if ys:
            y = ys[-1]
        if line.startswith("G01 X"):
            assert abs(y) > w2 + 4, line


def test_flat_too_short_for_cutter():
    t = k.beispiel_teil()
    t.fraesungen = [Fraesung("flaeche", [0], -2, -8, 17, 0, 0, 5)]  # 6 mm lang, Fräser Ø8
    res = k.generate(t)
    assert not res.ok and "zu groß für die Flächenlänge" in res.errors[0]


def test_slot_wide_three_positions_and_deburr():
    t = k.beispiel_teil()
    t.fraesungen = [Fraesung("nut", [0, 120, 240], -16, -28, 10, 2, 0, 5, True, 7, 0.3)]
    # Nut endet 2 mm vor dem Absatz Ø32: der Kegel des Fasenfräsers Ø10 käme daran
    res = k.generate(t)
    assert not res.ok and "käme an einen größeren Durchmesser" in res.errors[0]
    t.fraesungen[0].z2 = -25
    res = k.generate(t)
    assert res.ok, res.errors
    b = _block(res.code, "FRAESEN NUT 10X2")
    assert [b.count("SPOS=DC({})".format(a)) for a in (0, 120, 240)] == [1, 1, 1]
    assert min(_vals(b, "X")) == pytest.approx(22)  # Ø26 - 2*2
    ys = _vals(b, "Y")
    assert max(ys) == pytest.approx(1) and min(ys) == pytest.approx(-1)  # (10-8)/2
    cut = "\n".join(ln for ln in b.split("\n") if ln.startswith("G01"))
    assert min(_vals(cut, "Z")) == pytest.approx(-21) and max(_vals(cut, "Z")) == pytest.approx(-20)
    d = _block(res.code, "ENTGRATEN NUT 10X2")
    assert "T07D1 G94 G19 M8" in d
    assert min(_vals(d, "X")) == pytest.approx(2 * (12 - 0.3))  # Kante bei y=5 auf Ø26
    cut = "\n".join(ln for ln in d.split("\n") if ln.startswith("G01"))
    assert min(_vals(cut, "Z")) == pytest.approx(-25) and max(_vals(cut, "Z")) == pytest.approx(-16)


def test_slot_errors():
    t = k.beispiel_teil()
    t.fraesungen = [Fraesung("nut", [0], -16, -28, 6, 2, 0, 5)]
    assert "breiter als die Nut" in k.generate(t).errors[0]
    t.fraesungen = [Fraesung("nut", [0], -16, -28, 10, 20, 0, 5)]
    assert "Tiefe" in k.generate(t).errors[0]


def test_radial_hole_peck_and_countersink():
    t = k.beispiel_teil()
    t.fraesungen = [Fraesung("bohrung_radial", [90], -22.5, 0, 5, 6, 0, 6, True, 7, 0.3)]
    res = k.generate(t)
    assert res.ok, res.errors
    b = _block(res.code, "BOHREN D5 RADIAL")
    assert "SPOS=DC(90)" in b and "G01 X20 F150" in b and "G01 X14" in b
    d = _block(res.code, "ENTGRATEN BOHRUNG D5")
    assert "G01 X20.4 F400" in d  # 2*(13 - 2.5 - 0.3)


def test_face_holes_on_bolt_circle_need_axial_tool():
    t = k.beispiel_teil()
    t.fraesungen = [Fraesung("bohrung_axial", k.gleich_verteilt(4, 45), 0, 0, 5, 8, 12, 6)]
    res = k.generate(t)
    assert not res.ok and "radial eingebaut" in res.errors[0]
    t.fraesungen[0].wz = 8
    res = k.generate(t)
    assert res.ok, res.errors
    b = _block(res.code, "BOHREN D5 STIRN")
    assert "T08D1 G94 G17 M8" in b
    assert [b.count("SPOS=DC({})".format(a)) for a in (45, 135, 225, 315)] == [1, 1, 1, 1]
    assert "G00 X12 Y0" in b and min(_vals(b, "Z")) == pytest.approx(-8)
    # erst Z weg, dann X (Werkzeug steht vor der Stirnseite)
    assert b.index("G00 Z130") < b.index("G00 X150")


def test_wrong_tool_type_and_missing_tool():
    t = k.beispiel_teil()
    t.fraesungen = [Fraesung("flaeche", [0], 0, -10, 17, 0, 0, 6)]  # Bohrer statt Fräser
    assert "hier wird ein Fräser gebraucht" in k.generate(t).errors[0]
    t.fraesungen = [Fraesung("flaeche", [0], 0, -10, 17, 0, 0, 42)]
    assert "T42 steht nicht in der Werkzeugliste" in k.generate(t).errors[0]


def test_postprocessor_s4_clamping_and_own_positioning():
    t = k.beispiel_teil()
    pp = vorlage("sin_s4")
    pp.c_pos = "C=DC({W})"
    pp.klemmen = "M10"
    pp.loesen = "M11"
    res = k.generate(t, pp=pp)
    assert res.ok, res.errors
    b = _block(res.code, "FRAESEN FLAECHE SW17")
    assert "C=DC(0)\nM10\nS4=3000 M4=3" in b and "M11\nC=DC(180)\nM10" in b and "M4=5" in b
    assert "S3=" not in res.code and "M3=" not in res.code
    pp.c_pos = "SPOS=0"
    assert "{W}" in k.generate(t, pp=pp).errors[0]


def test_fanuc_output():
    from cnc_dnc.setupsheet import extract_tools
    from cnc_dnc.textutil import analyze_program

    pp = vorlage("fanuc")
    t = k.beispiel_teil()
    t.name = "O4711 Welle"
    res = k.generate(t, rahmen=True, pp=pp, maschine="3: Doosan")
    assert res.ok, res.errors
    assert any("C-Achse ein" in w for w in res.warnings)  # Vorlage: M-Befehl der Maschine fehlt noch
    lines = res.code.rstrip("\n").split("\n")
    assert lines[:3] == ["%", "O4711 (O4711_WELLE)", "(KONTUR-ASSISTENT TEIL O4711_WELLE)"]
    assert "(MASCHINE 3: DOOSAN)" in lines and lines[-2:] == ["M30", "%"]
    assert "(VORDREHEN VNMG08)\nT0101\nG18 G99\nG97 S2300 M3\nM8\n" in res.code
    assert "G02 X22. Z-15. R1." in res.code and "G42 G01 Z0. F0.1" in res.code
    b = _block(res.code, "FRAESEN FLAECHE SW17")
    assert "T0501\nG19 G98\nM8\nM5\nG00 C0.\nM13 S3000\n" in b
    assert "G00 C180." in b and "M15\nM9\nG00 X150.\nG00 Z130.\nG18" in b
    assert "SPOS" not in res.code and ";" not in res.code
    # Fanuc: jede Koordinate und jeder Vorschub mit Dezimalpunkt (sonst µm!)
    for ln in lines:
        if not ln.startswith("("):
            assert not re.search(r"(?<![A-Z])[XYZCFR]-?\d+(?![\d.])", ln), ln
    assert analyze_program(res.code).dialect == "fanuc"
    tools = extract_tools(res.code)
    assert tools[0].description == "VORDREHEN VNMG08" and tools[2].description.startswith("FRAESEN FLAECHE")
    # mit eingetragenen C-Achs-Befehlen keine Warnung mehr
    pp.c_ein, pp.c_aus = "M35", "M34"
    res = k.generate(t, pp=pp)
    assert not res.warnings
    b = _block(res.code, "FRAESEN FLAECHE SW17")
    assert "M5\nM35\nG00 C0." in b and "M15\nM9\nM34\nG00 X150." in b


def test_postprocessor_change_point_checked():
    pp = vorlage("sin_s3")
    pp.wechsel_x = 20
    assert "liegt nicht außerhalb des Rohteils" in k.generate(k.beispiel_teil(), pp=pp).errors[0]


def test_only_milling_without_turning():
    t = k.beispiel_teil()
    for p in t.punkte[1:]:
        p.op = "frei"
    res = k.generate(t)
    assert res.ok, res.errors
    assert "VORDREHEN" not in res.code and "FERTIGDREHEN" not in res.code and "FRAESEN FLAECHE" in res.code
    t.fraesungen = []
    assert "nichts zu bearbeiten" in k.generate(t).errors[0]


def test_deburr_refused_next_to_bigger_shoulder():
    """Der Kegel des Fasenfräsers würde in den Absatz fahren -> ablehnen statt Kollision."""
    t = k.beispiel_teil()
    # Fläche auf Ø20 endet direkt am Absatz Ø26 (Z-15), Fasenfräser Ø10
    t.fraesungen = [Fraesung("flaeche", [0], 0, -15, 17, 0, 0, 5, True, 7, 0.3)]
    res = k.generate(t)
    assert not res.ok and "größeren Durchmesser" in res.errors[0]
    # ohne Entgraten ist dieselbe Fläche in Ordnung (Fräser endet genau am Absatz)
    t.fraesungen[0].entgraten = False
    res = k.generate(t)
    assert res.ok, res.errors
    b = _block(res.code, "FRAESEN FLAECHE SW17")
    cut = "\n".join(ln for ln in b.split("\n") if ln.startswith("G01"))
    assert min(_vals(cut, "Z")) == pytest.approx(-15 + 4)
    # genug Abstand (5 mm) -> Entgraten erlaubt
    t.fraesungen = [Fraesung("flaeche", [0], 0, -9, 17, 0, 0, 5, True, 7, 0.3)]
    assert k.generate(t).ok
    # Querbohrung: Senker käme an den Absatz
    t.fraesungen = [Fraesung("bohrung_radial", [0], -12, 0, 3, 3, 0, 6, True, 7, 0.3)]
    assert "größeren Durchmesser" in k.generate(t).errors[0]
    # ohne Entgraten geht sie, Tiefe ab Ø20 in der Bohrungsachse
    t.fraesungen[0].entgraten = False
    res = k.generate(t)
    assert res.ok and "G01 X14" in _block(res.code, "BOHREN D3 RADIAL")
    # Bohrer würde den Absatz anschneiden
    t.fraesungen = [Fraesung("bohrung_radial", [0], -13.5, 0, 4, 3, 0, 6)]
    assert "zu nah an einem Absatz" in k.generate(t).errors[0]


def test_face_holes_must_sit_on_a_reachable_face():
    t = k.beispiel_teil()
    t.fraesungen = [Fraesung("bohrung_axial", [0], 0, 0, 5, 8, 18, 8)]  # Ø18+5 > Stirnfläche Ø18
    assert "nicht auf der Planfläche" in k.generate(t).errors[0]
    # Planfläche Ø20 -> Ø30 bei Z-15
    t.punkte = [KPunkt(0, 0), KPunkt(20, 0), KPunkt(20, -15), KPunkt(30, -15), KPunkt(30, -25)]
    t.werkzeuge.append(Werkzeug(9, 1, "BOHRER D2", "bohrer", "axial", 2, 4000, 80, 0))
    t.fraesungen = [Fraesung("bohrung_axial", [0, 180], -15, 0, 2, 3, 25, 9)]
    res = k.generate(t)
    assert res.ok, res.errors
    b = _block(res.code, "BOHREN D2 STIRN")
    assert "G00 X25 Y0" in b and "G00 Z-13" in b and "G01 Z-18 F80" in b
    # mit dem Ø5-Bohrer käme man auf dem Weg dorthin an Ø20
    t.fraesungen = [Fraesung("bohrung_axial", [0], -15, 0, 5, 3, 24, 8)]
    assert "davor liegenden Durchmesser" in k.generate(t).errors[0]
