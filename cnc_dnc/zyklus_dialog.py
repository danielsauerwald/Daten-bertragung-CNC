"""Fenster „Zyklen“: Zyklen der Maschine (aus Fotos gelesen oder von Hand), ausfüllen und einfügen."""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk
from typing import Callable, Dict, List, Optional

from . import dialogs, ki, ki_dialog
from .dialogs import Dialog
from .system import IS_WIN, toggle_touch_keyboard
from .theme import C, MONO_FAMILY, Fonts, px
from .zyklen import Zyklus, als_text, aus_text, lade_liste, speichere_liste


class ZyklusDialog(Dialog):
    """Ergebnis: NC-Text des ausgefüllten Zyklus (zum Einfügen am Cursor) oder None."""

    def __init__(self, parent, fonts: Fonts, machine, on_changed: Optional[Callable[[], None]] = None) -> None:
        super().__init__(parent, fonts, "Zyklen – " + machine.display_name)
        self.machine = machine
        self.on_changed = on_changed
        self.zyklen: List[Zyklus] = lade_liste(machine)
        self.aktiv: Optional[Zyklus] = None
        self.vars: Dict[str, tk.StringVar] = {}
        self._thumb = None
        self._size = (px(self, 1280), px(self, 780))
        bg = C["bg"]

        head = tk.Frame(self, bg=bg)
        head.pack(fill="x", padx=px(self, 16), pady=(px(self, 12), px(self, 6)))
        tk.Label(head, text="Zyklen  ·  " + machine.display_name, bg=bg, fg=C["text"], font=fonts.title).pack(
            side="left")
        tk.Label(head, text=machine.control_name, bg=bg, fg=C["muted"], font=fonts.ui).pack(
            side="left", padx=(px(self, 12), 0))
        ttk.Button(head, text="Neu …", style="Small.TButton", command=self.neu).pack(side="right")
        ttk.Button(head, text="📷  Aus Bild lesen …", style="Small.TButton", command=self.aus_bild).pack(
            side="right", padx=(0, px(self, 8)))

        bar = tk.Frame(self, bg=bg)
        bar.pack(fill="x", side="bottom", padx=px(self, 16), pady=px(self, 12))
        self.ins_btn = ttk.Button(bar, text="Am Cursor einfügen", style="Primary.TButton", command=self.einfuegen)
        self.ins_btn.pack(side="right")
        ttk.Button(bar, text="Kopieren", command=self.kopieren).pack(side="right", padx=(0, px(self, 8)))
        ttk.Button(bar, text="Schließen", command=self.cancel).pack(side="right", padx=(0, px(self, 8)))
        if IS_WIN:
            ttk.Button(bar, text="⌨", style="Small.TButton", width=3, command=toggle_touch_keyboard).pack(
                side="right", padx=(0, px(self, 8)))
        # Meldung zuletzt packen: die Tasten bekommen zuerst ihren Platz
        self.msg = tk.Label(bar, text="", bg=bg, fg=C["muted"], font=fonts.ui, anchor="w", justify="left",
                            wraplength=px(self, 620))
        self.msg.pack(side="left", fill="x", expand=True)

        main = tk.Frame(self, bg=bg)
        main.pack(fill="both", expand=True, padx=px(self, 16))
        main.grid_columnconfigure(1, weight=1)
        main.grid_rowconfigure(0, weight=1)

        left = tk.Frame(main, bg=bg, width=px(self, 330))
        left.grid(row=0, column=0, sticky="nsew")
        left.grid_propagate(False)
        left.grid_rowconfigure(0, weight=1)
        left.grid_columnconfigure(0, weight=1)
        box = tk.Frame(left, bg=C["surface"])
        box.grid(row=0, column=0, sticky="nsew")
        self.tree = ttk.Treeview(box, columns=("name", "titel"), show="headings", selectmode="browse")
        self.tree.heading("name", text="ZYKLUS", anchor="w")
        self.tree.heading("titel", text="FUNKTION", anchor="w")
        self.tree.column("name", width=px(self, 110), anchor="w")
        self.tree.column("titel", width=px(self, 190), anchor="w")
        sb = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._gewaehlt())
        self.tree.bind("<Double-1>", lambda e: self.bearbeiten())
        btns = tk.Frame(left, bg=bg)
        btns.grid(row=1, column=0, sticky="ew", pady=(px(self, 6), 0))
        ttk.Button(btns, text="Bearbeiten …", style="Small.TButton", command=self.bearbeiten).pack(
            side="left", fill="x", expand=True)
        ttk.Button(btns, text="Löschen", style="Small.TButton", command=self.loeschen).pack(
            side="left", fill="x", expand=True, padx=(px(self, 6), 0))

        right = tk.Frame(main, bg=C["surface"], highlightthickness=1, highlightbackground=C["border"])
        right.grid(row=0, column=1, sticky="nsew", padx=(px(self, 12), 0))
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(2, weight=1)
        top = tk.Frame(right, bg=C["surface"])
        top.grid(row=0, column=0, sticky="ew", padx=px(self, 12), pady=(px(self, 10), 0))
        top.grid_columnconfigure(0, weight=1)
        self.titel = tk.Label(top, text="", bg=C["surface"], fg=C["text"], font=fonts.ui_bold, anchor="w",
                              justify="left")
        self.titel.grid(row=0, column=0, sticky="w")
        self.hinweis = tk.Label(top, text="", bg=C["surface"], fg=C["warn"], font=fonts.small, anchor="w",
                                justify="left", wraplength=px(self, 520))
        self.hinweis.grid(row=1, column=0, sticky="w")
        self.bild_lbl = tk.Label(top, bg=C["surface"], cursor="hand2")
        self.bild_lbl.grid(row=0, column=1, rowspan=3, sticky="ne", padx=(px(self, 10), 0))
        self.bild_lbl.bind("<Button-1>", lambda e: self.bild_oeffnen())
        self.bild_btn = ttk.Button(top, text="Bild öffnen", style="Small.TButton", command=self.bild_oeffnen)
        self.bild_btn.grid(row=2, column=0, sticky="w", pady=(px(self, 4), 0))

        tk.Label(right, text="PARAMETER (* = muss ausgefüllt werden)", bg=C["surface"], fg=C["faint"],
                 font=fonts.small_bold, anchor="w").grid(row=1, column=0, sticky="w", padx=px(self, 12),
                                                         pady=(px(self, 8), 0))
        holder = tk.Frame(right, bg=C["surface"])
        holder.grid(row=2, column=0, sticky="nsew", padx=px(self, 12))
        self.cv = tk.Canvas(holder, bg=C["surface"], highlightthickness=0)
        vsb = ttk.Scrollbar(holder, orient="vertical", command=self.cv.yview)
        self.cv.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.cv.pack(side="left", fill="both", expand=True)
        self.form = tk.Frame(self.cv, bg=C["surface"])
        self.form.grid_columnconfigure(2, weight=1)
        self._form_win = self.cv.create_window(0, 0, window=self.form, anchor="nw")
        self.form.bind("<Configure>", lambda e: self.cv.configure(scrollregion=self.cv.bbox("all")))
        self.cv.bind("<Configure>", lambda e: self.cv.itemconfigure(self._form_win, width=e.width))
        self.cv.bind("<Enter>", lambda e: self.cv.bind_all("<MouseWheel>", self._rad))
        self.cv.bind("<Leave>", lambda e: self.cv.unbind_all("<MouseWheel>"))

        tk.Label(right, text="NC-TEXT", bg=C["surface"], fg=C["faint"], font=fonts.small_bold, anchor="w").grid(
            row=3, column=0, sticky="w", padx=px(self, 12), pady=(px(self, 8), 0))
        self.vorschau = tk.Text(right, font=(MONO_FAMILY, 11), height=4, wrap="char", bg=C["surface_alt"],
                                fg=C["text"], relief="flat", highlightthickness=0, padx=px(self, 8),
                                pady=px(self, 6))
        self.vorschau.grid(row=4, column=0, sticky="ew", padx=px(self, 12), pady=(0, px(self, 12)))
        self.vorschau.configure(state="disabled")
        self._liste()

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

    def destroy(self) -> None:
        try:
            self.cv.unbind_all("<MouseWheel>")
        except tk.TclError:
            pass
        super().destroy()

    def _rad(self, event) -> None:
        self.cv.yview_scroll(int(-event.delta / 120) or (-1 if event.delta > 0 else 1), "units")

    # -- Liste ------------------------------------------------------------------

    def _speichern(self) -> None:
        speichere_liste(self.machine, self.zyklen)
        if self.on_changed is not None:
            self.on_changed()

    def _liste(self, waehle: Optional[str] = None) -> None:
        self.tree.delete(*self.tree.get_children())
        for z in self.zyklen:
            self.tree.insert("", "end", iid=z.id, values=(z.name, z.titel))
        ziel = waehle or (self.aktiv.id if self.aktiv is not None else (self.zyklen[0].id if self.zyklen else None))
        if ziel and self.tree.exists(ziel):
            self.tree.selection_set(ziel)
            self.tree.see(ziel)
        self._gewaehlt()

    def _gewaehlt(self) -> None:
        sel = self.tree.selection()
        z = next((x for x in self.zyklen if sel and x.id == sel[0]), None)
        self._werte_merken()
        self.aktiv = z
        self._formular()

    def _formular(self) -> None:
        for w in self.form.winfo_children():
            w.destroy()
        self.vars = {}
        z = self.aktiv
        f = self.fonts
        if z is None:
            self.titel.configure(text="Noch kein Zyklus. „📷 Aus Bild lesen …“: Foto vom Zyklus aus dem Handbuch "
                                      "oder von der Eingabemaske laden – oder „Neu …“ für eine eigene Vorlage."
                                 if not self.zyklen else "Links einen Zyklus antippen.")
            self.hinweis.configure(text="")
            self._bild(None)
            self._zeige_code("")
            return
        self.titel.configure(text="{}{}".format(z.name, "  –  " + z.titel if z.titel else ""))
        self.hinweis.configure(text="\n".join("! " + h for h in z.hinweise[:4]))
        self._bild(z)
        for r, p in enumerate(z.parameter):
            tk.Label(self.form, text=p.name + (" *" if p.pflicht else ""), bg=C["surface"], fg=C["text"],
                     font=(MONO_FAMILY, 11, "bold"), anchor="w").grid(row=r, column=0, sticky="w",
                                                                      pady=px(self, 2))
            var = tk.StringVar(master=self, value=z.werte.get(p.name, p.standard))
            self.vars[p.name] = var
            ttk.Entry(self.form, textvariable=var, width=12, font=(MONO_FAMILY, 11)).grid(
                row=r, column=1, sticky="w", padx=(px(self, 8), px(self, 10)), pady=px(self, 2))
            text = p.bedeutung + ("  [{}]".format(p.einheit) if p.einheit else "")
            tk.Label(self.form, text=text, bg=C["surface"], fg=C["muted"], font=f.small, anchor="w",
                     justify="left", wraplength=px(self, 520)).grid(row=r, column=2, sticky="w")
            var.trace_add("write", lambda *a: self._aktualisieren())
        self.cv.yview_moveto(0)
        self._aktualisieren()

    def _bild(self, z: Optional[Zyklus]) -> None:
        pfad = os.path.join(ki.bilder_ordner(self.machine.id), z.bild) if z is not None and z.bild else ""
        self._thumb = None
        if pfad and os.path.isfile(pfad) and ki.pillow_da():
            try:
                from PIL import Image, ImageTk

                with Image.open(pfad) as im:
                    im.thumbnail((px(self, 300), px(self, 170)))
                    self._thumb = ImageTk.PhotoImage(im.copy(), master=self)
            except Exception:
                self._thumb = None
        self.bild_lbl.configure(image=self._thumb if self._thumb is not None else "")
        if pfad and os.path.isfile(pfad):
            self.bild_btn.grid()
        else:
            self.bild_btn.grid_remove()

    def _werte(self) -> Dict[str, str]:
        return {k: v.get().strip() for k, v in self.vars.items()}

    def _werte_merken(self) -> None:
        if self.aktiv is not None and self.vars:
            self.aktiv.werte = self._werte()

    def _aktualisieren(self) -> None:
        z = self.aktiv
        if z is None:
            return
        werte = self._werte()
        err, warn = z.pruefen(werte)
        self._zeige_code(z.code(werte))
        if err:
            self.msg.configure(text="✕  " + err[0], fg=C["error"])
        elif warn:
            self.msg.configure(text="!  " + warn[0], fg=C["warn"])
        else:
            self.msg.configure(text="Werte wie an der Maschine eintragen (Punkt oder Komma). Leere Werte lässt "
                                    "der Zyklus weg.", fg=C["muted"])
        self.ins_btn.state(["disabled"] if err else ["!disabled"])

    def _zeige_code(self, text: str) -> None:
        self.vorschau.configure(state="normal")
        self.vorschau.delete("1.0", "end")
        self.vorschau.insert("1.0", text)
        self.vorschau.configure(state="disabled")

    # -- Aktionen ---------------------------------------------------------------

    def aus_bild(self) -> None:
        if not ki_dialog.bereit(self, self.fonts):
            return
        pfade = ki_dialog.bilder_waehlen(self, "Foto vom Zyklus wählen (Handbuchseite oder Eingabemaske)")
        if not pfade:
            return
        m = self.machine

        def arbeit():
            z = ki.zyklus_aus_bildern(pfade, m.control_name)
            try:
                z.bild = ki.bild_ablegen(m.id, pfade[0], "zyklus")
            except (ki.KIFehler, OSError):
                pass
            return z

        z = ki_dialog.lesen(self, self.fonts, "Zyklus wird gelesen …",
                            "Das Foto geht an Claude. Das dauert meist 20 bis 60 Sekunden.", arbeit)
        if z is None:
            return
        res = ZyklusEditDialog(self, self.fonts, z, "Gelesenen Zyklus prüfen").show()
        if res is None:
            return
        self.zyklen.append(res)
        self._speichern()
        self.aktiv = None
        self._liste(res.id)

    def neu(self) -> None:
        res = ZyklusEditDialog(self, self.fonts, Zyklus(name="ZYKLUS"), "Neuer Zyklus").show()
        if res is None:
            return
        self.zyklen.append(res)
        self._speichern()
        self.aktiv = None
        self._liste(res.id)

    def bearbeiten(self) -> None:
        z = self.aktiv
        if z is None:
            return
        self._werte_merken()
        res = ZyklusEditDialog(self, self.fonts, z, "Zyklus bearbeiten").show()
        if res is None:
            return
        self.zyklen[self.zyklen.index(z)] = res
        self._speichern()
        self.aktiv = None
        self._liste(res.id)

    def loeschen(self) -> None:
        z = self.aktiv
        if z is None:
            return
        if not dialogs.confirm(self, self.fonts, "Zyklus löschen", "„{}“ aus der Liste dieser Maschine "
                               "löschen?".format(z.anzeige), yes="Löschen", danger=True):
            return
        self.zyklen.remove(z)
        self.aktiv = None
        self._speichern()
        self._liste()

    def bild_oeffnen(self) -> None:
        z = self.aktiv
        if z is not None and z.bild:
            ki_dialog.bild_oeffnen(self, self.fonts, os.path.join(ki.bilder_ordner(self.machine.id), z.bild))

    def _fertiger_code(self) -> Optional[str]:
        z = self.aktiv
        if z is None:
            return None
        werte = self._werte()
        err, _warn = z.pruefen(werte)
        if err:
            self._aktualisieren()
            return None
        z.werte = werte
        self._speichern()  # Werte fürs nächste Mal merken
        return z.code(werte)

    def kopieren(self) -> None:
        code = self._fertiger_code()
        if code is None:
            return
        self.clipboard_clear()
        self.clipboard_append(code)
        self.msg.configure(text="✓  In die Zwischenablage kopiert.", fg=C["ok"])

    def einfuegen(self) -> None:
        code = self._fertiger_code()
        if code is None:
            return
        self.result = code
        self.destroy()


