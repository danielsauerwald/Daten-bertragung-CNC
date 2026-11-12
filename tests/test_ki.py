"""Bilder lesen (Claude): Anfrage, Auswertung, Zyklen – mit Schein-Client, ohne echte Anfragen."""

import io
import json
import os
from types import SimpleNamespace

import pytest

from cnc_dnc import ki
from cnc_dnc.config import AppConfig, MachineProfile
from cnc_dnc.postpro import vorlage
from cnc_dnc.zyklen import Zyklus, ZParam, aus_ki, aus_text, als_text


class FakeClient:
    """Nimmt die Anfrage entgegen und liefert eine feste Antwort (wie client.beta.messages.create)."""

    def __init__(self, data=None, stop_reason="end_turn", texte=None):
        self.kwargs = None
        self._data = data
        self._stop = stop_reason
        self._texte = texte
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.kwargs = kwargs
        texte = self._texte if self._texte is not None else [json.dumps(self._data)]
        content = [SimpleNamespace(type="thinking", thinking="")] + [SimpleNamespace(type="text", text=t)
                                                                     for t in texte]
        return SimpleNamespace(stop_reason=self._stop, content=content)


def _png(w=8, h=6) -> bytes:
    """Gültiges kleines PNG ohne Pillow."""
    import struct
    import zlib

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + b"\xff\xff\xff" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


PNG = _png()


def _bild(tmp_path, name="liste.png"):
    p = tmp_path / name
    p.write_bytes(PNG)
    return str(p)


def test_request_shape_and_answer(tmp_path):
    fake = FakeClient({"ok": True})
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"],
              "additionalProperties": False}
    data = ki.anfrage([ki.bild_laden(_bild(tmp_path))], "Lies das.", schema, client=fake)
    assert data == {"ok": True}
    kw = fake.kwargs
    assert kw["model"] == "claude-opus-5-5" and kw["max_tokens"] == 16000
    assert kw["betas"] == ["server-side-fallback-2026-07-01"] and kw["fallbacks"] == "default"
    assert kw["output_config"]["format"] == {"type": "json_schema", "schema": schema}
    assert kw["output_config"]["effort"] == "high" and "thinking" not in kw
    content = kw["messages"][0]["content"]
    assert content[0]["type"] == "image" and content[0]["source"]["type"] == "base64"
    assert content[-1] == {"type": "text", "text": "Lies das."}


def test_refusal_and_unreadable_answers(tmp_path):
    bild = [ki.bild_laden(_bild(tmp_path))]
    with pytest.raises(ki.KIFehler, match="abgelehnt"):
        ki.anfrage(bild, "x", {}, client=FakeClient({}, stop_reason="refusal"))
    with pytest.raises(ki.KIFehler, match="zu lang"):
        ki.anfrage(bild, "x", {}, client=FakeClient({}, stop_reason="max_tokens"))
    with pytest.raises(ki.KIFehler, match="nicht lesbar"):
        ki.anfrage(bild, "x", {}, client=FakeClient(texte=["kein json"]))
    # nach einem Ersatzmodell zählt die letzte gültige Antwort
    assert ki.anfrage(bild, "x", {}, client=FakeClient(texte=['{"a": 1}', '{"a": 2}'])) == {"a": 2}
    with pytest.raises(ki.KIFehler, match="Kein Bild"):
        ki.anfrage([], "x", {}, client=FakeClient({}))


