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
        _log_error(traceback.format_exc())
        _show_fatal("Das Programm konnte nicht gestartet werden:\n\n" + traceback.format_exc(limit=3))
        code = 1
    sys.exit(code)


if __name__ == "__main__":
    run()
