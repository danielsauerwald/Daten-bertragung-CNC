import os
import re

import pytest

from cnc_dnc.numbering import renumber
from cnc_dnc.textutil import compact_program, load_program_file

METLOG = os.path.join(os.path.dirname(__file__), "data", "METLOG_300069.txt")

FANUC = """%
O1000 (WELLE)
N5 G21 G99
N10 T0101
N20 G0 X52. Z2.
N30 G71 U2. R0.5
N40 G71 P50 Q90 U0.4 W0.1 F0.25
N50 G0 X20.
N60 G1 Z-20.
N70 X30. Z-30.
N80 Z-50.
N90 X52.
N100 G70 P50 Q90
N110 IF [#1 GT 5] GOTO 130
N120 #1=#1+1
N130 T0202 (FASE)
/N140 G0 X100.
N150 M30
%
"""


def _code(text):
    """Programminhalt ohne Satznummern – darf sich durch Nummerieren nie ändern."""
    return [re.sub(r"^(/?)N\d+\s*", r"\1", ln.strip()) for ln in text.split("\n") if ln.strip()]


def test_metlog_per_tool():
    text = compact_program(load_program_file(METLOG)).text
    r = renumber(text, "tool")
    assert not r.error
    out = r.text
    assert "%_N_KANAL1_MPF\n;$PATH=/_N_WKS_DIR/_N_METLOG_300069_WPD\n;VORDREHEN VNMG08\nN1000 T01D1" in out
    assert "\n;FERTIGDREHEN VCMT04\nN2000 T02D1 G95 G18 S2500 M3 M8\nN2010 G00 Z-2 M3 M8\n" in out
    # Zeilen vor dem ersten Werkzeug: N10, N20 …
    assert ";M00\nN10 $P_UIFR[1,Z,tr]=R_GLOB[0]" in out
    # Kommentare, Köpfe, Sprungmarken ohne Nummer
    assert "\nENDE:\n" in out and "\n%_N_KANAL2_MPF\n" in out
    assert _code(out) == _code(text)
    assert renumber(out, "tool").text == out  # zweimal -> gleich


def test_flat_and_step():
    r = renumber("%_N_A_MPF\nG0 X1\nG1 X2\n%_N_B_MPF\nG0 X3\n", "flat", step=5)
    assert r.text == "%_N_A_MPF\nN5 G0 X1\nN10 G1 X2\n%_N_B_MPF\nN5 G0 X3\n"


def test_fanuc_cycle_and_goto_references_follow():
    r = renumber(FANUC, "tool")
    assert not r.error and r.refs == 5
    out = r.text
    assert "N1030 G71 P1040 Q1080 U0.4 W0.1 F0.25\nN1040 G0 X20." in out
    assert "N1080 X52.\nN1090 G70 P1040 Q1080\n" in out
    assert "IF [#1 GT 5] GOTO 2000\n" in out and "N2000 T0202 (FASE)\n" in out
    assert "\n/N2010 G0 X100.\n" in out  # Satzausblendung "/" bleibt vorne
    assert "O1000 (WELLE)\n" in out and "N10 G21 G99\n" in out
    flat = renumber(FANUC, "flat").text
    assert "G71 P60 Q100" in flat and "G70 P60 Q100" in flat and "GOTO 140" in flat


@pytest.mark.parametrize("scheme", ["toolonly", "remove"])
def test_references_block_removing_numbers(scheme):
    r = renumber(FANUC, scheme)
    assert r.error and r.text == FANUC


def test_toolonly_and_remove():
    text = "%_N_K1_MPF\n;VOR\nT1D1 G95\nG0 X1\n;FERTIG\nT2D1\nG1 X2\nM17\n"
    assert renumber(text, "toolonly").text == "%_N_K1_MPF\n;VOR\nN1 T1D1 G95\nG0 X1\n;FERTIG\nN2 T2D1\nG1 X2\nM17\n"
    numbered = renumber(text, "tool").text
    assert renumber(numbered, "remove").text == text


def test_milling_g73_q_is_not_a_block_reference():
    text = "O1\nN10 T5 M6\nN20 G73 X0 Y0 Z-20. R2. Q3. F100\nN30 G80\n"
    r = renumber(text, "flat")
    assert not r.error and "G73 X0 Y0 Z-20. R2. Q3. F100" in r.text


def test_duplicate_target_is_refused():
    text = "O1\nN10 G0\nN10 G1\nN20 GOTO 10\n"
    r = renumber(text, "flat")
    assert r.error and r.text == text


def test_heidenhain_counts_blocks():
    text = "0 BEGIN PGM P MM\n1 TOOL CALL 1 Z S100\nL X0\n5 L X10\n7 END PGM P MM\n"
    r = renumber(text, "tool", dialect="heidenhain")
    assert r.text == "0 BEGIN PGM P MM\n1 TOOL CALL 1 Z S100\n2 L X0\n3 L X10\n4 END PGM P MM\n"


def test_big_tool_block_switches_to_ten_thousands():
    body = "".join("G1 X{}\n".format(i) for i in range(150))
    r = renumber("O1\nT1 M6\n" + body + "T2 M6\nG0 X0\n", "tool")
    assert "N10000 T1 M6" in r.text and "N20000 T2 M6" in r.text and r.note
