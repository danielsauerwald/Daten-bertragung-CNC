import os
import re
import zlib

import pytest

from cnc_dnc.setupsheet import (
    SheetData,
    extract_parameters,
    extract_tools,
    format_tool,
    load_sheet,
    save_sheet,
    sidecar_path,
    tools_by_side,
)
from cnc_dnc.setupsheet_pdf import SheetContext, render_setup_sheet
from cnc_dnc.textutil import load_program_file

METLOG = os.path.join(os.path.dirname(__file__), "data", "METLOG_300069.txt")


def test_metlog_tools_per_side_like_the_paper_sheet():
    g = tools_by_side(extract_tools(load_program_file(METLOG)))
    assert [(t.tool, t.description) for t in g[1]] == [
        ("T1", "VORDREHEN VNMG08"), ("T2", "FERTIGDREHEN VCMT04"), ("T3", "BOHREN 12"),
        ("T8", "INNEN ECOCUT 10"), ("T10", "GEWINDEBOHREN M14LH")]
    assert [(t.tool, t.description) for t in g[2]] == [
        ("T2", "SCHLICHTEN DCMT04"), ("T2", "GEWINDE M22X1.5"), ("T3", "INNEN ECOCUT 10"), ("T3", "FRAESEN")]
    # Werkzeuge aus anderen Unterprogrammen extra, ohne Kommentar mit Programmname
    # Funktion statt Programmnamen: T12 = ABSTECHEN (nicht "ABSTECH / UEBERGABE"), T11 = ANSCHLAG
    assert [(t.tool, t.description) for t in g[0]] == [("T12", "ABSTECHEN"), ("T11", "ANSCHLAG")]


def test_fanuc_single_program_and_side_marker():
    text = ("%\nO1234 (FLANSCH)\n(WERKZEUG T01 - PLANFRAESER D63)\nN10 T01 M06\nG0 X0\n"
            "N20 T05 M06 (BOHRER 8.5)\n(SEITE 2)\n(ANFASEN)\nN30 T07 M06\nM30\n%\n")
    g = tools_by_side(extract_tools(text))
    assert [(t.tool, t.description) for t in g[1]] == [("T1", "PLANFRAESER D63"), ("T5", "BOHRER 8.5")]
    assert [(t.tool, t.description) for t in g[2]] == [("T7", "ANFASEN")]


@pytest.mark.parametrize("line,shown", [
    ("T01D1 G95", "T1"), ("T03D2 G95", "T3 D2"), ("N10 T0101", "T0101"), ('T="BOHRER_8" D1', "BOHRER_8"),
    ("N30 T12 M06", "T12"), ("1 TOOL CALL 5 Z S2000", "T5"),
])
def test_format_tool(line, shown):
    assert format_tool(line) == shown


def test_parameters_from_parameter_program():
    params = extract_parameters(load_program_file(METLOG))
    names = [p.name for p in params]
    assert names[0] == "$P_UIFR[1,Z,tr]" and "R72" in names and "R81" in names
    r72 = params[names.index("R72")]
    assert (r72.value, r72.comment) == ("32", "DURCHMESSER STANGE")
    assert "R86" not in names  # berechnete Werte (R86=R79+…) gehören nicht aufs Blatt


def test_sheet_data_roundtrip_and_tool_matching(tmp_path):
    prog = tmp_path / "TEIL.txt"
    prog.write_text("x")
    tools = extract_tools(load_program_file(METLOG))
    d = SheetData()
    d.fields["hsp_druck"] = "25"
    d.notes = "Kühlung prüfen"
    d.set_tool(tools[2], "VORDREHEN", "Platte neu")
    save_sheet(str(prog), d)
    assert os.path.basename(sidecar_path(str(prog))) == "TEIL.einrichteblatt.json"
    back = load_sheet(str(prog))
    assert back.fields["hsp_druck"] == "25" and back.notes == "Kühlung prüfen"
    assert back.tool_entry(tools[2]) == {"description": "VORDREHEN", "remark": "Platte neu"}
    # Kommentar im Programm geändert -> Bemerkung bleibt trotzdem am Werkzeug
    changed = tools[2]._replace(description="VORDREHEN NEU")
    assert back.tool_entry(changed)["remark"] == "Platte neu"
    # fehlende Datei -> leeres Blatt
    assert load_sheet(str(tmp_path / "GIBTSNICHT.txt")).notes == ""


