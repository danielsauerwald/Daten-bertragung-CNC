"""Fenster „Simulation“: Drehen (mit Plattenform) und Fräsen/Bohren (C-/Y-Achse) abspielen."""

from __future__ import annotations

import math
import time
import tkinter as tk
from tkinter import ttk
from typing import List, Optional, Tuple

from .dialogs import Dialog
from .fraessim import NONE, FraesSimulation, richtung
from .kontur import FEATURES, STRATEGIEN, Ergebnis, Teil, strategie
from .simulation import DrehSimulation, Ereignis
from .theme import C, MONO_FAMILY, Fonts, px

# Abspielen nach Zeit: Bahn in mm je Sekunde (Eilgang schneller), etwa 60 Bilder je Sekunde
TEMPO_MM_S = [2, 5, 10, 25, 50, 100, 250, 1000]
TEMPO_START = 3
EILGANG_FAKTOR = 5.0
BILD_MS = 16
PLATTE_FARBE = "#E8B923"
HALTER_FARBE = "#8D99A6"
MATERIAL = "#D7DEE8"
MATERIAL_RAND = "#7B8A9C"
ZIEL = C["primary"]
BAHN_G1 = "#0E8A5B"
BAHN_G0 = "#D9480F"
FRAESER_FARBE = "#C2410C"


class Ablauf:
    """Drehen und danach Fräsen als ein durchgehender Ablauf von Schritten."""

    def __init__(self, teil: Teil, res: Ergebnis) -> None:
        self.dreh: Optional[DrehSimulation] = DrehSimulation(teil, res) if res.moves else None
        self.fraes: Optional[FraesSimulation] = FraesSimulation(teil, res) if res.fraes_ops else None
        self.n_dreh = len(self.dreh.schritte) if self.dreh else 0
        self.n = self.n_dreh + (len(self.fraes.schritte) if self.fraes else 0)

    @property
    def i(self) -> int:
        return (self.dreh.i if self.dreh else 0) + (self.fraes.i if self.fraes else 0)

    @property
    def phase(self) -> str:
        if self.dreh is None:
            return "fraes"
        if self.fraes is None or self.fraes.i == 0:
            return "dreh"
        return "fraes"

    @property
    def fertig(self) -> bool:
        return (self.dreh is None or self.dreh.fertig) and (self.fraes is None or self.fraes.fertig)

    def reset(self) -> None:
        for s in (self.dreh, self.fraes):
            if s is not None:
                s.reset()

    def weiter(self, k: int) -> None:
        if self.dreh is not None and not self.dreh.fertig:
            before = self.dreh.i
            self.dreh.weiter(k)
            k -= self.dreh.i - before
        if k > 0 and self.fraes is not None and (self.dreh is None or self.dreh.fertig):
            self.fraes.weiter(k)

    def bis_ende(self) -> None:
        self.weiter(self.n + 1)

    def _naechster(self):
        """Nächster Schritt (Drehen bzw. Fräsen) oder None am Ende."""
        if self.dreh is not None and not self.dreh.fertig and self.dreh.i < len(self.dreh.schritte):
            return self.dreh.schritte[self.dreh.i]
        if self.fraes is not None and not self.fraes.fertig and self.fraes.i < len(self.fraes.schritte):
            return self.fraes.schritte[self.fraes.i]
        return None

    def weiter_strecke(self, mm: float) -> float:
        """So viele Schritte, wie in ``mm`` Bahn passen (Eilgang zählt 1/EILGANG_FAKTOR).
        Ergebnis: nicht verbrauchter Rest (für das nächste Bild)."""
        while not self.fertig:
            st = self._naechster()
            if st is None:
                self.weiter(1)  # Abschluss (Restmaterial) rechnen
                break
            kosten = st.laenge / (EILGANG_FAKTOR if st.eil else 1.0)
            if kosten > mm:
                return mm
            mm -= kosten
            self.weiter(1)
        return 0.0

    def gehe_zu(self, idx: int) -> None:
        if idx <= self.n_dreh:
            if self.fraes is not None:
                self.fraes.reset()
            if self.dreh is not None:
                self.dreh.gehe_zu(idx)
        else:
            if self.dreh is not None and not self.dreh.fertig:
                self.dreh.bis_ende()
            if self.fraes is not None:
                self.fraes.gehe_zu(idx - self.n_dreh)

    @property
    def ereignisse(self) -> List[Ereignis]:
        out = list(self.dreh.ereignisse) if self.dreh else []
        if self.fraes:
            for e in self.fraes.ereignisse:
                out.append(Ereignis(e.art, e.text, e.z, e.r, e.schritt + self.n_dreh if e.schritt >= 0 else -1,
                                    e.zeile, e.t))
        return out

    @property
    def zeile(self) -> int:
        if self.phase == "fraes" and self.fraes is not None:
            return self.fraes.zeile
        return self.dreh.zeile if self.dreh else -1

    def zusammenfassung(self) -> str:
        if not self.fertig:
            return "Schritt {} von {}".format(self.i, self.n)
        ev = self.ereignisse
        schwer = [e for e in ev if e.schwer]
        info = [e for e in ev if not e.schwer]
        if schwer:
            return "✕  {} Problem(e): {}".format(len(schwer), schwer[0].text)
        if info:
            return "✓  Keine Kollision. {} Hinweis(e): {}".format(len(info), info[0].text)
        return "✓  Keine Kollision, kein Restmaterial."


