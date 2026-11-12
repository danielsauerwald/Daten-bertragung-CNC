"""Fenster „3D-Modell“: Modell ansehen, Drehachse (X0) und Nullpunkt Z0 wählen, Teil übernehmen.

Links oben das Modell in 3D (ziehen = drehen, Rad = zoomen, rechte Maustaste = schieben), darunter die
Seitenansicht (Halbschnitt) mit der erkannten Kontur. Ein Klick in die Seitenansicht oder auf eine
Planfläche im 3D-Bild setzt den Nullpunkt Z0. X0 ist immer die Drehachse.
"""

from __future__ import annotations

import math
import tkinter as tk
from tkinter import ttk
from typing import Dict, List, Optional, Tuple

from . import cad, dialogs
from .dialogs import Dialog
from .kontur import Teil, build_geometry, build_innen, fmt, parse_num, polyline
from .theme import C, Fonts, px

Vec = Tuple[float, float, float]

MAX_DREIECKE = 7000  # in Ruhe gezeichnet
MAX_DREIECKE_ZIEHEN = 1800  # während die Maus dreht
SOFORT_BIS = 40000  # kleinere Modelle sofort auswerten, größere im Hintergrund
LICHT = (-0.35, 0.55, 0.76)
FARBE = (150, 168, 190)  # Stahl
FARBE_Z0 = (236, 150, 60)  # Planfläche am Nullpunkt
FARBE_RUND = (118, 182, 140)  # gewählte Fläche (Achse)


# --------------------------------------------------------------------------
# Netz fürs Zeichnen (ohne Tk – testbar)
# --------------------------------------------------------------------------


class Netz:
    """Dreiecke in Teilkoordinaten (z entlang der Achse, u = C0/oben, v = C90), für die Anzeige
    vereinfacht. ``quelle[i]``: Index eines Original-Dreiecks (für Flächen-Infos beim Anklicken)."""

    def __init__(self, punkte: List[Vec], dreiecke: List[Tuple[int, int, int]], quelle: List[int]) -> None:
        self.punkte = punkte
        self.dreiecke = dreiecke
        self.quelle = quelle


def netz_aus_modell(m: cad.Modell, lage: cad.Lage, max_n: int) -> Netz:
    """Modell in Teilkoordinaten; mehr als ``max_n`` Dreiecke werden durch Zusammenlegen naher Punkte
    (Raster) vereinfacht – die Form bleibt geschlossen, nur feiner Rundungen werden gröber."""
    pts: List[Vec] = []
    idx: Dict[Tuple[float, float, float], int] = {}
    tris: List[Tuple[int, int, int]] = []
    for t in m.dreiecke:
        tri = []
        for p in t:
            q = lage.koord(p)
            key = (round(q[0], 4), round(q[1], 4), round(q[2], 4))
            i = idx.get(key)
            if i is None:
                i = idx[key] = len(pts)
                pts.append(q)
            tri.append(i)
        tris.append((tri[0], tri[1], tri[2]))
    return netz_vereinfachen(Netz(pts, tris, list(range(len(tris)))), max_n)


def netz_vereinfachen(netz: Netz, max_n: int) -> Netz:
    """Nahe Punkte auf einem Raster zusammenlegen, bis höchstens ``max_n`` Dreiecke übrig sind."""
    pts, tris, quelle = netz.punkte, netz.dreiecke, netz.quelle
    if len(tris) <= max_n:
        return netz
    lo = [min(p[k] for p in pts) for k in range(3)]
    hi = [max(p[k] for p in pts) for k in range(3)]
    groesse = max(hi[k] - lo[k] for k in range(3)) or 1.0
    zelle = groesse / 400.0
    neu_pts, neu_tris, neu_q = pts, tris, quelle
    for _ in range(12):
        neu_pts: List[Vec] = []
        summe: List[List[float]] = []
        zellen: Dict[Tuple[int, int, int], int] = {}
        abb = []
        for p in pts:
            key = (int(math.floor(p[0] / zelle)), int(math.floor(p[1] / zelle)), int(math.floor(p[2] / zelle)))
            j = zellen.get(key)
            if j is None:
                j = zellen[key] = len(summe)
                summe.append([0.0, 0.0, 0.0, 0.0])
            s = summe[j]
            s[0] += p[0]
            s[1] += p[1]
            s[2] += p[2]
            s[3] += 1
            abb.append(j)
        neu_pts = [(s[0] / s[3], s[1] / s[3], s[2] / s[3]) for s in summe]
        gesehen = set()
        neu_tris, neu_q = [], []
        for k, (a, b, c) in enumerate(tris):
            a, b, c = abb[a], abb[b], abb[c]
            if a == b or b == c or a == c:
                continue
            key2 = tuple(sorted((a, b, c)))
            if key2 in gesehen:
                continue
            gesehen.add(key2)
            neu_tris.append((a, b, c))
            neu_q.append(quelle[k])
        if len(neu_tris) <= max_n:
            return Netz(neu_pts, neu_tris, neu_q)
        zelle *= 1.5
    return Netz(neu_pts, neu_tris, neu_q)


