"""Postprozessor: Vorlagen, Platzhalter, Zahlenformat, Speichern pro Maschine."""

from cnc_dnc.config import AppConfig, MachineProfile
from cnc_dnc.postpro import Postprozessor, fuer_maschine, program_number, vorlage


def test_placeholders_and_line_breaks():
    pp = Postprozessor()
    assert pp.fill("T{T2}D{D}", T=5, D=1) == ["T05D1"]
    assert pp.fill("T{T2}{D2}", T=12, D=3) == ["T1203"]
    assert pp.fill("S3={S} M3=3", S=2999.6) == ["S3=3000 M3=3"]
    assert pp.fill("SPOS=DC({W})", W=90) == ["SPOS=DC(90)"]
    assert pp.fill("%|O{NR} ({NAME})", NR="0815", NAME="WELLE") == ["%", "O0815 (WELLE)"]
    assert pp.fill("") == [] and pp.fill(" | ") == []
    assert pp.comment("Fräsen Ø8") == ";FRAESEN D8"


def test_number_format_with_and_without_decimal_point():
    sin, fan = vorlage("sin_s3"), vorlage("fanuc")
    assert [sin.num(v) for v in (20, -1.6, 0, 0.25)] == ["20", "-1.6", "0", "0.25"]
    assert [fan.num(v) for v in (20, -1.6, 0, 0.25)] == ["20.", "-1.6", "0.", "0.25"]
    assert fan.fill(fan.c_pos, W=180) == ["G00 C180."]
    assert fan.comment("Vordrehen") == "(VORDREHEN)"


def test_tool_lines_one_line_or_separate():
    sin = vorlage("sin_s3")
    assert sin.tool_lines(1, 1, sin.vorschub_u, sin.ebene_drehen, 2300) == ["T01D1 G95 G18 S2300 M3 M8"]
    assert sin.tool_lines(5, 1, sin.vorschub_min, sin.ebene_radial) == ["T05D1 G94 G19 M8"]
    fan = vorlage("fanuc")
    assert fan.tool_lines(1, 1, fan.vorschub_u, fan.ebene_drehen, 2300) == ["T0101", "G18 G99", "G97 S2300 M3",
                                                                            "M8"]


def test_presets():
    s4 = vorlage("sin_s4")
    assert s4.fill(s4.awz_ein, S=2500) == ["S4=2500 M4=3"] and s4.awz_aus == "M4=5"
    s3 = vorlage("sin_s3")
    assert s3.fill(s3.awz_ein, S=2500) == ["S3=2500 M3=3"] and s3.awz_aus == "M3=5"
    fan = vorlage("fanuc")
    assert fan.art == "fanuc" and fan.dezimalpunkt and fan.kreis == "R" and fan.wz == "T{T2}{D2}"
    for key in ("sin_s3", "sin_s4"):
        assert vorlage(key).pruefen() == ([], [])
    err, warn = fan.pruefen()
    assert not err and any("C-Achse ein" in w for w in warn)


def test_check_finds_broken_templates():
    pp = vorlage("sin_s3")
    pp.c_pos = "SPOS=0"
    pp.awz_ein = "M3=3"
    pp.wz = "WZ"
    err, _ = pp.pruefen()
    assert any("{W}" in e for e in err) and any("Angetriebenes" in e for e in err) and any("{T}" in e for e in err)
    assert not [e for e in pp.pruefen(mit_fraesen=False)[0] if "{W}" in e]  # ohne Fräsen egal
    fan = vorlage("fanuc")
    fan.dezimalpunkt = False
    assert any("Dezimalpunkt" in w for w in fan.pruefen()[1])


def test_from_dict_uses_preset_of_control_and_ignores_garbage():
    assert Postprozessor.from_dict({}, "fanuc").art == "fanuc"
    assert Postprozessor.from_dict({}, "sinumerik").awz_ein == "S3={S} M3=3"
    assert Postprozessor.from_dict(None, "heidenhain").art == "sinumerik"
    pp = Postprozessor.from_dict({"awz_ein": "S4={S} M4=3", "wechsel_x": "200", "dezimalpunkt": 1,
                                  "unbekannt": 5, "wechsel_z": "abc"}, "sinumerik")
    assert pp.awz_ein == "S4={S} M4=3" and pp.wechsel_x == 200.0 and pp.dezimalpunkt is True
    assert pp.wechsel_z == 130.0
    # gespeichertes Format geht vor die Steuerung der Maschine
    assert Postprozessor.from_dict({"art": "fanuc"}, "sinumerik").wz == "T{T2}{D2}"
    assert Postprozessor.from_dict(vorlage("sin_s4").to_dict()) == vorlage("sin_s4")


def test_saved_per_machine_in_config(tmp_path):
    cfg = AppConfig()
    cfg.path = str(tmp_path / "config.json")
    a = MachineProfile(name="Emco S3", control="sinumerik")
    b = MachineProfile(name="Emco S4", control="sinumerik", post=vorlage("sin_s4").to_dict())
    c = MachineProfile(name="Doosan", control="fanuc")
    cfg.machines = [a, b, c]
    cfg.save()
    again = AppConfig.load(cfg.path)
    pps = [fuer_maschine(m) for m in again.machines]
    assert [p.awz_ein for p in pps] == ["S3={S} M3=3", "S4={S} M4=3", "M13 S{S}"]
    # Kopie einer Maschine hat einen eigenen Postprozessor
    copy = again.machines[1].copy()
    copy.post["awz_ein"] = "S5={S} M5=3"
    assert again.machines[1].post["awz_ein"] == "S4={S} M4=3"
    # kaputter Wert in der Datei
    assert MachineProfile.from_dict({"name": "x", "post": "kaputt"}).post == {}


def test_program_number():
    assert program_number("O4711 Welle") == "4711"
    assert program_number("Teil 12") == "0012"
    assert program_number("WELLE") == "1000"
    assert program_number("0000") == "1000"