class ZyklusEditDialog(Dialog):
    """Name, Funktion, Vorlage und Parameterliste eines Zyklus bearbeiten. Ergebnis: Zyklus oder None."""

    def __init__(self, parent, fonts: Fonts, z: Zyklus, titel: str) -> None:
        super().__init__(parent, fonts, titel)
        self.z = Zyklus.from_dict(z.to_dict())
        bg = C["surface"]
        body = tk.Frame(self, bg=bg, padx=px(self, 20), pady=px(self, 14))
        body.grid_columnconfigure(1, weight=1)
        tk.Label(body, text=titel, bg=bg, fg=C["text"], font=fonts.title, anchor="w").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, px(self, 8)))
        self.name = tk.StringVar(master=self, value=self.z.name)
        self.funktion = tk.StringVar(master=self, value=self.z.titel)
        for r, (label, var, width) in enumerate((("Name", self.name, 18), ("Funktion", self.funktion, 40)), 1):
            tk.Label(body, text=label, bg=bg, fg=C["text"], font=fonts.ui, anchor="w").grid(
                row=r, column=0, sticky="w", pady=px(self, 3))
            ttk.Entry(body, textvariable=var, width=width, font=fonts.ui).grid(row=r, column=1, sticky="w",
                                                                              pady=px(self, 3))
        tk.Label(body, text="Vorlage", bg=bg, fg=C["text"], font=fonts.ui, anchor="nw").grid(
            row=3, column=0, sticky="nw", pady=px(self, 3))
        self.vorlage = tk.Text(body, font=(MONO_FAMILY, 11), height=3, width=70, wrap="char", relief="flat",
                               highlightthickness=1, highlightbackground=C["border"])
        self.vorlage.grid(row=3, column=1, sticky="ew", pady=px(self, 3))
        self.vorlage.insert("1.0", self.z.vorlage.replace("|", "\n"))
        tk.Label(body, text="Eine Zeile je NC-Satz, Parameter als {NAME}. Siemens: CYCLE83({RTP},{RFP},…) – leere "
                            "Werte bleiben leere Stellen. Fanuc: G83 X{X} Z{Z} R{R} Q{Q} F{F} – leere Wörter "
                            "fallen weg.", bg=bg, fg=C["muted"], font=fonts.small, anchor="w", justify="left",
                 wraplength=px(self, 700)).grid(row=4, column=1, sticky="w")
        tk.Label(body, text="Parameter", bg=bg, fg=C["text"], font=fonts.ui, anchor="nw").grid(
            row=5, column=0, sticky="nw", pady=(px(self, 8), px(self, 3)))
        self.params = tk.Text(body, font=(MONO_FAMILY, 10), height=12, width=70, wrap="none", relief="flat",
                              highlightthickness=1, highlightbackground=C["border"])
        self.params.grid(row=5, column=1, sticky="nsew", pady=(px(self, 8), px(self, 3)))
        self.params.insert("1.0", als_text(self.z))
        tk.Label(body, text="Je Zeile: NAME; Bedeutung; Einheit; Vorgabewert; Pflicht (ja/nein)", bg=bg,
                 fg=C["muted"], font=fonts.small, anchor="w").grid(row=6, column=1, sticky="w")
        if self.z.hinweise:
            tk.Label(body, text="\n".join("! " + h for h in self.z.hinweise), bg=bg, fg=C["warn"], font=fonts.small,
                     anchor="w", justify="left", wraplength=px(self, 700)).grid(row=7, column=1, sticky="w",
                                                                                pady=(px(self, 6), 0))
        self.msg = tk.Label(body, text="", bg=bg, fg=C["error"], font=fonts.ui, anchor="w", justify="left",
                            wraplength=px(self, 700))
        self.msg.grid(row=8, column=0, columnspan=2, sticky="w", pady=(px(self, 6), 0))
        bar = tk.Frame(self, bg=C["bg"])
        bar.pack(fill="x", side="bottom")
        ttk.Button(bar, text="Speichern", style="Primary.TButton", command=self.ok).pack(
            side="right", padx=(0, px(self, 12)), pady=px(self, 12))
        ttk.Button(bar, text="Abbrechen", command=self.cancel).pack(side="right", padx=(0, px(self, 12)))
        if IS_WIN:
            ttk.Button(bar, text="⌨", style="Small.TButton", width=3, command=toggle_touch_keyboard).pack(
                side="left", padx=px(self, 12))
        body.pack(fill="both", expand=True)

    def zyklus(self) -> Zyklus:
        z = Zyklus.from_dict(self.z.to_dict())
        z.name = self.name.get().strip() or "ZYKLUS"
        z.titel = self.funktion.get().strip()
        z.vorlage = "|".join(ln.strip() for ln in self.vorlage.get("1.0", "end").splitlines() if ln.strip())
        z.parameter = aus_text(self.params.get("1.0", "end"))
        z.werte = {k: v for k, v in z.werte.items() if any(p.name == k for p in z.parameter)}
        return z

    def ok(self) -> None:
        z = self.zyklus()
        err, _warn = z.pruefen()
        if err:
            self.msg.configure(text="✕  " + err[0])
            return
        self.result = z
        self.destroy()