class SimulationDialog(Dialog):
    def __init__(self, parent, fonts: Fonts, teil: Teil, res: Ergebnis, titel: str = "") -> None:
        super().__init__(parent, fonts, "Simulation")
        self.teil = teil
        self.ab = Ablauf(teil, res)
        self.sim = self.ab.dreh  # Drehen (für die Zeichnung)
        self.fs = self.ab.fraes
        self._job: Optional[str] = None
        self._spielt = False
        self._t_last = 0.0
        self._guthaben = 0.0  # noch nicht verbrauchte Bahn in mm
        self._bild_zeit = 0.0
        self._bahn_item: Optional[int] = None
        self._bahn_move: Optional[Tuple[int, bool]] = None
        self._trace_bis = 0
        self._markiert: set = set()
        self._modus = ""
        self._view: Optional[Tuple[float, float, float]] = None
        self._bild: Optional[tk.PhotoImage] = None
        self._size = (px(self, 1500), px(self, 960))
        self.minsize(px(self, 800), px(self, 500))
        bg = C["bg"]
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        head = tk.Frame(self, bg=bg)
        head.grid(row=0, column=0, sticky="ew", padx=px(self, 14), pady=(px(self, 10), px(self, 4)))
        tk.Label(head, text="Simulation" + ("  ·  " + titel if titel else ""), bg=bg, fg=C["text"],
                 font=fonts.title).pack(side="left")
        self.summary = tk.Label(head, text="", bg=bg, fg=C["muted"], font=fonts.ui, anchor="w")
        self.summary.pack(side="left", padx=(px(self, 16), 0))

        main = tk.Frame(self, bg=bg)
        main.grid(row=1, column=0, sticky="nsew", padx=px(self, 12))
        main.grid_rowconfigure(0, weight=1)
        main.grid_columnconfigure(0, weight=1)
        self.cv = tk.Canvas(main, bg=C["surface"], highlightthickness=1, highlightbackground=C["border"])
        self.cv.grid(row=0, column=0, sticky="nsew")
        self.cv.bind("<Configure>", lambda e: self._neu_zeichnen())

        side = tk.Frame(main, bg=bg, width=px(self, 380))
        side.grid(row=0, column=1, sticky="nsew", padx=(px(self, 12), 0))
        side.grid_propagate(False)
        side.grid_columnconfigure(0, weight=1)
        side.grid_rowconfigure(3, weight=1)
        side.grid_rowconfigure(5, weight=1)
        tk.Label(side, text="WERKZEUG", bg=bg, fg=C["faint"], font=fonts.small_bold).grid(row=0, column=0,
                                                                                         sticky="w")
        self.wz_info = tk.Label(side, text="", bg=C["surface"], fg=C["text"], font=fonts.ui, anchor="nw",
                                justify="left", padx=px(self, 8), pady=px(self, 6), wraplength=px(self, 350))
        self.wz_info.grid(row=1, column=0, sticky="ew", pady=(0, px(self, 8)))
        tk.Label(side, text="NC-PROGRAMM", bg=bg, fg=C["faint"], font=fonts.small_bold).grid(row=2, column=0,
                                                                                            sticky="w")
        box = tk.Frame(side, bg=C["surface"])
        box.grid(row=3, column=0, sticky="nsew", pady=(0, px(self, 8)))
        self.code_text = tk.Text(box, font=(MONO_FAMILY, 10), wrap="none", bg=C["surface_alt"], fg=C["text"],
                                 relief="flat", highlightthickness=0, width=20, height=8)
        sb = ttk.Scrollbar(box, orient="vertical", command=self.code_text.yview)
        self.code_text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.code_text.pack(side="left", fill="both", expand=True)
        self.code_text.insert("1.0", res.code)
        self.code_text.tag_configure("aktiv", background=C["select"])
        self.code_text.configure(state="disabled")
        tk.Label(side, text="MELDUNGEN (antippen = hinspringen)", bg=bg, fg=C["faint"], font=fonts.small_bold).grid(
            row=4, column=0, sticky="w")
        lbox = tk.Frame(side, bg=C["surface"])
        lbox.grid(row=5, column=0, sticky="nsew")
        self.liste = ttk.Treeview(lbox, columns=("text",), show="", selectmode="browse", height=5)
        self.liste.column("text", width=px(self, 340))
        lsb = ttk.Scrollbar(lbox, orient="vertical", command=self.liste.yview)
        self.liste.configure(yscrollcommand=lsb.set)
        lsb.pack(side="right", fill="y")
        self.liste.pack(side="left", fill="both", expand=True)
        self.liste.bind("<<TreeviewSelect>>", self._ereignis_gewaehlt)
        self.liste.tag_configure("schwer", foreground=C["error"])
        self.liste.tag_configure("info", foreground=C["warn"])

        bar = tk.Frame(self, bg=bg)
        bar.grid(row=2, column=0, sticky="ew", padx=px(self, 12), pady=px(self, 10))
        ttk.Button(bar, text="|◀", width=3, style="Small.TButton", command=self.anfang).pack(side="left")
        self.btn_play = ttk.Button(bar, text="▶  Abspielen", style="Primary.TButton", command=self.spielen)
        self.btn_play.pack(side="left", padx=(px(self, 8), 0))
        ttk.Button(bar, text="Schritt ▶", style="Small.TButton", command=lambda: self.schritt(1)).pack(
            side="left", padx=(px(self, 6), 0))
        ttk.Button(bar, text="▶|", width=3, style="Small.TButton", command=self.ende).pack(
            side="left", padx=(px(self, 6), 0))
        if self.fs is not None and self.sim is not None:
            ttk.Button(bar, text="Zum Fräsen", style="Small.TButton", command=self.zum_fraesen).pack(
                side="left", padx=(px(self, 6), 0))
        tk.Label(bar, text="Tempo", bg=bg, fg=C["muted"], font=fonts.ui).pack(side="left", padx=(px(self, 12), 4))
        self._tempo = tk.DoubleVar(master=self, value=TEMPO_START)
        ttk.Scale(bar, from_=0, to=len(TEMPO_MM_S) - 1, orient="horizontal", length=px(self, 130),
                  variable=self._tempo, command=lambda _v: self._tempo_text()).pack(side="left")
        self.tempo_lbl = tk.Label(bar, text="", bg=bg, fg=C["muted"], font=fonts.small, width=9, anchor="w")
        self.tempo_lbl.pack(side="left", padx=(px(self, 6), 0))
        self._tempo_text()
        self.zeige_halter = tk.BooleanVar(master=self, value=True)
        self.zeige_bahn = tk.BooleanVar(master=self, value=True)
        self.lupe = tk.BooleanVar(master=self, value=False)
        for text, var in (("Halter", self.zeige_halter), ("Bahn", self.zeige_bahn), ("Lupe", self.lupe)):
            ttk.Checkbutton(bar, text=text, variable=var, command=self._neu_zeichnen).pack(
                side="left", padx=(px(self, 8), 0))
        ttk.Button(bar, text="Schließen", command=self.cancel).pack(side="right")
        self._liste_fuellen()
        self._status()

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
        return None

    def destroy(self) -> None:
        self._stop()
        super().destroy()

    # -- Steuerung ----------------------------------------------------------------

    def _stop(self) -> None:
        self._spielt = False
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None
        if hasattr(self, "btn_play"):
            self.btn_play.configure(text="▶  Abspielen")

    def mm_je_s(self) -> float:
        return float(TEMPO_MM_S[max(0, min(len(TEMPO_MM_S) - 1, int(round(self._tempo.get()))))])

    def _tempo_text(self) -> None:
        self.tempo_lbl.configure(text="{:g} mm/s".format(self.mm_je_s()))

    def spielen(self) -> None:
        if self._spielt:
            self._stop()
            return
        if self.ab.fertig:
            self.anfang()
        self._spielt = True
        self.btn_play.configure(text="❚❚  Pause")
        self._t_last = time.perf_counter() - BILD_MS / 1000.0  # erstes Bild bewegt schon
        self._guthaben = 0.0
        self._tick()

    def _tick(self) -> None:
        """Ein Bild: so viel Bahn abfahren, wie seit dem letzten Bild bei gewähltem Tempo vergangen ist."""
        self._job = None
        if not self._spielt:
            return
        now = time.perf_counter()
        dt = min(0.25, max(0.0, now - self._t_last))  # nach einer Pause nicht nachholen
        self._t_last = now
        self._guthaben = self.ab.weiter_strecke(self._guthaben + dt * self.mm_je_s())
        self._aktualisieren()
        if self.ab.fertig:
            self._stop()
            self._aktualisieren()
            return
        # Rechenzeit abziehen, damit es bei ~60 Bildern je Sekunde bleibt
        rest = BILD_MS - int((time.perf_counter() - now) * 1000)
        self._job = self.after(max(1, rest), self._tick)

    def schritt(self, n: int = 1) -> None:
        self._stop()
        self.ab.weiter(n)
        self._aktualisieren()

    def anfang(self) -> None:
        self._stop()
        self.ab.reset()
        self._neu_zeichnen()
        self._status()

    def ende(self) -> None:
        self._stop()
        self.ab.bis_ende()
        self._aktualisieren()

    def zum_fraesen(self) -> None:
        self.gehe_zu(self.ab.n_dreh + 1)

    def gehe_zu(self, index: int) -> None:
        self._stop()
        back = index < self.ab.i
        self.ab.gehe_zu(index)
        if back:
            self._neu_zeichnen()
        self._aktualisieren()

    def _ereignis_gewaehlt(self, _e=None) -> None:
        sel = self.liste.selection()
        if not sel:
            return
        e = self._ereignisse[int(sel[0])]
        if e.schritt >= 0:
            self.gehe_zu(e.schritt + 1)
        if self.ab.phase == "dreh":
            self._marker(e)

    # -- Anzeige allgemein --------------------------------------------------------

    def _liste_fuellen(self) -> None:
        self._ereignisse: List[Ereignis] = self.ab.ereignisse
        self.liste.delete(*self.liste.get_children())
        for n, e in enumerate(self._ereignisse):
            self.liste.insert("", "end", iid=str(n), values=(("✕ " if e.schwer else "• ") + e.text,),
                              tags=("schwer" if e.schwer else "info",))

    def _status(self) -> None:
        txt = self.ab.zusammenfassung()
        color = C["error"] if txt.startswith("✕") else (C["ok"] if txt.startswith("✓") else C["muted"])
        self.summary.configure(text=txt, fg=color)
        if self.ab.phase == "dreh" and self.sim is not None:
            pos = self.sim.position
            if pos is not None:
                f, _s = pos
                h, p = f.halter, f.platte
                self.wz_info.configure(text="T{}  ·  {}\n{}\n{}  ·  {}".format(
                    f.t, p.code, p.beschreibung, h.code, h.beschreibung))
        elif self.fs is not None:
            pos = self.fs.position
            if pos is not None:
                op, w, winkel, _p = pos
                fr = self.teil.fraesungen[op.nr - 1] if 0 < op.nr <= len(self.teil.fraesungen) else None
                was = FEATURES.get(fr.art, "") if fr else ""
                strat = STRATEGIEN.get(fr.art, {}).get(strategie(fr), "") if fr and not op.entgraten else ""
                self.wz_info.configure(text="T{}  ·  {}  ·  Ø{:g}\n{}{}  ·  C {:g}°\n{}".format(
                    op.t, w.name, 2 * w.r, was, " entgraten" if op.entgraten else "", winkel, strat))
        line = self.ab.zeile
        self.code_text.tag_remove("aktiv", "1.0", "end")
        if line >= 0:
            self.code_text.tag_add("aktiv", "{}.0".format(line + 1), "{}.0".format(line + 2))
            self.code_text.see("{}.0".format(line + 1))
        if len(self.ab.ereignisse) != len(self._ereignisse):
            self._liste_fuellen()

    def _aktualisieren(self) -> None:
        if self.ab.phase != self._modus:
            self._neu_zeichnen()
        else:
            self._dynamisch()
        self._status()

    def _neu_zeichnen(self) -> None:
        if self.cv.winfo_width() < 50:
            return
        self._modus = self.ab.phase
        if self._modus == "dreh" and self.sim is not None:
            self._dreh_neu()
        else:
            self._fraes_neu()

    def _dynamisch(self) -> None:
        if self._modus == "dreh" and self.sim is not None:
            self._dreh_dynamisch()
        else:
            self._fraes_dynamisch()

    # -- Drehen -------------------------------------------------------------------

    def _transform(self) -> None:
        W, H = self.cv.winfo_width(), self.cv.winfo_height()
        sim = self.sim
        R = sim.R
        z_lo, z_hi = sim.z_lo, sim.z_hi
        m = px(self, 30)
        s = min((W - 2 * m) / (z_hi - z_lo), (H - 2 * m) / (2.6 * R))
        cx = m + (W - 2 * m - (z_hi - z_lo) * s) / 2 - z_lo * s
        cy = H * 0.58
        self._base = (s, cx, cy)

    def P(self, z: float, r: float) -> Tuple[float, float]:
        s, cx, cy = self._view or self._base
        return cx + z * s, cy - r * s

    def _dreh_neu(self) -> None:
        cv = self.cv
        cv.delete("all")
        self._trace_bis = 0
        self._markiert = set()
        self._transform()
        self._view = None
        if self.lupe.get():
            pos = self.sim.position
            if pos is not None:
                _f, sp = pos
                s0, _cx, _cy = self._base
                s = s0 * 5
                W, H = cv.winfo_width(), cv.winfo_height()
                self._view = (s, W / 2 - sp[0] * s, H / 2 + sp[1] * s)
        sim = self.sim
        self.achse_item = cv.create_line(*self.P(sim.z_lo, 0), *self.P(sim.z_hi, 0), fill=C["faint"],
                                         dash=(12, 4, 2, 4))
        x0, y0 = self.P(sim.front, sim.R)
        x1, y1 = self.P(sim.stock_end, -sim.R)
        cv.create_rectangle(x0, y0, x1, y1, outline=C["faint"], dash=(4, 3))
        self.mat_oben = cv.create_polygon(0, 0, 0, 0, 0, 0, fill=MATERIAL, outline=MATERIAL_RAND, width=1)
        self.mat_unten = cv.create_polygon(0, 0, 0, 0, 0, 0, fill=MATERIAL, outline="", stipple="gray50")
        self.bohr_item = cv.create_polygon(0, 0, 0, 0, 0, 0, fill=cv.cget("bg"), outline="")
        cv.tag_raise(self.achse_item)
        ziel: List[float] = []
        for z, r in zip(sim.zc[::4], sim.ziel[::4]):
            if r >= 0:
                ziel.extend(self.P(z, r))
        if len(ziel) >= 4:
            cv.create_line(*ziel, fill=ZIEL, dash=(5, 3), width=1)
        if sim.gin.elems:  # Fertigkontur innen
            innen: List[float] = []
            for z, r in zip(sim.zc[::4], sim.ziel_innen[::4]):
                if z <= 0 and 0 < r < float("inf"):
                    innen.extend(self.P(z, r))
            if len(innen) >= 4:
                cv.create_line(*innen, fill=ZIEL, dash=(5, 3), width=1)
        self.halter_item = cv.create_polygon(0, 0, 0, 0, 0, 0, fill=HALTER_FARBE, outline="#4B5563", stipple="gray50")
        self.platte_item = cv.create_polygon(0, 0, 0, 0, 0, 0, fill=PLATTE_FARBE, outline="#5C4300", width=1)
        self.tip_item = cv.create_oval(0, 0, 0, 0, fill="#5C4300", outline="")
        cv.create_text(px(self, 10), px(self, 8), anchor="nw", fill=C["faint"], font=self.fonts.small_bold,
                       text="Drehen · Schnitt durch die Drehachse · gelb Platte, grau Halter, blau gestrichelt "
                            "Fertigkontur")
        self._dreh_dynamisch()

    def _dreh_dynamisch(self) -> None:
        cv = self.cv
        if not hasattr(self, "mat_oben") or not cv.find_withtag(self.mat_oben):
            return
        sim = self.sim
        if self.lupe.get() and self._view is not None:
            pos = sim.position
            if pos is not None:
                s, cx, cy = self._view
                W, H = cv.winfo_width(), cv.winfo_height()
                x, y = cx + pos[1][0] * s, cy - pos[1][1] * s
                if abs(x - W / 2) > W * 0.3 or abs(y - H / 2) > H * 0.3:
                    self.after_idle(self._neu_zeichnen)
        pts = sim.kontur_jetzt(0.2 if not self._view else 0.05)
        oben: List[float] = []
        unten: List[float] = []
        if pts:
            for z, r in pts:
                oben.extend(self.P(z, r))
                unten.extend(self.P(z, -r))
            for lst in (oben, unten):
                lst.extend(self.P(pts[-1][0], 0))
                lst.extend(self.P(pts[0][0], 0))
        cv.coords(self.mat_oben, *(oben if len(oben) >= 6 else [0, 0, 0, 0, 0, 0]))
        cv.coords(self.mat_unten, *(unten if len(unten) >= 6 else [0, 0, 0, 0, 0, 0]))
        bohrung = sim.bohrung_jetzt(0.2 if not self._view else 0.05)
        loch: List[float] = []
        for z, r in bohrung + [(z, -r) for z, r in reversed(bohrung)]:
            loch.extend(self.P(z, r))
        cv.coords(self.bohr_item, *(loch if len(loch) >= 6 else [0, 0, 0, 0, 0, 0]))
        pos = sim.position
        if pos is not None:
            f, s = pos
            platte, halter = f.umriss(s)
            cv.coords(self.platte_item, *[c for p in platte for c in self.P(*p)])
            if self.zeige_halter.get():
                cv.coords(self.halter_item, *[c for p in halter for c in self.P(*p)])
            else:
                cv.coords(self.halter_item, 0, 0, 0, 0, 0, 0)
            x, y = self.P(s[0] + f.off[0], s[1] + f.off[1])
            cv.coords(self.tip_item, x - 2, y - 2, x + 2, y + 2)
        self._bahn()
        for n, e in enumerate(sim.ereignisse):
            if n in self._markiert or e.art == "hinweis":
                continue
            if (0 <= e.schritt < sim.i) or (e.schritt < 0 and sim.fertig):
                self._marker(e, tag="marker")
                self._markiert.add(n)
        cv.tag_raise(self.halter_item)
        cv.tag_raise(self.platte_item)
        cv.tag_raise(self.tip_item)

    def _bahn(self) -> None:
        """Werkzeugbahn nachziehen: eine Linie je NC-Satz (nicht je Schritt – bleibt auch lang flüssig)."""
        cv = self.cv
        if not self.zeige_bahn.get():
            cv.delete("bahn")
            self._trace_bis = 0
            self._bahn_item = self._bahn_move = None
            return
        if self._trace_bis == 0:
            self._bahn_item = self._bahn_move = None
        sim = self.sim
        neu: List[float] = []
        for k in range(self._trace_bis, min(sim.i, len(sim.schritte))):
            st = sim.schritte[k]
            f = sim.formen[st.t]
            b = self.P(st.b[0] + f.off[0], st.b[1] + f.off[1])
            if self._bahn_move == (st.move, st.eil) and self._bahn_item is not None:
                neu.extend(b)
                continue
            if neu and self._bahn_item is not None:
                cv.coords(self._bahn_item, *(cv.coords(self._bahn_item) + neu))
                neu = []
            a = self.P(st.a[0] + f.off[0], st.a[1] + f.off[1])
            self._bahn_item = cv.create_line(*a, *b, fill=BAHN_G0 if st.eil else BAHN_G1, width=1,
                                             dash=(3, 3) if st.eil else (), tags=("bahn",))
            self._bahn_move = (st.move, st.eil)
        if neu and self._bahn_item is not None:
            cv.coords(self._bahn_item, *(cv.coords(self._bahn_item) + neu))
        self._trace_bis = sim.i
        cv.tag_lower("bahn", self.halter_item)

    def _marker(self, e: Ereignis, tag: str = "auswahl") -> None:
        if self._modus != "dreh":
            return
        cv = self.cv
        x, y = self.P(e.z, e.r)
        r = px(self, 7)
        if e.schwer:
            cv.create_line(x - r, y - r, x + r, y + r, fill=C["error"], width=3, tags=(tag,))
            cv.create_line(x - r, y + r, x + r, y - r, fill=C["error"], width=3, tags=(tag,))
        else:
            cv.create_oval(x - r, y - r, x + r, y + r, outline=C["warn"], width=2, tags=(tag,))

    # -- Fräsen -------------------------------------------------------------------

    def _fraes_neu(self) -> None:
        cv = self.cv
        cv.delete("all")
        fs = self.fs
        if fs is None:
            return
        W, H = cv.winfo_width(), cv.winfo_height()
        half = W * 0.42
        R = fs.R
        self._fe = (half / 2, H / 2 + px(self, 8), min(half, H - px(self, 40)) / (2 * (R + 6)))
        cv.create_text(px(self, 10), px(self, 8), anchor="nw", fill=C["faint"], font=self.fonts.small_bold,
                       text="Fräsen · links Querschnitt (Stirnansicht, 0° oben) · rechts Draufsicht der Winkellage")
        cv.create_line(half, px(self, 26), half, H - px(self, 8), fill=C["border"])
        self.f_titel_l = cv.create_text(px(self, 10), px(self, 26), anchor="nw", fill=C["muted"],
                                        font=self.fonts.small, text="")
        self.f_titel_r = cv.create_text(half + px(self, 10), px(self, 26), anchor="nw", fill=C["muted"],
                                        font=self.fonts.small, text="")
        cx, cy, s = self._fe
        self.f_kreis = cv.create_oval(0, 0, 0, 0, outline=C["faint"], dash=(4, 3))
        self.f_schnitt = cv.create_polygon(0, 0, 0, 0, 0, 0, fill=MATERIAL, outline=MATERIAL_RAND)
        self.f_wz_l = cv.create_polygon(0, 0, 0, 0, 0, 0, fill=FRAESER_FARBE, outline="#5C1A00", stipple="gray50")
        cv.create_line(cx - (R + 4) * s, cy, cx + (R + 4) * s, cy, fill=C["faint"], dash=(8, 3, 2, 3))
        cv.create_line(cx, cy - (R + 4) * s, cx, cy + (R + 4) * s, fill=C["faint"], dash=(8, 3, 2, 3))
        # Draufsicht: Bild aus dem Höhenraster
        rw, rh = W - half - px(self, 30), H - px(self, 70)
        cell = max(1, int(min(rw / max(1, fs.nz), rh / max(1, fs.ny))))
        self._cell = cell
        self._bild_pos = (half + px(self, 15) + (rw - fs.nz * cell) / 2, px(self, 50) + (rh - fs.ny * cell) / 2)
        self._bild = tk.PhotoImage(master=self, width=fs.nz * cell, height=fs.ny * cell)
        self.f_bild = cv.create_image(*self._bild_pos, image=self._bild, anchor="nw")
        # Abwicklung des Mantels (Nut auf dem Mantel): Z nach rechts, C 0…360° nach oben
        self._mbild = None
        if fs.nc:
            mcell = max(1, int(rw / max(1, fs.nz)))
            schritt = max(1, int(math.ceil(fs.nc / max(1.0, rh))))
            zeilen = int(math.ceil(fs.nc / schritt))
            self._mgeo = (mcell, schritt, zeilen, half + px(self, 15) + (rw - fs.nz * mcell) / 2,
                          px(self, 50) + (rh - zeilen) / 2)
            self._mbild = tk.PhotoImage(master=self, width=fs.nz * mcell, height=zeilen)
        self.f_wz_r = cv.create_oval(0, 0, 0, 0, outline=FRAESER_FARBE, width=2)
        self.f_achse_r = cv.create_line(0, 0, 0, 0, fill=FRAESER_FARBE)
        self._fraes_dynamisch()

    def _fraes_dynamisch(self) -> None:
        cv = self.cv
        fs = self.fs
        if fs is None or not hasattr(self, "f_schnitt") or not cv.find_withtag(self.f_schnitt):
            return
        cx, cy, s = self._fe
        pos = fs.position
        if pos is None:
            return
        op, w, winkel, (x, y, z) = pos
        z_cut = min(z, -0.05)
        r_now = max(0.0, fs._r(z_cut))
        cv.coords(self.f_kreis, cx - r_now * s, cy - r_now * s, cx + r_now * s, cy + r_now * s)
        # Querschnitt: voller Kreis als Material, darüber die weggefrästen Bereiche in Hintergrundfarbe.
        # Erst alle Ränder, dann alle Flächen ohne Rand – so bleiben nur die Kanten zum Material sichtbar.
        kreis = [c for k in range(180) for c in (cx + r_now * s * math.cos(2 * math.pi * k / 180),
                                                  cy - r_now * s * math.sin(2 * math.pi * k / 180))]
        cv.coords(self.f_schnitt, *(kreis if r_now > 0 else [0, 0, 0, 0, 0, 0]))
        cv.delete("abtrag")
        polys = [[c for px_, py_ in poly for c in (cx + px_ * s, cy - py_ * s)] for poly in fs.abtrag(z_cut)]
        polys = [p for p in polys if len(p) >= 6]
        bg = cv.cget("bg")
        for p in polys:
            cv.create_polygon(*p, fill="", outline=MATERIAL_RAND, width=2, tags=("abtrag",))
        for p in polys:
            cv.create_polygon(*p, fill=bg, outline="", tags=("abtrag",))
        cv.tag_raise("abtrag", self.f_schnitt)
        cv.delete("loch")
        for a, hx, hr, hz in fs.bohrungen:
            if hz < z_cut:
                d, _e = richtung(a)
                bx, by = cx + hx * d[0] * s, cy - hx * d[1] * s
                cv.create_oval(bx - hr * s, by - hr * s, bx + hr * s, by + hr * s, fill=C["surface"],
                               outline=MATERIAL_RAND, tags=("loch",))
        d, e = richtung(winkel)
        if op.lage in ("radial", "mantel"):
            outer = max(x, fs.R) + 12
            corners = [(x, y - w.r), (outer, y - w.r), (outer, y + w.r), (x, y + w.r)]
            poly = []
            for xl, yl in corners:
                gx, gy = xl * d[0] + yl * e[0], xl * d[1] + yl * e[1]
                poly += [cx + gx * s, cy - gy * s]
            cv.coords(self.f_wz_l, *poly)
        else:
            gx, gy = x * d[0] + y * e[0], x * d[1] + y * e[1]
            poly = []
            for k in range(16):
                t = 2 * math.pi * k / 16
                poly += [cx + (gx + w.r * math.cos(t)) * s, cy - (gy + w.r * math.sin(t)) * s]
            cv.coords(self.f_wz_l, *poly)
        cv.tag_raise("loch")
        cv.tag_raise(self.f_wz_l)
        cv.itemconfigure(self.f_titel_l, text="Schnitt bei Z{:.1f}".format(z_cut))
        a = round(winkel, 3)
        bx0, by0 = self._bild_pos
        cell = self._cell
        if op.lage == "mantel" and self._mbild is not None:
            mcell, schritt, zeilen, mx0, my0 = self._mgeo
            cv.itemconfigure(self.f_bild, image=self._mbild)
            cv.coords(self.f_bild, mx0, my0)
            jetzt = time.perf_counter()
            if not self._spielt or jetzt - self._bild_zeit > 0.04 or self.ab.fertig:
                self._bild_zeit = jetzt
                self._mantel_bild_fuellen()
            tx = mx0 + (z - fs.z0) / fs.g * mcell
            ty = my0 + zeilen - (winkel % 360.0) / fs.dc / schritt
            rz = w.r / fs.g * mcell
            rc = math.degrees(w.r / max(x, 1.0)) / fs.dc / schritt
            cv.coords(self.f_wz_r, tx - rz, ty - rc, tx + rz, ty + rc)
            cv.coords(self.f_achse_r, tx - 3, ty, tx + 3, ty)
            cv.itemconfigure(self.f_titel_r, text="Abwicklung (Z nach rechts, C 0…360° nach oben) · dunkler = "
                                                  "tiefer · Werkzeug C{:.1f}° X{:.2f}".format(winkel % 360.0, 2 * x))
            return
        if self._mbild is not None:
            cv.itemconfigure(self.f_bild, image=self._bild)
            cv.coords(self.f_bild, *self._bild_pos)
        if op.lage == "radial" and a in fs.h:
            # Draufsicht neu aufbauen ist teuer: beim Abspielen höchstens ~12-mal je Sekunde
            jetzt = time.perf_counter()
            if not self._spielt or jetzt - self._bild_zeit > 0.04 or self.ab.fertig:
                self._bild_zeit = jetzt
                self._bild_fuellen(fs.h[a], fs.orig[a], a)
            tx = bx0 + (z - fs.z0) / fs.g * cell
            ty = by0 + (fs.ny - (y - fs.y0) / fs.g) * cell
            rr = w.r / fs.g * cell
            cv.coords(self.f_wz_r, tx - rr, ty - rr, tx + rr, ty + rr)
            cv.coords(self.f_achse_r, tx - 3, ty, tx + 3, ty)
            cv.itemconfigure(self.f_titel_r, text="Draufsicht C{:g}° (Z nach rechts, Y nach oben) · dunkler = "
                                                  "tiefer · Werkzeug X{:.2f}".format(winkel, 2 * x))
        else:
            cv.coords(self.f_wz_r, 0, 0, 0, 0)
            cv.coords(self.f_achse_r, 0, 0, 0, 0)
            cv.itemconfigure(self.f_titel_r, text="Stirnbohrung C{:g}° · Tiefe Z{:.2f}".format(winkel, z))

    def _mantel_bild_fuellen(self) -> None:
        """Abwicklung malen – nur den geänderten Z-Streifen, wenn möglich."""
        fs = self.fs
        mcell, schritt, zeilen, _x0, _y0 = self._mgeo
        pal = _palette()
        stand = ("mantel", fs.generation, id(self._mbild))
        teil = fs.geaendert.pop("mantel", None)
        if getattr(self, "_mbild_stand", None) == stand and teil is not None:
            i0, i1 = teil
        elif getattr(self, "_mbild_stand", None) == stand:
            return
        else:
            i0, i1 = 0, fs.nz - 1
        self._mbild_stand = stand
        rows = []
        for zr in range(zeilen - 1, -1, -1):
            kc = min(fs.nc - 1, zr * schritt)
            cols = []
            for iz in range(i0, i1 + 1):
                h = fs.mh[iz][kc]
                c = "#FFFFFF" if h <= 0 else _mantel_farbe(pal, fs.mh_orig[iz][kc] - h)
                cols.extend([c] * mcell)
            rows.append("{" + " ".join(cols) + "}")
        self._mbild.put(" ".join(rows), to=(i0 * mcell, 0))

    def _bild_fuellen(self, grid, orig, winkel: Optional[float] = None) -> None:
        """Draufsicht malen – nur den seit dem letzten Mal geänderten Streifen, wenn möglich."""
        fs = self.fs
        cell = self._cell
        pal = _palette()
        stand = (winkel, fs.generation, id(self._bild))
        teil = fs.geaendert.pop(winkel, None) if winkel is not None else None
        if getattr(self, "_bild_stand", None) == stand and teil is not None:
            i0, i1 = teil
        elif getattr(self, "_bild_stand", None) == stand:
            return  # nichts geändert
        else:
            i0, i1 = 0, fs.nz - 1
            if winkel is not None:
                fs.geaendert.pop(winkel, None)
        self._bild_stand = stand
        rows = []
        for j in range(fs.ny - 1, -1, -1):
            cols = []
            for iz in range(i0, i1 + 1):
                h = grid[iz][j]
                if h <= NONE / 2:
                    c = "#FFFFFF"
                else:
                    depth = orig[iz][j] - h
                    c = pal[min(len(pal) - 1, int(depth / 4.0 * (len(pal) - 1) + 0.5))] if depth > 0.03 else MATERIAL
                cols.extend([c] * cell)
            row = "{" + " ".join(cols) + "}"
            rows.extend([row] * cell)
        self._bild.put(" ".join(rows), to=(i0 * cell, 0))


def _mantel_farbe(pal: List[str], tiefe: float) -> str:
    return pal[min(len(pal) - 1, int(tiefe / 4.0 * (len(pal) - 1) + 0.5))] if tiefe > 0.03 else MATERIAL


_PAL: List[str] = []


def _palette() -> List[str]:
    """Hell (wenig) bis dunkelblau (tief)."""
    if not _PAL:
        a, b = (0xBF, 0xD7, 0xF5), (0x12, 0x47, 0x9D)
        for k in range(24):
            t = k / 23
            _PAL.append("#{:02X}{:02X}{:02X}".format(*(int(a[i] + (b[i] - a[i]) * t) for i in range(3))))
    return _PAL