def test_image_is_rotated_shrunk_and_jpeg(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    p = tmp_path / "foto.jpg"
    Image.new("RGB", (4000, 3000), "white").save(str(p), "JPEG")
    media, data = ki.bild_laden(str(p))
    im = Image.open(io.BytesIO(data))
    assert media == "image/jpeg" and max(im.size) <= ki.MAX_KANTE and im.size[0] * im.size[1] <= ki.MAX_PIXEL
    assert len(data) <= ki.MAX_BYTES
    with pytest.raises(ki.KIFehler):
        ki.bild_laden(str(tmp_path / "fehlt.jpg"))


ANTWORT_FANUC = {
    "steuerung": "Fanuc 0i-TF",
    "codes": [{"code": "M13", "bedeutung": "Angetriebenes Werkzeug Rechtslauf"},
              {"code": "M15", "bedeutung": "Angetriebenes Werkzeug Halt"},
              {"code": "M34", "bedeutung": "C-Achse ein"},
              {"code": "M35", "bedeutung": "C-Achse aus"},
              {"code": "M68", "bedeutung": "Spindelklemmung ein"}],
    "vorschlaege": [
        {"feld": "c_ein", "wert": "M34", "begruendung": "M34 – C-Achse ein", "sicher": True},
        {"feld": "c_aus", "wert": "M35", "begruendung": "M35 – C-Achse aus", "sicher": True},
        {"feld": "klemmen", "wert": "M68", "begruendung": "M68 – Spindelklemmung ein", "sicher": False},
        {"feld": "awz_ein", "wert": "M13", "begruendung": "M13 ohne Drehzahl", "sicher": True},
        {"feld": "awz_aus", "wert": "M15", "begruendung": "steht schon so drin", "sicher": True},
        {"feld": "dezimalpunkt", "wert": "ja", "begruendung": "Fanuc", "sicher": True},
        {"feld": "c_ein", "wert": "M99", "begruendung": "doppelt", "sicher": True},
    ],
    "hinweise": ["M68 nur mit Option Klemmung"],
}


def test_postprocessor_from_images(tmp_path):
    pp = vorlage("fanuc")
    fake = FakeClient(ANTWORT_FANUC)
    erg = ki.postpro_aus_bildern([_bild(tmp_path)], pp, "Fanuc / ISO", client=fake)
    aufgabe = fake.kwargs["messages"][0]["content"][-1]["text"]
    assert "Fanuc / ISO" in aufgabe and "awz_ein" in aufgabe and "{S}" in aufgabe
    schema = fake.kwargs["output_config"]["format"]["schema"]
    assert "c_ein" in schema["properties"]["vorschlaege"]["items"]["properties"]["feld"]["enum"]
    assert erg.steuerung == "Fanuc 0i-TF" and len(erg.codes) == 5 and erg.hinweise
    felder = [v.feld for v in erg.vorschlaege]
    # unverändert (awz_aus, dezimalpunkt) und doppelt (2. c_ein) fallen weg; Reihenfolge wie im Formular
    assert felder == ["c_ein", "klemmen", "c_aus", "awz_ein"]
    v = {x.feld: x for x in erg.vorschlaege}
    assert v["c_ein"].uebernehmen and v["c_ein"].alt == "" and v["c_ein"].neu == "M34"
    assert not v["klemmen"].uebernehmen  # unsicher
    assert v["awz_ein"].problem and not v["awz_ein"].uebernehmen  # ohne {S} geht es nicht
    neu = ki.postpro_anwenden(pp, list(erg.vorschlaege))
    assert (neu.c_ein, neu.c_aus, neu.klemmen) == ("M34", "M35", "M68")
    assert neu.awz_ein == pp.awz_ein  # Vorschlag mit Problem wird nie übernommen
    assert pp.c_ein == ""  # Original unverändert


def test_value_conversion():
    assert ki.wert_umwandeln("dezimalpunkt", "Ja") is True and ki.wert_umwandeln("eine_zeile", "nein") is False
    assert ki.wert_umwandeln("wechsel_x", "200,5") == 200.5
    assert ki.wert_umwandeln("art", "Siemens 840D") == "sinumerik" and ki.wert_umwandeln("art", "FANUC") == "fanuc"
    with pytest.raises(ValueError):
        ki.wert_umwandeln("wechsel_z", "weit weg")
    erg = ki.postpro_auswerten({"vorschlaege": [{"feld": "art", "wert": "Fanuc", "begruendung": "", "sicher": True},
                                                {"feld": "unbekannt", "wert": "x", "begruendung": "", "sicher": True}]},
                               vorlage("sin_s3"))
    assert [(v.feld, v.alt, v.neu) for v in erg.vorschlaege] == [("art", "Siemens Sinumerik", "Fanuc / ISO")]
    assert ki.postpro_anwenden(vorlage("sin_s3"), erg.vorschlaege).art == "fanuc"


# ---------------------------------------------------------------------------
# Zyklen
# ---------------------------------------------------------------------------


def test_siemens_cycle_keeps_empty_positions():
    z = Zyklus("CYCLE83", vorlage="CYCLE83({RTP},{RFP},{SDIS},{DP},{DPR},{FDEP},,{DAM})",
               parameter=[ZParam(n, pflicht=n in ("RTP", "DP")) for n in
                          ("RTP", "RFP", "SDIS", "DP", "DPR", "FDEP", "DAM")])
    assert z.code({"RTP": "10", "RFP": "0", "SDIS": "2", "DP": "-20,5"}) == "CYCLE83(10,0,2,-20.5)"
    assert z.code({"RTP": "10", "DP": "-5", "DAM": "1"}) == "CYCLE83(10,,,-5,,,,1)"
    assert z.pruefen({"RTP": "10"})[0] == ["DP (Pflichtwert) fehlt."]
    assert z.pruefen({"RTP": "10", "DP": "-5"}) == ([], [])


def test_fanuc_cycle_drops_empty_words_and_keeps_comments():
    z = Zyklus("G83", vorlage="G83 X{X} Z{Z} R{R} Q{Q} F{F} (TIEFBOHREN)|G80",
               parameter=[ZParam(n) for n in "XZRQF"])
    assert z.code({"Z": "-20.", "R": "2.", "Q": "3000", "F": "0.1"}) == "G83 Z-20. R2. Q3000 F0.1 (TIEFBOHREN)\nG80"
    z2 = Zyklus("G74", vorlage="G74 R{R}|G74 Z{Z} Q{Q} F{F}", parameter=[ZParam(n) for n in "RZQF"])
    assert z2.code({"R": "1", "Z": "-30", "F": "0.1"}) == "G74 R1\nG74 Z-30 F0.1"


def test_cycle_template_checks():
    z = Zyklus("X", vorlage="CYCLE1({A},{B})", parameter=[ZParam("A"), ZParam("C")])
    err, warn = z.pruefen()
    assert any("{B}" in e for e in err) and any("C" in w for w in warn)
    assert Zyklus("X", vorlage="", parameter=[]).pruefen()[0]
    assert any("doppelt" in e for e in Zyklus("X", vorlage="{A}", parameter=[ZParam("A"), ZParam("A")]).pruefen()[0])


def test_cycle_from_image_answer(tmp_path):
    antwort = {
        "name": "CYCLE83", "titel": "Tieflochbohren", "steuerung": "SINUMERIK 840D",
        "vorlage": "CYCLE83({RTP},{RFP},{SDIS},{DP},{DPR},{FDEP},{FDPR},{DAM},{DTB},{DTS},{FRF},{_VARI})",
        "parameter": [
            {"name": "RTP", "bedeutung": "Rückzugsebene", "einheit": "mm", "standard": "", "pflicht": True},
            {"name": "RFP", "bedeutung": "Referenzebene", "einheit": "mm", "standard": "0", "pflicht": True},
            {"name": "SDIS", "bedeutung": "Sicherheitsabstand", "einheit": "mm", "standard": "2", "pflicht": False},
            {"name": "DP", "bedeutung": "Endbohrtiefe", "einheit": "mm", "standard": "", "pflicht": True},
            {"name": "DPR", "bedeutung": "Tiefe relativ", "einheit": "mm", "standard": "", "pflicht": False},
            {"name": "FDEP", "bedeutung": "erste Bohrtiefe", "einheit": "mm", "standard": "", "pflicht": False},
            {"name": "FDPR", "bedeutung": "erste Tiefe relativ", "einheit": "mm", "standard": "", "pflicht": False},
            {"name": "DAM", "bedeutung": "Degression", "einheit": "mm", "standard": "", "pflicht": False},
            {"name": "DTB", "bedeutung": "Verweilzeit unten", "einheit": "s", "standard": "", "pflicht": False},
            {"name": "DTS", "bedeutung": "Verweilzeit oben", "einheit": "s", "standard": "", "pflicht": False},
            {"name": "FRF", "bedeutung": "Vorschubfaktor", "einheit": "", "standard": "1", "pflicht": False},
            {"name": "VARI", "bedeutung": "Bearbeitungsart 0=Spanbruch 1=Entspanen", "einheit": "",
             "standard": "1", "pflicht": False},
        ],
        "hinweise": ["_VARI im Bild ohne Unterstrich"],
    }
    fake = FakeClient(antwort)
    z = ki.zyklus_aus_bildern([_bild(tmp_path)], "Siemens Sinumerik", client=fake)
    assert "Siemens Sinumerik" in fake.kwargs["messages"][0]["content"][-1]["text"]
    assert fake.kwargs["output_config"]["format"]["schema"] is ki.ZYKLUS_SCHEMA
    assert (z.name, z.titel) == ("CYCLE83", "Tieflochbohren")
    # Platzhalter ohne Beschreibung (_VARI) wird als Parameter ergänzt, damit die Vorlage gültig ist
    assert "_VARI" in [p.name for p in z.parameter]
    assert z.pruefen()[0] == []
    assert z.werte == {"RFP": "0", "SDIS": "2", "FRF": "1", "VARI": "1"}
    werte = dict(z.werte, RTP="10", DP="-25", FDEP="-5", _VARI="1")
    assert z.code(werte) == "CYCLE83(10,0,2,-25,,-5,,,,,1,1)"


def test_cycle_names_are_cleaned():
    z = aus_ki({"name": "", "vorlage": "G76 P{P 1} Q{Q}", "parameter": [
        {"name": "Q", "bedeutung": "", "einheit": "", "standard": "", "pflicht": False}]})
    assert z.name == "ZYKLUS"
    # „{P 1}“ ist kein gültiger Platzhalter und bleibt Text; Q passt
    assert z.code({"Q": "50"}) == "G76 P{P 1} Q50"


def test_cycle_text_roundtrip_and_storage():
    z = Zyklus("G83", "Tiefbohren", vorlage="G83 Z{Z} Q{Q}", parameter=[
        ZParam("Z", "Tiefe", "mm", "", True), ZParam("Q", "Zustellung", "µm", "2000", False)])
    params = aus_text(als_text(z))
    assert [(p.name, p.bedeutung, p.einheit, p.standard, p.pflicht) for p in params] == \
        [("Z", "Tiefe", "mm", "", True), ("Q", "Zustellung", "µm", "2000", False)]
    z.werte = {"Z": "-10"}
    z2 = Zyklus.from_dict(json.loads(json.dumps(z.to_dict())))
    assert z2.to_dict() == z.to_dict()


def test_machine_keeps_codes_images_and_cycles(tmp_path):
    cfg = AppConfig()
    cfg.path = str(tmp_path / "config.json")
    m = cfg.machines[0]
    m.codes = [{"code": "M13", "bedeutung": "AWZ rechts"}]
    m.bilder = ["codes_1.jpg"]
    m.zyklen = [Zyklus("G83", vorlage="G83 Z{Z}", parameter=[ZParam("Z")]).to_dict()]
    cfg.ki_schluessel = "sk-test"
    cfg.save()
    cfg2 = AppConfig.load(cfg.path)
    m2 = cfg2.machines[0]
    assert m2.codes == m.codes and m2.bilder == m.bilder and m2.zyklen == m.zyklen
    assert cfg2.ki_schluessel == "sk-test"
    bad = MachineProfile.from_dict({"name": "X", "codes": "kaputt", "bilder": None, "zyklen": [1, {"name": "A"}]})
    assert bad.codes == [] and bad.bilder == [] and bad.zyklen == [{"name": "A"}]
    assert m.copy().zyklen == m.zyklen and m.copy().zyklen is not m.zyklen


def test_key_from_config_or_environment(tmp_path, monkeypatch):
    cfg = AppConfig()
    cfg.path = str(tmp_path / "config.json")
    ki.einrichten(cfg)
    try:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
        assert ki.schluessel() == "sk-env"
        ki.schluessel_speichern("  sk-own ")
        assert ki.schluessel() == "sk-own" and AppConfig.load(cfg.path).ki_schluessel == "sk-own"
        monkeypatch.delenv("ANTHROPIC_API_KEY")
        ki.schluessel_speichern("")
        assert ki.schluessel() == ""
        if ki.verfuegbar()[0]:
            with pytest.raises(ki.KIFehler, match="Kein API-Schlüssel"):
                ki.client_erzeugen()
    finally:
        ki.einrichten(None)


def test_store_image_with_machine(tmp_path):
    cfg = AppConfig()
    cfg.path = str(tmp_path / "config.json")
    ki.einrichten(cfg)
    try:
        name = ki.bild_ablegen("abc123", _bild(tmp_path, "M-Codes Seite 1.png"), "codes")
        pfad = os.path.join(ki.bilder_ordner("abc123"), name)
        assert os.path.isfile(pfad) and name.startswith("codes_") and "M-Codes_Seite_1" in name
        assert pfad.startswith(str(tmp_path))
    finally:
        ki.einrichten(None)


def test_self_test_against_local_server():
    pytest.importorskip("anthropic")
    pytest.importorskip("PIL")
    assert ki.selbsttest()