def volumen(netz: Netz) -> float:
    """Vorzeichenbehaftetes Volumen: > 0 heißt, die Dreiecke zeigen nach außen."""
    p = netz.punkte
    v = 0.0
    for a, b, c in netz.dreiecke:
        (ax, ay, az), (bx, by, bz), (cx, cy, cz) = p[a], p[b], p[c]
        v += ax * (by * cz - bz * cy) - ay * (bx * cz - bz * cx) + az * (bx * cy - by * cx)
    return v / 6.0


class Kamera:
    """Drehung (gieren um die Hochachse, nicken um die Querachse), Zoom und Verschiebung.
    Teil liegt wie auf der Drehmaschine: Z nach rechts (Stirnseite rechts), X (u) nach oben."""

    def __init__(self) -> None:
        self.standard()

    def standard(self) -> None:
        self.gier, self.nick, self.zoom, self.dx, self.dy = math.radians(-28), math.radians(22), 1.0, 0.0, 0.0

    def drehen(self, p: Vec) -> Vec:
        z, u, v = p
        cg, sg = math.cos(self.gier), math.sin(self.gier)
        x1, t1 = cg * z + sg * v, -sg * z + cg * v
        cn, sn = math.cos(self.nick), math.sin(self.nick)
        return x1, cn * u - sn * t1, sn * u + cn * t1  # (rechts, oben, zum Betrachter)


# --------------------------------------------------------------------------
# Fenster
# --------------------------------------------------------------------------


