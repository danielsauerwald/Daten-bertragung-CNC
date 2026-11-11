import itertools

import pytest

from cnc_dnc.textutil import (
    EndDetector,
    LineEndingNormalizer,
    build_send_chunks,
    detect_program_name,
    find_problem_chars,
    normalize_text,
    prepare_lines,
    safe_filename,
)

PROGRAM = ["%", "O1234 (FLANSCH)", "N10 G90 G54", "N20 G0 X10. Y5.", "", "N30 M30", "%"]


@pytest.mark.parametrize("eol", ["\n", "\r\n", "\n\r\r", "\r\r\n", "\r"])
def test_normalize_all_line_endings(eol):
    raw = eol.join(PROGRAM) + eol
    assert normalize_text(raw) == "\n".join(PROGRAM) + "\n"


@pytest.mark.parametrize("eol", ["\n", "\r\n", "\n\r\r", "\r\r\n", "\r"])
def test_normalize_survives_any_chunking(eol):
    """Zeilenenden, die über Blockgrenzen verteilt ankommen, dürfen nichts verschieben."""
    raw = "\x00\x00\x00" + eol.join(PROGRAM) + eol
    expected = "\n".join(PROGRAM) + "\n"
    # alle Aufteilungen in drei Teile
    for a, b in itertools.combinations(range(1, len(raw)), 2):
        n = LineEndingNormalizer()
        out = n.feed(raw[:a]) + n.feed(raw[a:b]) + n.feed(raw[b:]) + n.flush()
        assert out == expected, (a, b)
    # Zeichen für Zeichen
    n = LineEndingNormalizer()
    out = "".join(n.feed(ch) for ch in raw) + n.flush()
    assert out == expected


def test_control_chars_removed():
    raw = "\x00\x00\x11%\r\nO1\x13\r\nG0\tX1\x7f\r\n"
    assert normalize_text(raw) == "%\nO1\nG0\tX1\n"


def test_prepare_lines_frames_percent():
    assert prepare_lines("O1\nM30\n", frame_percent=True) == ["%", "O1", "M30", "%"]
    # schon vorhanden -> nicht doppelt
    assert prepare_lines("%\nO1\nM30\n%\n\n\n", frame_percent=True) == ["%", "O1", "M30", "%"]
    assert prepare_lines("\n\nO1 \n\nM30", strip_trailing_spaces=True, skip_empty_lines=True) == ["O1", "M30"]


def test_build_send_chunks():
    chunks = build_send_chunks("O1\nM30", eol="CRLF", frame_percent=True, leader_nuls=3, trailer_nuls=2)
    assert chunks == [b"\x00\x00\x00", b"%\r\n", b"O1\r\n", b"M30\r\n", b"%\r\n", b"\x00\x00"]
    assert b"".join(build_send_chunks("A\r\nB\n", eol="LF")) == b"A\nB\n"
    assert b"".join(build_send_chunks("A\nB", eol="CR")) == b"A\rB\r"


def test_find_problem_chars():
    assert find_problem_chars("G0 X1\n(BOHRUNG Ø10)\nM30") == [(2, "Ø")]
    assert find_problem_chars("G0 X1\n(ABC)\n") == []


@pytest.mark.parametrize("text,name", [
    ("%\nO1234 (FLANSCH OBEN)\nG0", "O1234_FLANSCH_OBEN"),
    ("%\nO0815\nG0", "O0815"),
    (":12\nG0", "O12"),
    ("0 BEGIN PGM TEIL1 MM\n1 L X0", "TEIL1"),
    ("%_N_WELLE_MPF\n;$PATH=/_N_MPF_DIR\nG0", "WELLE"),
    ("%\n<ABC.NC>\nG0", "ABC.NC"),
    ("G0 X1\n(MEIN TEIL)\n", "MEIN_TEIL"),
    ("G0 X1\nM30\n", None),
])
def test_detect_program_name(text, name):
    assert detect_program_name(text) == name


def test_safe_filename():
    assert safe_filename('a/b:c*d?"e<f>g|h') == "a_b_c_d_e_f_g_h"
    assert safe_filename("  Emco - 2  ") == "Emco_-_2"


