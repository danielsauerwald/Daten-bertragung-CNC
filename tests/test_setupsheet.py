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
    assert [(t.tool, t.description) for t in g[0]] == [("T12", "ABSTECH / UEBERGABE"), ("T11", "ANSCHLAGEN")]


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
