"""Fenster „Simulation“: Drehbearbeitung mit Plattenform abspielen."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import List, Optional, Tuple

from .dialogs import Dialog
from .kontur import Ergebnis, Teil
from .simulation import DrehSimulation, Ereignis
from .theme import C, MONO_FAMILY, Fonts, px

TEMPI = [1, 2, 5, 10, 25, 100]  # Schritte je Bild
PLATTE_FARBE = "#E8B923"
HALTER_FARBE = "#8D99A6"
MATERIAL = "#D7DEE8"
MATERIAL_RAND = "#7B8A9C"
ZIEL = C["primary"]
BAHN_G1 = "#0E8A5B"
BAHN_G0 = "#D9480F"


class SimulationDialog(Dialog):
    def __init__(self, parent, fonts: Fonts, teil: Teil, res: Ergebnis, titel: str = "") -> None:
        super().__init__(parent, fonts, "Simulation")
        self.sim = DrehSimulation(teil, res)
        self.code = res.code.split("\n")
        self._job: Optional[str] = None
        self._spielt = False
        self._trace_bis = 0
        self._markiert: set = set()
        self._view: Optional[Tuple[float, float, float]] = None
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
        self.btn_start = ttk.Button(bar, text="|◀", width=4, command=self.anfang)
        self.btn_start.pack(side="left")
        self.btn_play = ttk.Button(bar, text="▶  Abspielen", style="Primary.TButton", command=self.spielen)
        self.btn_play.pack(side="left", padx=(px(self, 8), 0))
        ttk.Button(bar, text="Schritt ▶", command=lambda: self.schritt(1)).pack(side="left", padx=(px(self, 8), 0))
        ttk.Button(bar, text="▶|", width=4, command=self.ende).pack(side="left", padx=(px(self, 8), 0))
        tk.Label(bar, text="Tempo", bg=bg, fg=C["muted"], font=fonts.ui).pack(side="left", padx=(px(self, 16), 4))
        self._tempo = tk.DoubleVar(master=self, value=3)
        ttk.Scale(bar, from_=0, to=len(TEMPI) - 1, orient="horizontal", length=px(self, 160),
                  variable=self._tempo).pack(side="left")
        self.zeige_halter = tk.BooleanVar(master=self, value=True)
        self.zeige_bahn = tk.BooleanVar(master=self, value=True)
        self.lupe = tk.BooleanVar(master=self, value=False)
        for text, var in (("Halter", self.zeige_halter), ("Bahn", self.zeige_bahn), ("Lupe", self.lupe)):
            ttk.Checkbutton(bar, text=text, variable=var, command=self._neu_zeichnen).pack(
                side="left", padx=(px(self, 12), 0))
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

    def spielen(self) -> None:
        if self._spielt:
            self._stop()
            return
        if self.sim.fertig:
            self.anfang()
        self._spielt = True
        self.btn_play.configure(text="❚❚  Pause")
        self._tick()

    def _tick(self) -> None:
        self._job = None
        if not self._spielt:
            return
        self.sim.weiter(TEMPI[max(0, min(len(TEMPI) - 1, int(round(self._tempo.get()))))])
        self._aktualisieren()
        if self.sim.fertig:
            self._stop()
            return
        self._job = self.after(30, self._tick)

    def schritt(self, n: int = 1) -> None:
        self._stop()
        self.sim.weiter(n)
        self._aktualisieren()

    def anfang(self) -> None:
        self._stop()
        self.sim.reset()
        self._trace_bis = 0
        self._markiert = set()
        self.cv.delete("bahn")
        self.cv.delete("marker")
        self._aktualisieren()

    def ende(self) -> None:
        self._stop()
        self.sim.bis_ende()
        self._aktualisieren()

    def gehe_zu(self, index: int) -> None:
        self._stop()
        if index < self.sim.i:
            self._trace_bis = 0
            self._markiert = set()
            self.cv.delete("bahn")
            self.cv.delete("marker")
        self.sim.gehe_zu(index)
        self._aktualisieren()

    def _ereignis_gewaehlt(self, _e=None) -> None:
        sel = self.liste.selection()
        if not sel:
            return
        e = self._ereignisse[int(sel[0])]
        if e.schritt >= 0:
            self.gehe_zu(e.schritt + 1)
        self._marker(e)

    # -- Anzeige ------------------------------------------------------------------

    def _liste_fuellen(self) -> None:
        self._ereignisse: List[Ereignis] = list(self.sim.ereignisse)
        self.liste.delete(*self.liste.get_children())
        for n, e in enumerate(self._ereignisse):
            self.liste.insert("", "end", iid=str(n), values=(("✕ " if e.schwer else "• ") + e.text,),
                              tags=("schwer" if e.schwer else "info",))

    def _status(self) -> None:
        sim = self.sim
        txt = sim.zusammenfassung()
        color = C["error"] if txt.startswith("✕") else (C["ok"] if txt.startswith("✓") else C["muted"])
        self.summary.configure(text=txt, fg=color)
        pos = sim.position
        if pos is not None:
            f, _s = pos
            h, p = f.halter, f.platte
            self.wz_info.configure(text="T{}  ·  {}\n{}\n{}  ·  {}".format(
                f.t, p.code, p.beschreibung, h.code, h.beschreibung))
        line = sim.zeile
        self.code_text.tag_remove("aktiv", "1.0", "end")
        if line >= 0:
            self.code_text.tag_add("aktiv", "{}.0".format(line + 1), "{}.0".format(line + 2))
            self.code_text.see("{}.0".format(line + 1))
        if len(sim.ereignisse) != len(self._ereignisse):
            self._liste_fuellen()

    def _aktualisieren(self) -> None:
        self._dynamisch()
        self._status()

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

    def _neu_zeichnen(self) -> None:
        cv = self.cv
        if cv.winfo_width() < 50:
            return
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
        # Achse, Rohteil, Fertigkontur
        cv.create_line(*self.P(sim.z_lo, 0), *self.P(sim.z_hi, 0), fill=C["faint"], dash=(12, 4, 2, 4))
        x0, y0 = self.P(sim.front, sim.R)
        x1, y1 = self.P(sim.stock_end, -sim.R)
        cv.create_rectangle(x0, y0, x1, y1, outline=C["faint"], dash=(4, 3))
        self.mat_oben = cv.create_polygon(0, 0, 0, 0, 0, 0, fill=MATERIAL, outline=MATERIAL_RAND, width=1)
        self.mat_unten = cv.create_polygon(0, 0, 0, 0, 0, 0, fill=MATERIAL, outline="", stipple="gray50")
        ziel: List[float] = []
        for z, r in zip(sim.zc[::4], sim.ziel[::4]):
            if r >= 0:
                ziel.extend(self.P(z, r))
        if len(ziel) >= 4:
            cv.create_line(*ziel, fill=ZIEL, dash=(5, 3), width=1)
        self.halter_item = cv.create_polygon(0, 0, 0, 0, 0, 0, fill=HALTER_FARBE, outline="#4B5563", stipple="gray50")
        self.platte_item = cv.create_polygon(0, 0, 0, 0, 0, 0, fill=PLATTE_FARBE, outline="#5C4300", width=1)
        self.tip_item = cv.create_oval(0, 0, 0, 0, fill="#5C4300", outline="")
        cv.create_text(px(self, 10), px(self, 8), anchor="nw", fill=C["faint"], font=self.fonts.small_bold,
                       text="Schnitt durch die Drehachse · gelb Platte, grau Halter, blau gestrichelt Fertigkontur")
        self._dynamisch()

    def _dynamisch(self) -> None:
        cv = self.cv
        if not hasattr(self, "mat_oben"):
            return
        if self.lupe.get():  # Lupe folgt dem Werkzeug
            self._neu_zeichnen_lupe()
        sim = self.sim
        pts = sim.kontur_jetzt(0.2 if not self._view else 0.05)
        oben: List[float] = []
        unten: List[float] = []
        if pts:
            for z, r in pts:
                oben.extend(self.P(z, r))
            oben.extend(self.P(pts[-1][0], 0))
            oben.extend(self.P(pts[0][0], 0))
            for z, r in pts:
                unten.extend(self.P(z, -r))
            unten.extend(self.P(pts[-1][0], 0))
            unten.extend(self.P(pts[0][0], 0))
        cv.coords(self.mat_oben, *(oben if len(oben) >= 6 else [0, 0, 0, 0, 0, 0]))
        cv.coords(self.mat_unten, *(unten if len(unten) >= 6 else [0, 0, 0, 0, 0, 0]))
        pos = sim.position
        if pos is not None:
            f, s = pos
            platte, halter = f.umriss(s)
            cv.coords(self.platte_item, *[c for p in platte for c in self.P(*p)])
            if self.zeige_halter.get():
                cv.coords(self.halter_item, *[c for p in halter for c in self.P(*p)])
            else:
                cv.coords(self.halter_item, 0, 0, 0, 0, 0, 0)
            tz, tr = s[0] + f.off[0], s[1] + f.off[1]
            x, y = self.P(tz, tr)
            cv.coords(self.tip_item, x - 2, y - 2, x + 2, y + 2)
        self._bahn()
        for n, e in enumerate(sim.ereignisse):
            if n in self._markiert or e.art == "hinweis":
                continue
            if (e.schritt >= 0 and e.schritt < sim.i) or (e.schritt < 0 and sim.fertig):
                self._marker(e, tag="marker")
                self._markiert.add(n)
        cv.tag_raise(self.halter_item)
        cv.tag_raise(self.platte_item)
        cv.tag_raise(self.tip_item)

    def _neu_zeichnen_lupe(self) -> None:
        pos = self.sim.position
        if pos is None or self._view is None:
            return
        _f, sp = pos
        s, cx, cy = self._view
        W, H = self.cv.winfo_width(), self.cv.winfo_height()
        x, y = cx + sp[0] * s, cy - sp[1] * s
        if abs(x - W / 2) > W * 0.3 or abs(y - H / 2) > H * 0.3:
            self.after_idle(self._neu_zeichnen)

    def _bahn(self) -> None:
        """Bahn der theoretischen Spitze bis zum aktuellen Schritt (nur neue Stücke zeichnen)."""
        if not self.zeige_bahn.get():
            self.cv.delete("bahn")
            self._trace_bis = 0
            return
        sim = self.sim
        for k in range(self._trace_bis, min(sim.i, len(sim.schritte))):
            st = sim.schritte[k]
            f = sim.formen[st.t]
            a = self.P(st.a[0] + f.off[0], st.a[1] + f.off[1])
            b = self.P(st.b[0] + f.off[0], st.b[1] + f.off[1])
            self.cv.create_line(*a, *b, fill=BAHN_G0 if st.eil else BAHN_G1, width=1,
                                dash=(3, 3) if st.eil else (), tags=("bahn",))
        self._trace_bis = sim.i
        self.cv.tag_lower("bahn", self.halter_item)

    def _marker(self, e: Ereignis, tag: str = "auswahl") -> None:
        cv = self.cv
        x, y = self.P(e.z, e.r)
        r = px(self, 7)
        if e.schwer:
            cv.create_line(x - r, y - r, x + r, y + r, fill=C["error"], width=3, tags=(tag,))
            cv.create_line(x - r, y + r, x + r, y - r, fill=C["error"], width=3, tags=(tag,))
        else:
            cv.create_oval(x - r, y - r, x + r, y + r, outline=C["warn"], width=2, tags=(tag,))