def test_end_detector_percent_ignores_comments():
    d = EndDetector("percent")
    assert not d.feed("%\nO1 (VORSCHUB 50%)\nG0\n")
    assert not d.feed("M30\n")
    assert d.feed("%")


def test_end_detector_split_input():
    d = EndDetector("endpgm")
    assert not d.feed("0 BEGIN PGM X MM\n1 L X0\n2 END P")
    assert not d.feed("GM X")
    assert d.feed(" MM\n")

    d = EndDetector("m30")
    assert not d.feed("G0 X1\n(M30 IM KOMMENTAR)\nN10 M300\n")
    assert d.feed("N20 M30\n")


def test_end_detector_none():
    d = EndDetector("none")
    assert not d.feed("%\nO1\n%\n")


# --------------------------------------------------------------------------
# Echtes Sinumerik-Werkstück mit mehreren Unterprogrammen
# --------------------------------------------------------------------------

import os  # noqa: E402

from cnc_dnc.textutil import analyze_program, load_program_file  # noqa: E402

METLOG = os.path.join(os.path.dirname(__file__), "data", "METLOG_300069.txt")


def test_sinumerik_workpiece_name_and_programs():
    text = load_program_file(METLOG)
    info = analyze_program(text)
    assert info.name == "METLOG_300069"
    assert info.workpiece == "METLOG_300069"
    assert info.dialect == "sinumerik"
    assert [(ln, name) for ln, name, _ in info.programs] == [
        (1, "ABSTECH"), (12, "ANSCHLAGEN"), (33, "ENTLADEN"), (56, "HAUPTPROGRAMM"),
        (71, "KANAL1"), (153, "KANAL2"), (235, "PARAMETER"), (262, "UEBERGABE"),
    ]
    assert "Werkstück METLOG_300069" in info.summary() and "8 Programme" in info.summary()
    assert "\x1a" not in text  # DOS-Dateiende-Zeichen wird entfernt


def test_sinumerik_is_not_framed_with_percent():
    text = load_program_file(METLOG)
    lines = prepare_lines(text, frame_percent=True)
    assert lines[0] == "%_N_ABSTECH_MPF"
    assert lines[-1] == "M17"


@pytest.mark.parametrize("mode", ["percent", "m30", "none"])
def test_sinumerik_headers_do_not_end_reception(mode):
    """Mehrere %_N_..._MPF-Köpfe dürfen den Empfang nicht vorzeitig beenden."""
    text = load_program_file(METLOG)
    d = EndDetector(mode)
    states = [d.feed(ch) for ch in text]
    if mode == "m30":
        # M30 steht erst im Hauptprogramm (Zeile 69) – nicht schon bei M17 oder M2=22
        first = states.index(True)
        assert text[:first].count("\n") + 1 == 69
    else:
        assert not any(states)


def test_percent_end_with_and_without_final_newline():
    for tail in ("%", "%\n", "%\r\n", "% \n"):
        d = EndDetector("percent")
        assert not d.feed("%\nO1\nG0 X1\nM30\n")
        assert d.feed(tail)


def test_fanuc_multi_program_list():
    info = analyze_program("%\nO0001 (DECKEL)\nG0\nM30\nO0002\nG1\nM30\n%\n")
    assert info.name == "O0001_DECKEL"
    assert info.dialect == "fanuc"
    assert [(ln, n) for ln, n, _ in info.programs] == [(2, "O0001 (DECKEL)"), (5, "O0002")]


def test_heidenhain_name():
    info = analyze_program("0 BEGIN PGM PLATTE MM\n1 BLK FORM 0.1 Z X+0\n2 END PGM PLATTE MM\n")
    assert (info.name, info.dialect) == ("PLATTE", "heidenhain")


# --------------------------------------------------------------------------
# Zusammenrücken: Leerzeilen weg, 2 Leerzeilen vor jedem Werkzeug
# --------------------------------------------------------------------------

from cnc_dnc.textutil import compact_program, is_tool_call  # noqa: E402