class CadDialog(Dialog):
    """Ergebnis: ``Teil`` (mit z_null) oder None."""

    def __init__(self, parent, fonts: Fonts, modell: cad.Modell, basis: Teil, dateiname: str = "") -> None:
        super().__init__(parent, fonts, "3D-Modell übernehmen")
        self.m = modell
        self.basis = basis
        self.dateiname = dateiname
        self.kandidaten = cad.achsen_kandidaten(modell)
        n, p, d = self.kandidaten[0]
        self.lage = cad.lage_standard(modell, p, d, n)
        self.erk: Optional[cad.Erkennung] = None
        self.z_null = 0.0
        self.kamera = Kamera()
        self.netz: Optional[Netz] = None
        self.netz_grob: Optional[Netz] = None
        self.aussen_zeigt = True
        self._items: Dict[int, int] = {}  # Canvas-Item -> Dreieck im gezeichneten Netz
        self._gezeichnet: Optional[Netz] = None
        self._drag: Optional[Tuple[int, int, int]] = None  # (x, y, Taste)
        self._bewegt = False
        self._job: Optional[str] = None
        self._waehle_achse = False
        self._rund: int = -1  # angeklickte runde Fläche (Index in m.flaechen)
        self._kugel = None  # (Netz, Mitte z, Radius) zum Einpassen
        self.prof: Optional[cad.Profil] = None
        self._rechnet = False
        self._nach_rechnen = ""  # Meldung, sobald die Auswertung fertig ist
        self._size = (px(self, 1500), px(self, 960))
        self.minsize(px(self, 820), px(self, 560))
        self._aufbau()
        self._neu_rechnen()

    # ------------------------------------------------------------------ Aufbau

    def _aufbau(self) -> None:
        bg = C["bg"]
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)
        head = tk.Frame(self, bg=bg)
        head.grid(row=0, column=0, sticky="ew", padx=px(self, 16), pady=(px(self, 10), px(self, 6)))
        tk.Label(head, text="3D-Modell → Drehteil", bg=bg, fg=C["text"], font=self.fonts.title).pack(side="left")
        art = "STEP – Flächen genau" if self.m.flaechen else "STL – Dreiecke"
        tk.Label(head, text="{}   ·   {}   ·   {} Dreiecke".format(
            self.dateiname or self.m.name, art, len(self.m.dreiecke)), bg=bg, fg=C["muted"],
            font=self.fonts.small).pack(side="left", padx=(px(self, 14), 0))

        main = tk.Frame(self, bg=bg)
        main.grid(row=1, column=0, sticky="nsew", padx=px(self, 12))
        main.grid_rowconfigure(0, weight=3)
        main.grid_rowconfigure(1, weight=2)
        main.grid_columnconfigure(0, weight=1)
        self.cv3 = tk.Canvas(main, bg=C["surface"], highlightthickness=1, highlightbackground=C["border"],
                             cursor="fleur")
        self.cv3.grid(row=0, column=0, sticky="nsew")
        self.cvs = tk.Canvas(main, bg=C["surface"], highlightthickness=1, highlightbackground=C["border"],
                             cursor="crosshair")
        self.cvs.grid(row=1, column=0, sticky="nsew", pady=(px(self, 8), 0))
        self.cv3.bind("<Configure>", lambda e: self._zeichne_3d())
        self.cvs.bind("<Configure>", lambda e: self._zeichne_seite())
        self.cv3.bind("<ButtonPress-1>", lambda e: self._druck(e, 1))
        self.cv3.bind("<ButtonPress-3>", lambda e: self._druck(e, 3))
        self.cv3.bind("<B1-Motion>", self._ziehen)
        self.cv3.bind("<B3-Motion>", self._ziehen)
        self.cv3.bind("<ButtonRelease-1>", self._loslassen)
        self.cv3.bind("<ButtonRelease-3>", self._loslassen)
        self.cv3.bind("<MouseWheel>", lambda e: self._zoomen(1 if e.delta > 0 else -1))
        self.cv3.bind("<Button-4>", lambda e: self._zoomen(1))
        self.cv3.bind("<Button-5>", lambda e: self._zoomen(-1))
        self.cvs.bind("<ButtonRelease-1>", self._klick_seite)
        self.cvs.bind("<Motion>", self._zeige_seite)
        self.cvs.bind("<Leave>", lambda e: self.cvs.delete("hover"))

        right = tk.Frame(main, bg=C["surface"], highlightthickness=1, highlightbackground=C["border"],
                         width=px(self, 430), padx=px(self, 12), pady=px(self, 10))
        right.grid(row=0, column=1, rowspan=2, sticky="nsew", padx=(px(self, 12), 0))
        right.grid_propagate(False)
        right.pack_propagate(False)
        sf = C["surface"]

        def titel(text: str, top: int = 0) -> None:
            tk.Label(right, text=text, bg=sf, fg=C["text"], font=self.fonts.ui_bold, anchor="w").pack(
                fill="x", pady=(px(self, top), px(self, 2)))

        def notiz(text: str) -> tk.Label:
            lb = tk.Label(right, text=text, bg=sf, fg=C["muted"], font=self.fonts.small, anchor="w",
                          justify="left", wraplength=px(self, 400))
            lb.pack(fill="x")
            return lb

        titel("Drehachse = X0")
        self.cb_achse = ttk.Combobox(right, state="readonly", font=self.fonts.ui,
                                     values=[k[0] for k in self.kandidaten])
        self.cb_achse.current(0)
        self.cb_achse.pack(fill="x", pady=(0, px(self, 4)))
        self.cb_achse.bind("<<ComboboxSelected>>", lambda e: self._achse_gewaehlt())
        row = tk.Frame(right, bg=sf)
        row.pack(fill="x", pady=(0, px(self, 2)))
        ttk.Button(row, text="Teil wenden", style="Small.TButton", command=self._wenden).pack(side="left")
        if self.m.flaechen:
            self.btn_rund = ttk.Button(row, text="Achse: runde Fläche anklicken", style="Small.TButton",
                                       command=self._achse_klicken)
            self.btn_rund.pack(side="left", padx=(px(self, 6), 0))
        notiz("„Teil wenden“ legt die andere Stirnseite nach vorn (rechts).")

        titel("Nullpunkt Z0", 12)
        notiz("Klick in die Seitenansicht oder auf eine Planfläche im 3D-Bild. Rastet auf Planflächen ein.")
        row = tk.Frame(right, bg=sf)
        row.pack(fill="x", pady=(px(self, 4), 0))
        tk.Label(row, text="Z0 hinter der Stirnseite", bg=sf, fg=C["text"], font=self.fonts.ui).pack(side="left")
        self.v_null = tk.StringVar(master=self, value="0")
        e = ttk.Entry(row, textvariable=self.v_null, width=8, font=self.fonts.ui)
        e.pack(side="left", padx=(px(self, 6), px(self, 4)))
        e.bind("<Return>", lambda ev: self._null_eingabe())
        e.bind("<FocusOut>", lambda ev: self._null_eingabe())
        tk.Label(row, text="mm", bg=sf, fg=C["text"], font=self.fonts.ui).pack(side="left")
        ttk.Button(row, text="Stirnseite", style="Small.TButton",
                   command=lambda: self._setze_null(0.0)).pack(side="right")
        self.lb_null = notiz("")

        titel("Erkannt", 12)
        box = tk.Frame(right, bg=sf)
        box.pack(fill="both", expand=True)
        self.txt = tk.Text(box, bg=sf, fg=C["text"], font=self.fonts.small, wrap="word", relief="flat",
                           highlightthickness=0, padx=0, pady=px(self, 2))
        sb = ttk.Scrollbar(box, orient="vertical", command=self.txt.yview)
        self.txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.txt.pack(side="left", fill="both", expand=True)
        self.txt.tag_configure("warn", foreground=C["warn"])
        self.txt.tag_configure("muted", foreground=C["muted"])

        row = tk.Frame(right, bg=sf)
        row.pack(fill="x", pady=(px(self, 8), 0))
        for text, cmd in (("Ansicht 3D", self._ansicht_iso), ("Seite", self._ansicht_seite),
                          ("Stirn", self._ansicht_stirn)):
            ttk.Button(row, text=text, style="Small.TButton", command=cmd).pack(
                side="left", padx=(0, px(self, 4)))

        foot = tk.Frame(self, bg=bg)
        foot.grid(row=2, column=0, sticky="ew", padx=px(self, 12), pady=px(self, 10))
        self.status = tk.Label(foot, text="", bg=bg, fg=C["muted"], font=self.fonts.small, anchor="w")
        self.status.pack(side="left", fill="x", expand=True)
        self.btn_ok = ttk.Button(foot, text="Übernehmen", style="Primary.TButton", command=self._uebernehmen)
        self.btn_ok.pack(side="right")
        ttk.Button(foot, text="Abbrechen", command=self.cancel).pack(side="right", padx=(0, px(self, 8)))

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

    # ------------------------------------------------------------- Rechnen

    def _neu_rechnen(self) -> None:
        """Nach Achse/Wenden: Profil, Erkennung und Netz neu – große Modelle im Hintergrund."""
        lage, m, basis = self.lage, self.m, self.basis

        def arbeit():
            prof = cad.profil(m, lage)
            erk = cad.teil_aus_modell(m, lage, basis, prof)
            netz = netz_aus_modell(m, lage, MAX_DREIECKE)
            return lage, prof, erk, netz, netz_vereinfachen(netz, MAX_DREIECKE_ZIEHEN)

        if len(m.dreiecke) <= SOFORT_BIS:
            try:
                res = arbeit()
            except Exception as exc:  # als Meldung im Fenster
                self._gerechnet(None, exc)
                return
            self._gerechnet(res, None)
            return
        from .ki import im_hintergrund

        self._rechnet = True
        self.btn_ok.state(["disabled"])
        self._meldung("Das Modell wird ausgewertet ({} Dreiecke) …".format(len(m.dreiecke)))
        for cv in (self.cv3, self.cvs):
            cv.delete("all")
            cv.create_text(px(self, 16), px(self, 16), anchor="nw", fill=C["muted"], font=self.fonts.ui,
                           text="Das Modell wird ausgewertet …")
        im_hintergrund(self, arbeit, self._gerechnet)

    def _gerechnet(self, res, err: Optional[Exception]) -> None:
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        self._rechnet = False
        self.btn_ok.state(["!disabled"])
        if err is not None:
            self._meldung("Auswerten ging nicht: {}".format(err), warn=True)
            return
        lage, prof, erk, netz, grob = res
        if lage is not self.lage:  # inzwischen andere Achse gewählt
            self._neu_rechnen()
            return
        self.prof, self.erk, self.netz, self.netz_grob = prof, erk, netz, grob
        self.aussen_zeigt = volumen(netz) >= 0
        self.z_null = 0.0
        self._liste()
        self._null_anzeigen()
        self._zeichne_3d()
        self._zeichne_seite()
        if self._nach_rechnen:
            self._meldung(self._nach_rechnen)
            self._nach_rechnen = ""
        else:
            self._meldung("")

    def _liste(self) -> None:
        t = self.txt
        t.configure(state="normal")
        t.delete("1.0", "end")
        erk = self.erk
        if erk is None:
            return
        for s in erk.merkmale:
            t.insert("end", "• " + s + "\n")
        if erk.hinweise:
            t.insert("end", "\n")
            for s in erk.hinweise:
                t.insert("end", "! " + s + "\n", "warn")
        t.insert("end", "\nNach dem Übernehmen alles im Assistenten prüfen und anpassen (Werkzeuge, "
                        "Schnittwerte, Rohteil) – dann den Code erzeugen.\n", "muted")
        t.configure(state="disabled")

    def _achse_gewaehlt(self) -> None:
        i = self.cb_achse.current()
        if i < 0:
            return
        n, p, d = self.kandidaten[i]
        self.lage = cad.lage_standard(self.m, p, d, n)
        self._rund = -1
        self._nach_rechnen = "Drehachse: {}".format(n)
        self._neu_rechnen()

    def _wenden(self) -> None:
        self.lage = cad.wenden(self.m, self.lage)
        self._nach_rechnen = "Teil gewendet – die andere Stirnseite ist jetzt vorn."
        self._neu_rechnen()

    def _achse_klicken(self) -> None:
        self._waehle_achse = not self._waehle_achse
        self.btn_rund.configure(text="Abbrechen: Fläche wählen" if self._waehle_achse else
                                "Achse: runde Fläche anklicken")
        self.cv3.configure(cursor="hand2" if self._waehle_achse else "fleur")
        if self._waehle_achse:
            self._meldung("Jetzt im 3D-Bild auf eine runde Fläche (Zylinder, Kegel) klicken.")

    # ------------------------------------------------------------ Nullpunkt

    def _setze_null(self, z_null: float, info: str = "") -> None:
        if self.prof is None:
            return
        laenge = self.prof.z_vorn - self.prof.z_hinten
        z_null = max(-50.0, min(laenge + 50.0, z_null))
        self.z_null = round(z_null + 0.0, 3) + 0.0
        self._null_anzeigen()
        self._zeichne_3d()
        self._zeichne_seite()
        if info:
            self._meldung(info)

    def _null_anzeigen(self) -> None:
        self.v_null.set(fmt(self.z_null))
        if abs(self.z_null) < 1e-6:
            text = "Z0 liegt an der Stirnseite (rechts). X0 = Drehachse."
        elif self.z_null > 0:
            text = "Z0 liegt {} mm hinter der Stirnseite – die Stirnseite ist im Programm Z{}.".format(
                fmt(self.z_null), fmt(self.z_null))
        else:
            text = "Z0 liegt {} mm vor der Stirnseite – die Stirnseite ist im Programm Z{}.".format(
                fmt(-self.z_null), fmt(self.z_null))
        self.lb_null.configure(text=text)

    def _null_eingabe(self) -> None:
        try:
            v = parse_num(self.v_null.get())
        except ValueError:
            self._meldung("„{}“ ist keine Zahl.".format(self.v_null.get()), warn=True)
            self.v_null.set(fmt(self.z_null))
            return
        if abs(v - self.z_null) > 1e-9:
            self._setze_null(v)

    def _einrasten(self, z_rel: float, tol: float) -> Tuple[float, bool]:
        """z (ab Stirnseite, negativ nach hinten) auf die nächste Planfläche ziehen, wenn nah genug."""
        kand = [p - self.prof.z_vorn for p in self.prof.plan] + [0.0, self.prof.z_hinten - self.prof.z_vorn]
        best = min(kand, key=lambda p: abs(p - z_rel))
        if abs(best - z_rel) <= tol:
            return best, True
        return round(z_rel, 1), False

    def _meldung(self, text: str, warn: bool = False) -> None:
        self.status.configure(text=text, fg=C["warn"] if warn else C["muted"])

    # ------------------------------------------------------------- 3D-Ansicht

    def _ansicht_iso(self) -> None:
        self.kamera.standard()
        self._zeichne_3d()

    def _ansicht_seite(self) -> None:
        self.kamera.standard()
        self.kamera.gier, self.kamera.nick = 0.0, 0.0
        self._zeichne_3d()

    def _ansicht_stirn(self) -> None:
        self.kamera.standard()
        self.kamera.gier, self.kamera.nick = math.radians(-90), 0.0
        self._zeichne_3d()

    def _druck(self, e, taste: int) -> None:
        self._drag = (e.x, e.y, taste)
        self._bewegt = False

    def _ziehen(self, e) -> None:
        if self._drag is None:
            return
        x0, y0, taste = self._drag
        dx, dy = e.x - x0, e.y - y0
        if not self._bewegt and abs(dx) + abs(dy) < 4:
            return
        self._bewegt = True
        self._drag = (e.x, e.y, taste)
        k = self.kamera
        if taste == 1:
            k.gier += dx * 0.01
            k.nick = max(-math.pi / 2, min(math.pi / 2, k.nick + dy * 0.01))
        else:
            k.dx += dx
            k.dy += dy
        self._zeichne_3d(grob=True)
        if self._job:
            self.after_cancel(self._job)
        self._job = self.after(220, self._zeichne_3d)

    def _loslassen(self, e) -> None:
        taste = self._drag[2] if self._drag else 1
        self._drag = None
        if not self._bewegt and taste == 1:
            self._klick_3d(e.x, e.y)

    def _zoomen(self, richtung: int) -> None:
        self.kamera.zoom = max(0.2, min(25.0, self.kamera.zoom * (1.15 if richtung > 0 else 1 / 1.15)))
        self._zeichne_3d(grob=True)
        if self._job:
            self.after_cancel(self._job)
        self._job = self.after(220, self._zeichne_3d)

    def _abbildung(self, w: int, h: int):
        """Funktion Teilpunkt -> (x, y, Tiefe) auf dem Canvas."""
        netz = self.netz
        if netz is None or not netz.punkte:
            return None
        k = self.kamera
        if self._kugel is None or self._kugel[0] is not netz:
            zs = [p[0] for p in netz.punkte]
            mz = (min(zs) + max(zs)) / 2
            r = max(math.sqrt((p[0] - mz) ** 2 + p[1] ** 2 + p[2] ** 2) for p in netz.punkte) or 1.0
            self._kugel = (netz, mz, r)
        _n, mz, r = self._kugel
        mitte = (mz, 0.0, 0.0)
        s = 0.94 * min(w, h) / (2 * r) * k.zoom
        cx, cy = w / 2 + k.dx, h / 2 + k.dy

        def ab(p: Vec) -> Tuple[float, float, float]:
            x, y, t = k.drehen((p[0] - mitte[0], p[1] - mitte[1], p[2] - mitte[2]))
            return cx + s * x, cy - s * y, t

        return ab

    def _zeichne_3d(self, grob: bool = False) -> None:
        cv = self.cv3
        self._job = None if not grob else self._job
        try:
            w, h = cv.winfo_width(), cv.winfo_height()
        except tk.TclError:
            return
        if self._rechnet:
            return
        cv.delete("all")
        if w < 20 or h < 20:
            return
        netz = self.netz_grob if grob else self.netz
        ab = self._abbildung(w, h)
        if netz is None or ab is None or self.prof is None or self._rechnet:
            return
        proj = [ab(p) for p in netz.punkte]
        drei = [self.kamera.drehen(p) for p in netz.punkte]
        lx, ly, lz = LICHT
        z0 = -self.z_null
        flaechen = self.m.flaechen
        liste = []
        for i, (a, b, c) in enumerate(netz.dreiecke):
            pa, pb, pc = drei[a], drei[b], drei[c]
            ux, uy, uz = pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]
            vx, vy, vz = pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2]
            nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
            ln = math.sqrt(nx * nx + ny * ny + nz * nz)
            if ln < 1e-12:
                continue
            if not self.aussen_zeigt:
                nx, ny, nz = -nx, -ny, -nz
            if nz <= 0:
                continue  # Rückseite
            hell = 0.30 + 0.70 * max(0.0, (nx * lx + ny * ly + nz * lz) / ln)
            q = netz.punkte
            farbe = FARBE
            if abs(q[a][0] - z0) < 0.02 and abs(q[b][0] - z0) < 0.02 and abs(q[c][0] - z0) < 0.02:
                farbe = FARBE_Z0
            elif self._rund >= 0 and flaechen and self.m.flaeche[netz.quelle[i]] == self._rund:
                farbe = FARBE_RUND
            stufe = int(hell * 24) / 24.0
            col = "#{:02x}{:02x}{:02x}".format(*(min(255, int(f * stufe + 18 * stufe)) for f in farbe))
            tiefe = (proj[a][2] + proj[b][2] + proj[c][2])
            liste.append((tiefe, i, col))
        liste.sort()
        items: Dict[int, int] = {}
        for _t, i, col in liste:
            a, b, c = netz.dreiecke[i]
            it = cv.create_polygon(proj[a][0], proj[a][1], proj[b][0], proj[b][1], proj[c][0], proj[c][1],
                                   fill=col, outline=col)
            items[it] = i
        self._items = items
        self._gezeichnet = netz
        # Drehachse (X0) und Nullpunkt
        zs = [p[0] for p in netz.punkte]
        a1, a2 = ab((min(zs) - 8, 0.0, 0.0)), ab((max(zs) + 12, 0.0, 0.0))
        cv.create_line(a1[0], a1[1], a2[0], a2[1], fill=C["primary"], dash=(8, 3, 2, 3), width=1)
        o = ab((z0, 0.0, 0.0))
        rmax = max(self.prof.aussen) if self.prof.aussen else 10.0
        lz_ = max(6.0, 0.6 * rmax)
        pz, px_ = ab((z0 + lz_, 0.0, 0.0)), ab((z0, lz_, 0.0))
        cv.create_line(o[0], o[1], pz[0], pz[1], fill="#2563eb", width=3, arrow="last")
        cv.create_line(o[0], o[1], px_[0], px_[1], fill="#dc2626", width=3, arrow="last")
        cv.create_text(pz[0] + 8, pz[1], text="Z", fill="#2563eb", font=self.fonts.ui_bold, anchor="w")
        cv.create_text(px_[0], px_[1] - 6, text="X", fill="#dc2626", font=self.fonts.ui_bold, anchor="s")
        r = 7
        cv.create_oval(o[0] - r, o[1] - r, o[0] + r, o[1] + r, outline=C["text"], width=2, fill=C["surface"])
        cv.create_arc(o[0] - r, o[1] - r, o[0] + r, o[1] + r, start=0, extent=90, fill=C["text"], outline="")
        cv.create_arc(o[0] - r, o[1] - r, o[0] + r, o[1] + r, start=180, extent=90, fill=C["text"], outline="")
        cv.create_text(px(self, 10), px(self, 8), anchor="nw", fill=C["muted"], font=self.fonts.small,
                       text="Ziehen = drehen · Rad = zoomen · rechte Taste = schieben · Klick auf Planfläche = Z0")

    def _klick_3d(self, x: int, y: int) -> None:
        netz = self._gezeichnet
        if netz is None or self._rechnet:
            return
        treffer = [it for it in self.cv3.find_overlapping(x - 1, y - 1, x + 1, y + 1) if it in self._items]
        if not treffer:
            return
        i = self._items[treffer[-1]]  # oberstes Dreieck
        ab = self._abbildung(self.cv3.winfo_width(), self.cv3.winfo_height())
        a, b, c = (netz.punkte[k] for k in netz.dreiecke[i])
        pa, pb, pc = ab(a), ab(b), ab(c)
        den = (pb[1] - pc[1]) * (pa[0] - pc[0]) + (pc[0] - pb[0]) * (pa[1] - pc[1])
        if abs(den) < 1e-9:
            wa, wb = 1 / 3, 1 / 3
        else:
            wa = ((pb[1] - pc[1]) * (x - pc[0]) + (pc[0] - pb[0]) * (y - pc[1])) / den
            wb = ((pc[1] - pa[1]) * (x - pc[0]) + (pa[0] - pc[0]) * (y - pc[1])) / den
        wc = 1 - wa - wb
        z = wa * a[0] + wb * b[0] + wc * c[0]
        f_idx = self.m.flaeche[netz.quelle[i]] if self.m.flaeche else -1
        f = self.m.flaechen[f_idx] if 0 <= f_idx < len(self.m.flaechen) else None
        if self._waehle_achse:
            self._achse_aus_flaeche(f, f_idx)
            return
        plan = abs(a[0] - b[0]) < 0.01 and abs(a[0] - c[0]) < 0.01
        if f is not None and f.get("typ") == "ebene" and abs(abs(cad._dot(f["normale"], self.lage.z)) - 1) < 1e-4:
            plan = True
        if plan:
            z_rel, _ = self._einrasten(z, 0.05)
            self._setze_null(-z_rel, "Z0 auf die Planfläche gelegt ({} mm hinter der Stirnseite).".format(
                fmt(round(-z_rel, 3))))
        else:
            z_rel, gerastet = self._einrasten(z, max(0.5, 0.02 * (self.prof.z_vorn - self.prof.z_hinten)))
            self._setze_null(-z_rel, "Z0 {} – für genaue Lage auf eine Planfläche klicken.".format(
                "auf die nächste Planfläche gelegt" if gerastet else "gesetzt"))

    def _achse_aus_flaeche(self, f: Optional[dict], f_idx: int) -> None:
        if f is None or f.get("typ") not in ("zylinder", "kegel", "torus"):
            self._meldung("Das ist keine runde Fläche – bitte einen Zylinder oder Kegel anklicken.", warn=True)
            return
        self._achse_klicken()  # Modus aus
        d = f["achse"]
        if cad._dot(d, self.lage.z) < 0:
            d = cad._scale(d, -1.0)
        name = "Fläche {} ({} Ø{})".format(f_idx + 1, f["typ"], fmt(round(2 * f["radius"], 3))) \
            if f.get("radius") else "Fläche {} ({})".format(f_idx + 1, f["typ"])
        self.kandidaten.append((name, f["punkt"], d))
        self.cb_achse.configure(values=[k[0] for k in self.kandidaten])
        self.cb_achse.current(len(self.kandidaten) - 1)
        self.lage = cad.lage_standard(self.m, f["punkt"], d, name)
        self._rund = f_idx
        self._nach_rechnen = "Drehachse aus der angeklickten Fläche übernommen."
        self._neu_rechnen()

    # ---------------------------------------------------------- Seitenansicht

    def _seite_massstab(self):
        w, h = self.cvs.winfo_width(), self.cvs.winfo_height()
        pr = self.prof
        laenge = max(1e-6, pr.z_vorn - pr.z_hinten)
        rmax = max(pr.aussen) if pr.aussen else 1.0
        rand = px(self, 34)
        s = min((w - 2 * rand) / (laenge + 16), (h - 2 * rand) / (2 * rmax + 8))
        x0 = w / 2 + s * (laenge / 2)  # Stirnseite (z_rel = 0), Teil mittig
        y0 = h / 2

        def ab(z_rel: float, r: float) -> Tuple[float, float]:
            return x0 + s * z_rel, y0 - s * r

        return ab, s, x0

    def _zeichne_seite(self) -> None:
        cv = self.cvs
        try:
            w, h = cv.winfo_width(), cv.winfo_height()
        except tk.TclError:
            return
        if self._rechnet:
            return
        cv.delete("all")
        if w < 40 or h < 40 or self.erk is None or self.prof is None:
            return
        pr = self.prof
        ab, s, x0 = self._seite_massstab()
        zr = [z - pr.z_vorn for z in pr.z]
        grau, hell = "#c9d2de", "#e4e9f0"
        for sign, col in ((1, grau), (-1, hell)):
            oben = [(zr[0] + pr.dz / 2, 0.0)] + list(zip(zr, [sign * r for r in pr.aussen])) + \
                   [(zr[-1] - pr.dz / 2, 0.0)]
            cv.create_polygon(*[c for z, r in oben for c in ab(z, r)], fill=col, outline="")
            # Bohrung ausschneiden
            k = 0
            while k < len(zr):
                if pr.innen[k] <= 0:
                    k += 1
                    continue
                j = k
                while j + 1 < len(zr) and pr.innen[j + 1] > 0:
                    j += 1
                poly = [(zr[k] + pr.dz / 2, 0.0)] + [(zr[q], sign * pr.innen[q]) for q in range(k, j + 1)] + \
                       [(zr[j] - pr.dz / 2, 0.0)]
                cv.create_polygon(*[c for z, r in poly for c in ab(z, r)], fill=C["surface"], outline="")
                k = j + 1
        # Planflächen (Rastpunkte)
        rmax = max(pr.aussen) if pr.aussen else 1.0
        for p in pr.plan:
            x, y1 = ab(p - pr.z_vorn, rmax + 3)
            _x, y2 = ab(p - pr.z_vorn, -rmax - 3)
            cv.create_line(x, y1, x, y2, fill="#d5dce5")
        # erkannte Kontur (blau) und Innenkontur
        t = self.erk.teil
        g = build_geometry(t)
        pts = polyline(g.elems)
        if len(pts) > 1:
            cv.create_line(*[c for z, r in pts for c in ab(z, r)], fill=C["primary"], width=2)
        if t.innen:
            gi = build_innen(t)
            pts = polyline(gi.elems)
            if len(pts) > 1:
                cv.create_line(*[c for z, r in pts for c in ab(z, r)], fill=C["primary"], width=2, dash=(6, 3))
        elif t.bohr_d > 0:
            tiefe = min(t.bohr_tiefe, pr.z_vorn - pr.z_hinten + 2)
            r = t.bohr_d / 2
            cv.create_line(*ab(0, r), *ab(-tiefe, r), fill=C["primary"], width=2, dash=(6, 3))
        # Drehachse X0
        xa, ya = ab(pr.z_hinten - pr.z_vorn - 6, 0)
        xb, _ = ab(6, 0)
        cv.create_line(xa, ya, xb, ya, fill=C["text"], dash=(12, 3, 2, 3))
        cv.create_text(xb + 4, ya, text="X0", anchor="w", fill=C["text"], font=self.fonts.small)
        # Nullpunkt Z0
        xz, _ = ab(-self.z_null, 0)
        cv.create_line(xz, px(self, 18), xz, h - px(self, 6), fill="#ea7a1e", width=2, dash=(10, 3, 2, 3))
        cv.create_text(xz, px(self, 16), text="Z0", anchor="s", fill="#ea7a1e", font=self.fonts.ui_bold)
        r = 6
        cv.create_oval(xz - r, ya - r, xz + r, ya + r, outline=C["text"], width=2, fill=C["surface"])
        cv.create_arc(xz - r, ya - r, xz + r, ya + r, start=0, extent=90, fill=C["text"], outline="")
        cv.create_arc(xz - r, ya - r, xz + r, ya + r, start=180, extent=90, fill=C["text"], outline="")
        cv.create_text(px(self, 10), px(self, 8), anchor="nw", fill=C["muted"], font=self.fonts.small,
                       text="Seitenansicht: grau = Modell, blau = erkannte Kontur · Klick setzt Z0")

    def _seite_z(self, x: int) -> Tuple[float, bool]:
        ab, s, x0 = self._seite_massstab()
        z_rel = (x - x0) / s
        return self._einrasten(z_rel, px(self, 8) / s)

    def _zeige_seite(self, e) -> None:
        cv = self.cvs
        cv.delete("hover")
        if self.erk is None or self._rechnet:
            return
        z_rel, gerastet = self._seite_z(e.x)
        ab, s, x0 = self._seite_massstab()
        x, _ = ab(z_rel, 0)
        cv.create_line(x, px(self, 24), x, cv.winfo_height() - px(self, 8), fill="#ea7a1e", dash=(2, 3),
                       tags="hover")
        cv.create_text(x + 4, cv.winfo_height() - px(self, 10), anchor="sw", tags="hover", fill="#ea7a1e",
                       font=self.fonts.small, text="Z0 hier: {} hinter Stirn{}".format(
                           fmt(round(-z_rel, 3)), " (Planfläche)" if gerastet else ""))

    def _klick_seite(self, e) -> None:
        if self.erk is None or self._rechnet:
            return
        z_rel, gerastet = self._seite_z(e.x)
        self._setze_null(-z_rel, "Z0 {} ({} mm hinter der Stirnseite).".format(
            "auf die Planfläche gelegt" if gerastet else "gesetzt", fmt(round(-z_rel, 3))))

    # ------------------------------------------------------------- Übernehmen

    def ergebnis(self) -> Optional[Teil]:
        if self.erk is None or self._rechnet:
            return None
        t = self.erk.teil
        t.z_null = self.z_null
        return t

    def _uebernehmen(self) -> None:
        t = self.ergebnis()
        if t is None or len(t.punkte) < 2:
            dialogs.error(self, self.fonts, "3D-Modell", "Aus dem Modell konnte keine Drehkontur gelesen werden – "
                          "stimmt die Drehachse?")
            return
        self.result = t
        self.destroy()