def _pdf_streams(data: bytes):
    out = []
    for m in re.finditer(rb"stream\n(.*?)\nendstream", data, re.S):
        out.append(zlib.decompress(m.group(1)))
    return b"\n".join(out)


def test_pdf_is_valid_and_contains_the_sheet(tmp_path):
    text = load_program_file(METLOG)
    d = SheetData()
    d.fields.update(stueckzahl="500", hsp_druck="25", gsp_druck="15")
    path = str(tmp_path / "METLOG_300069_Einrichteblatt.pdf")
    render_setup_sheet(path, SheetContext("METLOG_300069", "2: Emco - 2", "Metlog", "METLOG_300069.txt"),
                       extract_tools(text), extract_parameters(text), d)
    data = open(path, "rb").read()
    assert data.startswith(b"%PDF-1.4") and data.rstrip().endswith(b"%%EOF")
    # Querverweistabelle: jeder Eintrag zeigt auf "n 0 obj"
    xref = int(re.search(rb"startxref\n(\d+)", data).group(1))
    entries = re.findall(rb"(\d{10}) 00000 n", data[xref:])
    for i, off in enumerate(entries, start=1):
        assert data[int(off):].startswith(str(i).encode() + b" 0 obj")
    assert data.count(b"/Type /Page ") == 1  # passt auf eine Seite
    content = _pdf_streams(data)
    for needle in (b"(METLOG_300069)", b"(SEITE 1)", b"(SEITE 2)", b"(VORDREHEN VNMG08)", b"(25 bar)",
                   b"(DURCHMESSER STANGE)", b"(St\xfcckzahl)"):
        assert needle in content, needle


# --------------------------------------------------------------------------
# Korrekturblatt
# --------------------------------------------------------------------------

from cnc_dnc.setupsheet import korrektur_rows  # noqa: E402
from cnc_dnc.setupsheet_pdf import render_correction_sheet  # noqa: E402


def test_korrektur_rows_parting_and_stop_on_side_one():
    tools = extract_tools(load_program_file(METLOG))
    rows = korrektur_rows(tools, SheetData())
    assert [(r.tool, r.name) for r in rows[1]] == [
        ("T1", "VORDREHEN VNMG08"), ("T2", "FERTIGDREHEN VCMT04"), ("T3", "BOHREN 12"),
        ("T8", "INNEN ECOCUT 10"), ("T10", "GEWINDEBOHREN M14LH"),
        ("T11", "ANSCHLAG"), ("T12", "ABSTECHEN")]
    # gleiches Werkzeug auf einer Seite nur einmal (korrigiert wird pro Werkzeug)
    assert [(r.tool, r.name) for r in rows[2]] == [
        ("T2", "SCHLICHTEN DCMT04 / GEWINDE M22X1.5"), ("T3", "INNEN ECOCUT 10 / FRAESEN")]


def test_korrektur_side_name_and_remark_can_be_changed(tmp_path):
    tools = extract_tools(load_program_file(METLOG))
    d = SheetData()
    d.korrektur["0|T11"] = {"side": 2, "remark": "Anschlag auf S2"}
    d.korrektur["1|T2"] = {"name": "FERTIG", "remark": "X -0,02"}
    rows = korrektur_rows(tools, d)
    assert "T11" not in [r.tool for r in rows[1]]
    assert rows[2][-1].tool == "T11" and rows[2][-1].remark == "Anschlag auf S2"
    t2 = [r for r in rows[1] if r.tool == "T2"][0]
    assert (t2.name, t2.remark) == ("FERTIG", "X -0,02")
    # gespeichert und wieder geladen
    prog = tmp_path / "TEIL.txt"
    prog.write_text("x")
    save_sheet(str(prog), d)
    assert load_sheet(str(prog)).korrektur == d.korrektur


