"""Startpunkt:  python -m cnc_dnc   (oder CNC-Datenuebertragung.exe)

Optionen:
    --selftest   Fenster aufbauen, kurz anzeigen, wieder schließen (für automatische Tests)
    --config X   andere Konfigurationsdatei verwenden
"""

from __future__ import annotations

import os
import sys
import traceback


def _show_fatal(message: str) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox

        r = tk.Tk()
        r.withdraw()
        messagebox.showerror("CNC-Datenübertragung", message)
        r.destroy()
    except Exception:
        print(message, file=sys.stderr)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    selftest = "--selftest" in argv
    cfg_path = None
    if "--config" in argv:
        i = argv.index("--config")
        if i + 1 < len(argv):
            cfg_path = argv[i + 1]

    from .theme import enable_dpi_awareness

    enable_dpi_awareness()

    import tkinter as tk

    from .app import App
    from .config import AppConfig

    if selftest and cfg_path is None:
        import tempfile

        cfg_path = os.path.join(tempfile.mkdtemp(prefix="cnc_dnc_selftest_"), "config.json")

    config = AppConfig.load(cfg_path)
    root = tk.Tk()
    if not selftest:
        try:
            if config.geometry:
                root.geometry(config.geometry)
            else:
                root.geometry("1280x760")
            if sys.platform.startswith("win"):
                root.state("zoomed")
        except tk.TclError:
            pass

    def report(exc, val, tb):
        text = "".join(traceback.format_exception(exc, val, tb))
        print(text, file=sys.stderr)
        if selftest:
            root.destroy()
            raise SystemExit(1)
        _log_error(text)
        from . import dialogs

        try:
            dialogs.error(root, app.fonts, "Unerwarteter Fehler", str(val) or exc.__name__,
                          detail="Details stehen in: " + _log_path())
        except Exception:
            pass

    root.report_callback_exception = report
    app = App(root, config)

    if selftest:
        from .textutil import normalize_text

        assert normalize_text("%\n\r\rO1\n\r\r%\n\r\r") == "%\nO1\n%\n"
        # Einrichteblatt: Module vorhanden (auch in der EXE) und PDF-Erzeugung klappt
        from .setup_dialog import SetupSheetDialog  # noqa: F401
        from .setupsheet import SheetData, extract_tools
        from .setupsheet_pdf import SheetContext, render_setup_sheet

        sample = "%_N_KANAL1_MPF\n;VORDREHEN\nT1D1\nM17\n%_N_KANAL2_MPF\n;PLANEN\nT2D1\nM17\n"
        pdf = os.path.join(os.path.dirname(cfg_path), "selbsttest.pdf")
        render_setup_sheet(pdf, SheetContext("SELBSTTEST", "Maschine", "Kunde", "x.txt"),
                           extract_tools(sample), [], SheetData())
        assert open(pdf, "rb").read(8) == b"%PDF-1.4"
        from .setupsheet import korrektur_rows
        from .setupsheet_pdf import render_correction_sheet

        korr = os.path.join(os.path.dirname(cfg_path), "selbsttest_korrektur.pdf")
        render_correction_sheet(korr, SheetContext("SELBSTTEST", "Maschine", "Kunde", "x.txt"),
                                korrektur_rows(extract_tools(sample), SheetData()), SheetData())
        assert open(korr, "rb").read(8) == b"%PDF-1.4"
        # Satznummern: Modul vorhanden und Zyklus-Verweise werden mitgeführt
        from .numbering import renumber
        from .numbering_dialog import RenumberDialog  # noqa: F401

        assert "N1030 G70 P1010 Q1020" in renumber("O1\nN5 T1\nN6 G0\nN7 G1\nN8 G70 P6 Q7\n", "tool").text
        # Kontur-Assistent: Modul vorhanden und Code-Erzeugung (Drehen + Fräsen auf Winkellagen) klappt
        from .kontur import beispiel_teil, generate
        from .kontur_dialog import KonturDialog  # noqa: F401

        kres = generate(beispiel_teil(), rahmen=True)
        assert kres.ok and "SPOS=DC(180)" in kres.code and "G42 G01 Z0" in kres.code
        # Postprozessor: Fanuc-Vorlage (C-Achse, Dezimalpunkt) und Fenster vorhanden
        from .postpro import vorlage
        from .postpro_dialog import PostproDialog  # noqa: F401

        kres = generate(beispiel_teil(), rahmen=True, pp=vorlage("fanuc"))
        assert kres.ok and "G00 C180." in kres.code and "T0101" in kres.code
        # Simulation mit Plattenform (Katalog) rechnet durch, ohne Kollision
        from .simulation import DrehSimulation
        from .sim_dialog import SimulationDialog  # noqa: F401
        from .werkzeug_dialog import WerkzeugDialog  # noqa: F401

        dsim = DrehSimulation(beispiel_teil(), generate(beispiel_teil(), rahmen=True))
        dsim.bis_ende()
        assert dsim.fertig and not [e for e in dsim.ereignisse if e.schwer]
        from .fraessim import FraesSimulation

        fsim = FraesSimulation(beispiel_teil(), generate(beispiel_teil(), rahmen=True))
        fsim.bis_ende()
        assert fsim.fertig and not [e for e in fsim.ereignisse if e.schwer]
        # Innenbearbeitung: Vorbohren in Drehmitte, Bohrstange schruppen/schlichten, Simulation
        from .kontur import KPunkt, neues_teil

        bu = neues_teil()
        bu.rohteil_d, bu.rohteil_l, bu.bohr_d, bu.bohr_tiefe = 45.0, 40.0, 16.0, 28.0
        bu.punkte = [KPunkt(0, 0), KPunkt(40, 0), KPunkt(40, -30), KPunkt(45, -30, op="frei")]
        bu.innen = [KPunkt(24, 0, "fase", 0.5), KPunkt(24, -10), KPunkt(20, -10), KPunkt(20, -25)]
        kres = generate(bu, rahmen=True)
        assert kres.ok and ";VORBOHREN" in kres.code and "G41 G01 Z0" in kres.code, kres.errors
        dsim = DrehSimulation(bu, kres)
        dsim.bis_ende()
        assert dsim.fertig and not [e for e in dsim.ereignisse if e.schwer]
        # Zyklen und Bilder lesen: Fenster vorhanden; in der PC-Version der ganze Weg über HTTP
        from . import ki
        from .ki_dialog import VorschlagDialog  # noqa: F401
        from .zyklen import Zyklus, ZParam
        from .zyklus_dialog import ZyklusDialog  # noqa: F401

        zy = Zyklus("CYCLE83", vorlage="CYCLE83({RTP},{RFP},{SDIS},{DP})",
                    parameter=[ZParam(n) for n in ("RTP", "RFP", "SDIS", "DP")])
        assert zy.code({"RTP": "10", "DP": "-20"}) == "CYCLE83(10,,,-20)"
        if ki.verfuegbar()[0]:
            assert ki.selbsttest(), "Bilder lesen: Selbsttest fehlgeschlagen"
            print("Bilder lesen: bereit (PC-Version)")
        # 3D-Modell: STL überall (Drehkörper schreiben, lesen, erkennen), STEP in der PC-Version
        from . import cad
        from .cad_dialog import CadDialog  # noqa: F401

        stl = os.path.join(os.path.dirname(cfg_path), "selbsttest.stl")
        cad.speichere_stl(cad.drehkoerper([(0, 0), (0, 16), (20, 16), (20, 10), (29, 10), (30, 9), (30, 0)]), stl)
        mod = cad.lade(stl)
        n_, p_, d_ = cad.achsen_kandidaten(mod)[0]
        erk = cad.teil_aus_modell(mod, cad.lage_standard(mod, p_, d_, n_))
        assert [(p.x, p.z) for p in erk.teil.punkte] == [(0, 0), (20, 0), (20, -10), (32, -10), (32, -30)], \
            [(p.x, p.z) for p in erk.teil.punkte]
        erk.teil.z_null = 10
        assert "STIRNSEITE BEI Z10" in generate(erk.teil).code
        if ki.verfuegbar()[0]:  # PC-Version: STEP mit Open CASCADE
            assert cad.step_verfuegbar(), "STEP lesen fehlt in der PC-Version"
            mod = cad.lade(os.path.join(os.path.dirname(cad.__file__), "daten", "beispiel_welle.step"))
            n_, p_, d_ = cad.achsen_kandidaten(mod)[0]
            erk = cad.teil_aus_modell(mod, cad.lage_standard(mod, p_, d_, n_))
            assert sorted(f.art for f in erk.teil.fraesungen) == ["bohrung_radial", "flaeche"], erk.merkmale
            print("3D-Modell (STEP): bereit (PC-Version)")
        app.editor.set_text("%\nO1234 (SELBSTTEST)\nN10 G0 X0 Y0\nN20 M30\n%\n")
        root.update()
        root.after(1500, root.destroy)
        root.mainloop()
        print("Selbsttest OK")
        return 0

    root.mainloop()
    return 0


def _log_path() -> str:
    from .config import config_dir

    return os.path.join(config_dir(), "fehler.log")


def _log_error(text: str) -> None:
    try:
        import datetime

        os.makedirs(os.path.dirname(_log_path()), exist_ok=True)
        with open(_log_path(), "a", encoding="utf-8") as fh:
            fh.write("\n=== {} ===\n{}".format(datetime.datetime.now().isoformat(timespec="seconds"), text))
    except OSError:
        pass


def run() -> None:
    """Start mit Fehlermeldung statt stillem Absturz (wichtig ohne Konsole)."""
    try:
        code = main()
    except SystemExit:
        raise
    except Exception:
        if "--selftest" in sys.argv:
            # automatischer Test: kein Meldungsfenster, das niemand wegklickt
            print("Selbsttest FEHLGESCHLAGEN:\n" + traceback.format_exc(), file=sys.stderr)
            sys.exit(1)
        _log_error(traceback.format_exc())
        _show_fatal("Das Programm konnte nicht gestartet werden:\n\n" + traceback.format_exc(limit=3))
        code = 1
    sys.exit(code)


if __name__ == "__main__":
    run()