@pytest.mark.parametrize("line,expected", [
    ("T12D1 G96 LIMS=2200 S=R77 G18 M8", True),
    ("N30 T01 M06", True),
    ("M06T02", True),
    ('T="BOHRER_8" D1', True),
    ("1 TOOL CALL 5 Z S2000", True),
    ("(WERKZEUG T01 - PLANFRAESER)", False),     # nur Kommentar
    (";T05 BOHRER", False),
    ("STOPRE", False),
    ("TRANSMIT_S3", False),
    ("TAKEOVER(\"ABSTECH\",R86,6000,R87,0,0)", False),
    ("$TC_DP1[1,1]=120", False),
    ("G01 X10 Z-5 F0.2", False),
])
def test_is_tool_call(line, expected):
    assert is_tool_call(line) is expected


def _blank_runs(text):
    """Längen aller Folgen von Leerzeilen."""
    runs, n = [], 0
    for ln in text.split("\n")[:-1]:
        if ln.strip():
            if n:
                runs.append(n)
            n = 0
        else:
            n += 1
    return runs


def test_compact_metlog():
    text = load_program_file(METLOG)
    r = compact_program(text)
    out = r.text
    # vor Werkzeugen 1 Leerzeile, zwischen Programmen 2 – sonst keine Leerzeilen
    assert set(_blank_runs(out)) == {1, 2}
    # kein Programmtext verloren oder verändert (bis auf Leerzeichen am Zeilenende)
    assert [ln.rstrip() for ln in text.split("\n") if ln.strip()] == [ln for ln in out.split("\n") if ln.strip()]
    # Kommentar bleibt beim Werkzeug, Abstand steht darüber
    assert "G40 G00 X150 Z130\n\n;FERTIGDREHEN VCMT04\nT02D1 G95 G18 S2500 M3 M8\n" in out
    # direkt nach dem Programmkopf kein Abstand
    assert out.startswith("%_N_ABSTECH_MPF\n;$PATH=/_N_WKS_DIR/_N_METLOG_300069_WPD\nT12D1")
    assert "%_N_KANAL1_MPF\n;$PATH=/_N_WKS_DIR/_N_METLOG_300069_WPD\n;VORDREHEN VNMG08\nT01D1" in out
    # Unterprogramme bleiben getrennt
    assert "M17\n\n\n%_N_ANSCHLAGEN_MPF\n" in out
    # Leerzeichen innerhalb der Zeile bleiben, am Zeilenende nicht
    assert "G75 X0 FP=1        ;1. SEITE FREIFAHREN\n" in out
    assert " \n" not in out
    assert (r.tools, r.tool_gaps, r.program_gaps) == (12, 10, 7)
    # zweimal anwenden ändert nichts mehr
    assert compact_program(out).text == out


def test_compact_fanuc():
    text = ("%\n\nO1234 (FLANSCH)\n(WERKZEUG T01 - PLANFRAESER)\nN10 G21 G90\n\n\nN20 G54\nN30 T01 M06\n"
            "N40 S2400 M03\n\n(BOHRER)\n\nN100 T05 M06\nN110 M30\n\n%\n")
    out = compact_program(text).text
    assert out == ("%\nO1234 (FLANSCH)\n(WERKZEUG T01 - PLANFRAESER)\nN10 G21 G90\nN20 G54\n\nN30 T01 M06\n"
                   "N40 S2400 M03\n\n(BOHRER)\nN100 T05 M06\nN110 M30\n%\n")


def test_compact_heidenhain_only_removes_blank_lines():
    text = "0 BEGIN PGM P MM\n\n1 TOOL CALL 1 Z S100\n\n2 L X0\n3 TOOL CALL 2 Z\n4 END PGM P MM\n"
    r = compact_program(text, dialect="heidenhain")
    assert r.text == "0 BEGIN PGM P MM\n1 TOOL CALL 1 Z S100\n2 L X0\n3 TOOL CALL 2 Z\n4 END PGM P MM\n"
    assert r.tool_gaps == 0 and r.removed == 2