def test_correction_sheet_pdf(tmp_path):
    tools = extract_tools(load_program_file(METLOG))
    d = SheetData()
    d.korrektur["1|T2"] = {"remark": "X -0,02"}
    path = str(tmp_path / "METLOG_300069_Korrekturblatt.pdf")
    render_correction_sheet(path, SheetContext("METLOG_300069", "2: Emco - 2", "Metlog", "METLOG_300069.txt"),
                            korrektur_rows(tools, d), d)
    data = open(path, "rb").read()
    assert data.startswith(b"%PDF-1.4") and data.count(b"/Type /Page ") == 1  # A4, eine Seite
    assert b"/MediaBox [0 0 595.28 841.89]" in data  # hochkant
    content = _pdf_streams(data)
    for needle in (b"(KORREKTURBLATT)", b"(SEITE 1)", b"(SEITE 2)", b"(T12)", b"(T11)", b"(X -0,02)",
                   b"(Bemerkung)"):
        assert needle in content, needle



def test_tool_description_across_setup_lines():
    text = ("%_N_KANAL2_MPF\n;EINSTECHEN\nT06D1 G95\nG00 X100 Z100\nM5\n;FRAESEN 5MM\nM5\n\n"
            "T07D1 G94 G18 S3=3500 M3=3\nG00 X18 Z-9.5\nM17\n")
    assert [(t.tool, t.description) for t in extract_tools(text)] == [("T6", "EINSTECHEN"), ("T7", "FRAESEN 5MM")]
    # eine Bewegung dazwischen -> Kommentar gehört nicht mehr dazu
    text2 = text.replace(";FRAESEN 5MM\nM5\n", ";FRAESEN 5MM\nG0 X50\n")
    assert [t.description for t in extract_tools(text2)] == ["EINSTECHEN", ""]


# T12 kommt in KANAL1 und in ABSTECH/UEBERGABE vor (BOESE_300240) -> nur einmal auf Seite 1
BOESE_300240 = ("%_N_KANAL1_MPF\nT12D1 G96\nG00 X200\n;FERTIGDREHEN DCMT08\nT02D2 G95\n;BOHREN 16\nT08D1\nM17\n"
                "%_N_KANAL2_MPF\n;FERTIGDREHEN\nT02D1\n;INNENFASE\nT07D1\nM17\n"
                "%_N_ABSTECH_MPF\nT12D1\nM17\n%_N_UEBERGABE_MPF\nT12D1\nM17\n%_N_ANSCHLAGEN_MPF\nT11D1\nM17\n")


def test_same_tool_on_one_side_is_one_row():
    rows = korrektur_rows(extract_tools(BOESE_300240), SheetData())
    assert [(r.tool, r.name) for r in rows[1]] == [
        ("T2 D2", "FERTIGDREHEN DCMT08"), ("T8", "BOHREN 16"), ("T11", "ANSCHLAG"),
        ("T12", "ABSTECHEN")]
    t12 = rows[1][-1]
    assert set(t12.keys) == {"1|T12", "0|T12"}
    # T2 auf Seite 2 ist ein anderes Werkzeug (zweiter Revolver) -> bleibt getrennt
    assert [(r.tool, r.name) for r in rows[2]] == [("T2", "FERTIGDREHEN"), ("T7", "INNENFASE")]


