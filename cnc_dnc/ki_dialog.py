"""Fenster rund ums Bilder-Lesen: Schlüssel eintragen, warten, Vorschläge prüfen, Code-Liste ansehen."""

from __future__ import annotations

import os
import sys
import tkinter as tk
from tkinter import filedialog, ttk
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import dialogs, ki
from .dialogs import Dialog
from .system import IS_WIN, toggle_touch_keyboard
from .theme import C, MONO_FAMILY, Fonts, px

BILDTYPEN = [("Bilder", " ".join("*" + e for e in ki.BILD_ENDUNGEN)), ("Alle Dateien", "*.*")]


def bereit(parent, fonts: Fonts) -> bool:
    """Kann diese Version Bilder lesen und ist ein Schlüssel da? Sonst erklären bzw. nachfragen."""
    ok, hint = ki.verfuegbar()
    if not ok:
        dialogs.info(parent, fonts, "Nur in der PC-Version", hint)
        return False
    if not ki.schluessel():
        return SchluesselDialog(parent, fonts).show() is not None and bool(ki.schluessel())
    return True


def bilder_waehlen(parent, titel: str) -> List[str]:
    pfade = filedialog.askopenfilenames(parent=parent, title=titel, filetypes=BILDTYPEN)
    return [p for p in (pfade or ()) if p]


class SchluesselDialog(Dialog):
    """API-Schlüssel für das Bilder-Lesen eintragen (wird in der config.json gespeichert)."""

    def __init__(self, parent, fonts: Fonts) -> None:
        super().__init__(parent, fonts, "Bilder lesen – API-Schlüssel")
        bg = C["surface"]
        body = tk.Frame(self, bg=bg, padx=px(self, 22), pady=px(self, 16))
        body.pack(fill="both", expand=True)
        tk.Label(body, text="Bilder lesen mit Claude (Anthropic)", bg=bg, fg=C["text"], font=fonts.title,
                 anchor="w").pack(fill="x")
        tk.Label(body, text=(
            "Zum Lesen von Fotos (M-/G-Code-Listen, Zyklen) schickt das Programm die Bilder an Claude, die KI von "
            "Anthropic. Dafür braucht es einen API-Schlüssel: auf console.anthropic.com anmelden → "
            "„API Keys“ → „Create Key“, Guthaben unter „Billing“ aufladen. Ein Bild kostet je nach Größe "
            "etwa 2 bis 15 Cent.\n\nDer Schlüssel wird in den Einstellungen dieses PCs gespeichert. Statt "
            "hier geht auch die Windows-Umgebungsvariable ANTHROPIC_API_KEY."),
            bg=bg, fg=C["muted"], font=fonts.ui, justify="left", anchor="w",
            wraplength=px(self, 560)).pack(fill="x", pady=(px(self, 6), px(self, 12)))
        row = tk.Frame(body, bg=bg)
        row.pack(fill="x")
        tk.Label(row, text="Schlüssel", bg=bg, fg=C["text"], font=fonts.ui).pack(side="left")
        self.var = tk.StringVar(master=self, value=getattr(ki._cfg, "ki_schluessel", "") or "")
        self.entry = ttk.Entry(row, textvariable=self.var, width=46, font=fonts.ui, show="•")
        self.entry.pack(side="left", padx=(px(self, 10), 0), fill="x", expand=True)
        self.zeigen = tk.BooleanVar(master=self, value=False)
        ttk.Checkbutton(body, text="Schlüssel anzeigen", variable=self.zeigen, style="Surface.TCheckbutton",
                        command=lambda: self.entry.configure(show="" if self.zeigen.get() else "•")).pack(
            anchor="w", pady=(px(self, 6), 0))
        bar = tk.Frame(self, bg=C["bg"])
        bar.pack(fill="x")
        ttk.Button(bar, text="Speichern", style="Primary.TButton", command=self.ok).pack(
            side="right", padx=(0, px(self, 12)), pady=px(self, 12))
        ttk.Button(bar, text="Abbrechen", command=self.cancel).pack(side="right", padx=(0, px(self, 12)))
        if IS_WIN:
            ttk.Button(bar, text="⌨", style="Small.TButton", width=3, command=toggle_touch_keyboard).pack(
                side="left", padx=px(self, 12))
        self.bind("<Return>", lambda e: self.ok())
        self.entry.focus_set()

    def ok(self) -> None:
        wert = self.var.get().strip()
        ki.schluessel_speichern(wert)
        self.result = wert
        self.destroy()