# --------------------------------------------------------------------------
# Laden mit Wartefenster
# --------------------------------------------------------------------------


DATEITYPEN_STEP = ("STEP", "*.step *.stp *.STEP *.STP")
DATEITYPEN_STL = ("STL", "*.stl *.STL")


def dateitypen() -> List[Tuple[str, str]]:
    if cad.step_verfuegbar():
        return [("3D-Modell", "*.step *.stp *.stl *.STEP *.STP *.STL"), DATEITYPEN_STEP, DATEITYPEN_STL,
                ("Alle Dateien", "*.*")]
    return [DATEITYPEN_STL, ("Alle Dateien", "*.*")]


def modell_laden(parent, fonts: Fonts, pfad: str) -> Optional[cad.Modell]:
    """Datei im Hintergrund lesen (STEP kann bei großen Modellen dauern). Fehler als Meldung."""
    import os

    from .ki_dialog import WartenDialog

    ext = os.path.splitext(pfad)[1].lower()
    if ext in (".step", ".stp") and not cad.step_verfuegbar():
        dialogs.error(parent, fonts, "3D-Modell", "STEP-Dateien liest nur die PC-Version (mit Open CASCADE).",
                      detail="Im CAD-Programm als STL speichern (fein, in mm) – STL geht überall.")
        return None
    dlg = WartenDialog(parent, fonts, "3D-Modell laden", "{} wird gelesen …".format(os.path.basename(pfad)),
                       lambda: cad.lade(pfad))
    res = dlg.show()
    if dlg.fehler is not None:
        exc = dlg.fehler
        msg = str(exc) if isinstance(exc, cad.CADFehler) else "Die Datei konnte nicht gelesen werden: {}".format(exc)
        dialogs.error(parent, fonts, "3D-Modell", msg)
        return None
    return res