def test_tool_without_comment_borrows_name_on_same_side_only():
    tools = extract_tools(BOESE_300240)
    k1_t12 = [t for t in tools if t.side == 1 and t.tool == "T12"][0]
    assert k1_t12.description == "ABSTECHEN"  # von den Abstech-Programmen (Hauptspindel)
    text = "%_N_KANAL1_MPF\n;SCHRUPPEN\nT02D1\nM17\n%_N_KANAL2_MPF\nT02D1\nM17\n"
    assert [(t.side, t.description) for t in extract_tools(text)] == [(1, "SCHRUPPEN"), (2, "")]


def test_moving_merged_row_moves_all_parts():
    d = SheetData()
    for k in ("1|T12", "0|T12"):
        d.korrektur[k] = {"side": 2}
    rows = korrektur_rows(extract_tools(BOESE_300240), d)
    assert "T12" not in [r.tool for r in rows[1]]
    assert [r.tool for r in rows[2]].count("T12") == 1


@pytest.mark.parametrize("desc,func,tool", [
    ("VORDREHEN VNMG08", "VORDREHEN", "VNMG08"),
    ("BOHREN 12", "BOHREN", "Ø12"),
    ("INNEN ECOCUT 10", "INNEN", "ECOCUT Ø10"),
    ("GEWINDEBOHREN M14LH", "GEWINDEBOHREN", "M14LH"),
    ("GEWINDE M22X1.5", "GEWINDE", "M22X1.5"),
    ("SCHLICHTEN DCMT04 / GEWINDE M22X1.5", "SCHLICHTEN / GEWINDE", "DCMT04 / M22X1.5"),
    ("FRAESER 20", "FRAESEN", "FRAESER Ø20"),
    ("FRAESEN 5MM", "FRAESEN", "Ø5"),
    ("BOHREN WENDEPLATTENBOHRER 16", "BOHREN", "WENDEPLATTENBOHRER Ø16"),
    ("SCHRUPPEN R0.8", "SCHRUPPEN", "R0.8"),        # SCHRUPPEN ist keine Wendeplatte
    ("DCMT11T304 SCHLICHTEN", "SCHLICHTEN", "DCMT11T304"),
    ("FASE INNEN", "FASE INNEN", ""),
    ("ABSTECHEN", "ABSTECHEN", ""),
])
def test_split_function_and_tool(desc, func, tool):
    from cnc_dnc.setupsheet import split_description

    assert split_description(desc) == (func, tool)


def test_function_from_program_names():
    from cnc_dnc.setupsheet import function_from_program

    assert function_from_program("ABSTECH") == "ABSTECHEN"
    assert function_from_program("ANSCHLAGEN") == "ANSCHLAG"
    assert function_from_program("UEBERGABE") == ""        # keine Werkzeugfunktion
    assert function_from_program("STANGENWECHSEL") == ""


def test_korrektur_rows_have_function_and_tool_and_overrides():
    tools = extract_tools(load_program_file(METLOG))
    d = SheetData()
    rows = korrektur_rows(tools, d)
    t1 = rows[1][0]
    assert (t1.tool, t1.function, t1.tooltext) == ("T1", "VORDREHEN", "VNMG08")
    d.korrektur[t1.key] = {"function": "SCHRUPPEN", "tool": "VNMG 080408"}
    t1 = korrektur_rows(tools, d)[1][0]
    assert (t1.function, t1.tooltext) == ("SCHRUPPEN", "VNMG 080408")


def test_correction_sheet_landscape(tmp_path):
    tools = extract_tools(load_program_file(METLOG))
    path = str(tmp_path / "quer.pdf")
    render_correction_sheet(path, SheetContext("METLOG_300069"), korrektur_rows(tools, SheetData()), SheetData(),
                            landscape=True)
    data = open(path, "rb").read()
    assert b"/MediaBox [0 0 841.89 595.28]" in data and data.count(b"/Type /Page ") == 1
    content = _pdf_streams(data)
    for needle in (b"(Funktion)", b"(Werkzeug)", b"(VNMG08)", b"(\xd812)", b"(ABSTECHEN)"):
        assert needle in content, needle