class WartenDialog(Dialog):
    """„Bilder werden gelesen …“ – rechnet im Hintergrund, schließt sich danach selbst."""

    def __init__(self, parent, fonts: Fonts, titel: str, text: str, arbeit: Callable[[], Any]) -> None:
        super().__init__(parent, fonts, titel)
        self.fehler: Optional[Exception] = None
        self._abgebrochen = False
        bg = C["surface"]
        body = tk.Frame(self, bg=bg, padx=px(self, 26), pady=px(self, 20))
        body.pack(fill="both", expand=True)
        tk.Label(body, text=titel, bg=bg, fg=C["text"], font=fonts.title, anchor="w").pack(fill="x")
        tk.Label(body, text=text, bg=bg, fg=C["muted"], font=fonts.ui, anchor="w", justify="left",
                 wraplength=px(self, 460)).pack(fill="x", pady=(px(self, 6), px(self, 12)))
        self.bar = ttk.Progressbar(body, mode="indeterminate", length=px(self, 460))
        self.bar.pack(fill="x")
        self.bar.start(15)
        foot = tk.Frame(self, bg=C["bg"])
        foot.pack(fill="x")
        ttk.Button(foot, text="Abbrechen", command=self.cancel).pack(side="right", padx=px(self, 12),
                                                                     pady=px(self, 12))
        ki.im_hintergrund(self, arbeit, self._fertig)

    def _fertig(self, res: Any, err: Optional[Exception]) -> None:
        if self._abgebrochen:
            return
        self.result, self.fehler = res, err
        self.destroy()

    def cancel(self) -> None:
        self._abgebrochen = True  # die Anfrage läuft im Hintergrund zu Ende, das Ergebnis wird verworfen
        self.result, self.fehler = None, None
        self.destroy()


def lesen(parent, fonts: Fonts, titel: str, text: str, arbeit: Callable[[], Any]) -> Any:
    """Arbeit mit Wartefenster ausführen; Fehler als Meldung. Ergebnis oder None."""
    dlg = WartenDialog(parent, fonts, titel, text, arbeit)
    res = dlg.show()
    if dlg.fehler is not None:
        exc = dlg.fehler
        msg = str(exc) if isinstance(exc, ki.KIFehler) else "Unerwarteter Fehler: {}".format(exc)
        dialogs.error(parent, fonts, "Bilder lesen", msg)
        return None
    return res


class VorschlagDialog(Dialog):
    """Vorschläge aus den Bildern prüfen: je Feld jetzt / neu / Quelle, an- und abwählbar.
    Ergebnis: Liste der gewählten Vorschläge (oder None)."""

    def __init__(self, parent, fonts: Fonts, erg: "ki.PostproErgebnis") -> None:
        super().__init__(parent, fonts, "Vorschläge aus den Bildern")
        self.erg = erg
        self.wahl: Dict[str, bool] = {v.feld: v.uebernehmen for v in erg.vorschlaege}
        bg = C["bg"]
        self._size = (px(self, 1180), px(self, 720))
        head = tk.Frame(self, bg=bg)
        head.pack(fill="x", padx=px(self, 16), pady=(px(self, 12), px(self, 4)))
        tk.Label(head, text="Vorschläge für den Postprozessor", bg=bg, fg=C["text"], font=fonts.title).pack(
            side="left")
        if erg.steuerung:
            tk.Label(head, text="Erkannt: " + erg.steuerung, bg=bg, fg=C["muted"], font=fonts.ui).pack(
                side="left", padx=(px(self, 14), 0))
        tk.Label(self, text="Antippen schaltet einen Vorschlag an (✓) oder aus. Unsichere Zuordnungen und Werte "
                            "mit Problem sind aus – bitte mit der Code-Liste unten vergleichen.", bg=bg,
                 fg=C["muted"], font=fonts.small, anchor="w", justify="left").pack(fill="x", padx=px(self, 16))

        bar = tk.Frame(self, bg=bg)
        bar.pack(fill="x", side="bottom", padx=px(self, 16), pady=px(self, 12))
        self.info = tk.Label(bar, text="", bg=bg, fg=C["muted"], font=fonts.ui, anchor="w")
        self.info.pack(side="left")
        self.ok_btn = ttk.Button(bar, text="Übernehmen", style="Primary.TButton", command=self.ok)
        self.ok_btn.pack(side="right")
        ttk.Button(bar, text="Abbrechen", command=self.cancel).pack(side="right", padx=(0, px(self, 10)))
        ttk.Button(bar, text="Alle sicheren", style="Small.TButton", command=self.alle_sicheren).pack(
            side="right", padx=(0, px(self, 18)))

        pane = tk.PanedWindow(self, orient="vertical", bg=bg, sashwidth=px(self, 6), bd=0)
        pane.pack(fill="both", expand=True, padx=px(self, 16), pady=(px(self, 6), 0))
        top = tk.Frame(pane, bg=C["surface"])
        cols = (("an", "", 40), ("feld", "Feld", 190), ("alt", "Jetzt", 190), ("neu", "Vorschlag", 210),
                ("quelle", "Aus dem Bild", 420))
        self.tree = ttk.Treeview(top, columns=[c[0] for c in cols], show="headings", selectmode="none")
        for key, title, w in cols:
            self.tree.heading(key, text=title, anchor="w")
            self.tree.column(key, width=px(self, w), anchor="w", stretch=key == "quelle")
        sb = ttk.Scrollbar(top, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        self.tree.tag_configure("aus", foreground=C["muted"])
        self.tree.tag_configure("problem", foreground=C["error"])
        self.tree.bind("<ButtonRelease-1>", self._klick)
        pane.add(top, minsize=px(self, 160), stretch="always")

        low = tk.Frame(pane, bg=C["surface"])
        tk.Label(low, text="CODE-LISTE AUS DEN BILDERN ({})".format(len(erg.codes)), bg=C["surface"],
                 fg=C["faint"], font=fonts.small_bold, anchor="w").pack(fill="x", padx=px(self, 8),
                                                                         pady=(px(self, 6), 0))
        txt = tk.Text(low, font=(MONO_FAMILY, 10), height=8, wrap="word", bg=C["surface_alt"], fg=C["text"],
                      relief="flat", highlightthickness=0, padx=px(self, 8), pady=px(self, 6))
        sb2 = ttk.Scrollbar(low, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=sb2.set)
        sb2.pack(side="right", fill="y")
        txt.pack(fill="both", expand=True)
        zeilen = ["{:<10} {}".format(c["code"], c["bedeutung"]) for c in erg.codes]
        if erg.hinweise:
            zeilen += [""] + ["! " + h for h in erg.hinweise]
        txt.insert("1.0", "\n".join(zeilen) or "(keine Codes gelesen)")
        txt.configure(state="disabled")
        pane.add(low, minsize=px(self, 120), stretch="always")
        self._fuellen()

    def show(self):
        w, h, x, y = self.place_on_screen(*self._size)
        self.geometry("{}x{}+{}+{}".format(w, h, x, y))
        self.deiconify()
        try:
            self.wait_visibility()
            self.grab_set()
        except tk.TclError:
            pass
        self.lift()
        self.focus_force()
        self.wait_window()
        return self.result

    def _fuellen(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for v in self.erg.vorschlaege:
            an = self.wahl.get(v.feld, False)
            quelle = v.begruendung + ("" if v.sicher else "  (unsicher)")
            if v.problem:
                quelle = "✕ " + v.problem + " – " + quelle
            self.tree.insert("", "end", iid=v.feld, values=("✓" if an else "", v.label, v.alt, v.neu, quelle),
                             tags=("problem",) if v.problem else (() if an else ("aus",)))
        n = sum(1 for v in self.erg.vorschlaege if self.wahl.get(v.feld) and not v.problem)
        self.info.configure(text="{} von {} Vorschlägen gewählt".format(n, len(self.erg.vorschlaege))
                            if self.erg.vorschlaege else "Keine neuen Werte – der Postprozessor passt schon zu "
                                                         "den Bildern (oder sie enthalten keine passenden Codes).")

    def _klick(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        self.umschalten(iid)

    def umschalten(self, feld: str) -> None:
        v = next((x for x in self.erg.vorschlaege if x.feld == feld), None)
        if v is None or v.problem:
            return
        self.wahl[feld] = not self.wahl.get(feld, False)
        self._fuellen()

    def alle_sicheren(self) -> None:
        for v in self.erg.vorschlaege:
            self.wahl[v.feld] = v.uebernehmen
        self._fuellen()

    def ok(self) -> None:
        self.result = [v for v in self.erg.vorschlaege if self.wahl.get(v.feld) and not v.problem]
        self.destroy()


def bild_oeffnen(parent, fonts: Fonts, pfad: str) -> None:
    """Abgelegtes Bild mit dem Bildbetrachter von Windows öffnen."""
    if not os.path.isfile(pfad):
        dialogs.error(parent, fonts, "Bild fehlt", "Die Datei gibt es nicht mehr:\n" + pfad)
        return
    try:
        if IS_WIN:
            os.startfile(pfad)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            import subprocess

            subprocess.Popen(["open", pfad])
        else:
            import subprocess

            subprocess.Popen(["xdg-open", pfad])
    except OSError as exc:
        dialogs.error(parent, fonts, "Bild öffnen", str(exc))


class CodeListeDialog(Dialog):
    """Gelesene M-/G-Codes und abgelegte Bilder einer Maschine ansehen."""

    def __init__(self, parent, fonts: Fonts, titel: str, codes: Sequence[Dict[str, str]], bilder: Sequence[str],
                 machine_id: str) -> None:
        super().__init__(parent, fonts, "Code-Liste – " + titel)
        self.machine_id = machine_id
        self.result = None
        bg = C["bg"]
        tk.Label(self, text="Code-Liste  ·  " + titel, bg=bg, fg=C["text"], font=fonts.title, anchor="w").pack(
            fill="x", padx=px(self, 16), pady=(px(self, 12), px(self, 6)))
        bar = tk.Frame(self, bg=bg)
        bar.pack(fill="x", side="bottom", padx=px(self, 16), pady=px(self, 12))
        ttk.Button(bar, text="Schließen", command=self.cancel).pack(side="right")
        main = tk.Frame(self, bg=bg)
        main.pack(fill="both", expand=True, padx=px(self, 16))
        box = tk.Frame(main, bg=C["surface"])
        box.pack(side="left", fill="both", expand=True)
        self.tree = ttk.Treeview(box, columns=("code", "bedeutung"), show="headings", height=16)
        self.tree.heading("code", text="CODE", anchor="w")
        self.tree.heading("bedeutung", text="BEDEUTUNG", anchor="w")
        self.tree.column("code", width=px(self, 110), anchor="w")
        self.tree.column("bedeutung", width=px(self, 520), anchor="w")
        sb = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        for c in codes:
            self.tree.insert("", "end", values=(c.get("code", ""), c.get("bedeutung", "")))
        side = tk.Frame(main, bg=bg)
        side.pack(side="right", fill="y", padx=(px(self, 12), 0))
        tk.Label(side, text="BILDER", bg=bg, fg=C["faint"], font=fonts.small_bold).pack(anchor="w")
        self.bilder = list(bilder)
        self.lb = tk.Listbox(side, height=10, width=34, font=fonts.small, activestyle="none")
        for b in self.bilder:
            self.lb.insert("end", b)
        self.lb.pack(fill="y", expand=True)
        self.lb.bind("<Double-1>", lambda e: self.oeffnen())
        ttk.Button(side, text="Bild öffnen", style="Small.TButton", command=self.oeffnen).pack(
            fill="x", pady=(px(self, 6), 0))

    def oeffnen(self) -> None:
        sel = self.lb.curselection()
        if not sel:
            return
        bild_oeffnen(self, self.fonts, os.path.join(ki.bilder_ordner(self.machine_id), self.bilder[sel[0]]))


def codes_zusammenfuehren(alt: Sequence[Dict[str, str]], neu: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
    """Code-Liste der Maschine ergänzen: neue Codes anhängen, vorhandene mit neuer Bedeutung ersetzen."""
    out: List[Dict[str, str]] = [dict(c) for c in alt]
    pos = {c["code"].upper(): i for i, c in enumerate(out)}
    for c in neu:
        key = c["code"].upper()
        if key in pos:
            if c.get("bedeutung"):
                out[pos[key]]["bedeutung"] = c["bedeutung"]
        else:
            pos[key] = len(out)
            out.append(dict(c))
    return out


def postpro_lesen(parent, fonts: Fonts, pp, steuerung: str, machine_id: str
                  ) -> Optional[Tuple["ki.PostproErgebnis", List[str]]]:
    """Ganzer Ablauf „Postprozessor aus Bildern“: Bilder wählen, lesen, ablegen.
    Ergebnis: (Auswertung, abgelegte Bilddateien) oder None."""
    if not bereit(parent, fonts):
        return None
    pfade = bilder_waehlen(parent, "Bilder der M-/G-Code-Liste wählen (mehrere möglich)")
    if not pfade:
        return None

    def arbeit():
        erg = ki.postpro_aus_bildern(pfade, pp, steuerung)
        abgelegt = []
        for p in pfade:
            try:
                abgelegt.append(ki.bild_ablegen(machine_id, p, "codes"))
            except (ki.KIFehler, OSError):
                pass
        return erg, abgelegt

    return lesen(parent, fonts, "Bilder werden gelesen …",
                 "{} Bild(er) gehen an Claude. Das dauert meist 20 bis 60 Sekunden.".format(len(pfade)), arbeit)
