"""Postprozessor: maschinenabhängige G- und M-Befehle für den Kontur-Assistenten.

Jede Maschine in der Maschinenliste hat ihren eigenen Postprozessor (gespeichert in
``MachineProfile.post``). Die Felder sind kleine Vorlagen mit Platzhaltern:

==========  ==============================================  =====================
Platzhalter Bedeutung                                       Beispiel
==========  ==============================================  =====================
{T} {T2}    Werkzeugnummer, {T2} zweistellig                T5 / T05
{D} {D2}    Korrekturnummer, {D2} zweistellig               D1 / 01
{S}         Drehzahl                                        S3=3000
{W}         Winkel der Spindel/C-Achse                      SPOS=DC(90), C90.
{TEXT}      Kommentartext                                   ;{TEXT}  oder ({TEXT})
{NAME}      Programmname                                    %_N_{NAME}_MPF
{NR}        Programmnummer (Ziffern aus dem Namen)          O{NR}
==========  ==============================================  =====================

Ein ``|`` in einer Vorlage beginnt eine neue Zeile (z. B. ``%|O{NR} ({NAME})``).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, fields
from typing import Any, Dict, List, Optional, Tuple

ARTEN = {"sinumerik": "Siemens Sinumerik", "fanuc": "Fanuc / ISO"}


# --------------------------------------------------------------------------
# Zahlen und Texte für das NC-Programm
# --------------------------------------------------------------------------


def fmt(v: float) -> str:
    """Zahl für das NC-Programm: max. 3 Nachkommastellen, ohne überflüssige Nullen."""
    v = round(float(v), 3)
    if abs(v) < 0.0005:
        return "0"
    return "{:.3f}".format(v).rstrip("0").rstrip(".")


_TRANS = str.maketrans({"Ä": "AE", "Ö": "OE", "Ü": "UE", "ß": "SS", "Ø": "D", "ø": "D", "°": " GRAD", "×": "X"})


def nc_text(text: str) -> str:
    """Kommentartext nur aus Zeichen, die jede Steuerung verträgt (A-Z, 0-9, wenige Zeichen)."""
    s = str(text).upper().translate(_TRANS)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^A-Z0-9 .,:+\-_=/*#]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def nc_name(text: str) -> str:
    s = re.sub(r"[^A-Z0-9_]", "_", nc_text(text).replace(" ", "_"))
    s = re.sub(r"_+", "_", s).strip("_")
    if not s:
        s = "TEIL"
    if s[0].isdigit():
        s = "T" + s
    return s[:24]


def program_number(name: str) -> str:
    """Programmnummer für Fanuc (O1234) aus den Ziffern des Namens, sonst 1000."""
    digits = re.findall(r"\d+", str(name))
    if digits and int(digits[0][:4]) > 0:
        return digits[0][:4].zfill(4)
    return "1000"


# --------------------------------------------------------------------------
# Postprozessor
# --------------------------------------------------------------------------


@dataclass
class Postprozessor:
    art: str = "sinumerik"
    # Programmformat
    kommentar: str = ";{TEXT}"
    kopf: str = "%_N_{NAME}_MPF|;$PATH=/_N_MPF_DIR"
    start: str = "G54 G90"
    ende: str = "M30"
    kreis: str = "CR="
    dezimalpunkt: bool = False
    eine_zeile: bool = True
    # Drehen
    wz: str = "T{T2}D{D}"
    vorschub_u: str = "G95"
    ebene_drehen: str = "G18"
    spindel_ein: str = "S{S} M3"
    spindel_aus: str = "M5"
    kuehl_ein: str = "M8"
    kuehl_aus: str = ""
    wechsel_x: float = 150.0
    wechsel_z: float = 130.0
    # Angetriebene Werkzeuge, C- und Y-Achse
    vorschub_min: str = "G94"
    ebene_radial: str = "G19"
    ebene_axial: str = "G17"
    c_ein: str = ""
    c_pos: str = "SPOS=DC({W})"
    klemmen: str = ""
    loesen: str = ""
    c_aus: str = ""
    awz_ein: str = "S3={S} M3=3"
    awz_aus: str = "M3=5"

    # -- Laden / Speichern ----------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]], control: str = "") -> "Postprozessor":
        """Gespeicherte Werte über die passende Vorlage legen (fehlende Felder = Vorlage)."""
        data = data if isinstance(data, dict) else {}
        art = data.get("art") if data.get("art") in ARTEN else ("fanuc" if control == "fanuc" else "sinumerik")
        pp = vorlage("fanuc" if art == "fanuc" else "sin_s3")
        for f in fields(cls):
            if f.name not in data:
                continue
            default = getattr(pp, f.name)
            try:
                if isinstance(default, bool):
                    value = bool(data[f.name])
                elif isinstance(default, float):
                    value = float(data[f.name])
                else:
                    value = str(data[f.name]).strip()
            except (TypeError, ValueError):
                continue
            setattr(pp, f.name, value)
        if pp.art not in ARTEN:
            pp.art = "sinumerik"
        return pp

    def copy(self) -> "Postprozessor":
        return Postprozessor(**asdict(self))

    # -- Ausgabe ----------------------------------------------------------------

    def num(self, v: float) -> str:
        """Koordinate/Vorschub; bei Fanuc mit Dezimalpunkt (X20. statt X20 = 20 µm!)."""
        s = fmt(v)
        if self.dezimalpunkt and "." not in s:
            s += "."
        return s

    def fill(self, template: str, **values: Any) -> List[str]:
        """Vorlage ausfüllen; ``|`` trennt Zeilen, leere Zeilen entfallen."""
        t = int(values.get("T", 0) or 0)
        d = int(values.get("D", 1) or 1)
        rep = {
            "{T2}": "{:02d}".format(t), "{T}": str(t),
            "{D2}": "{:02d}".format(d), "{D}": str(d),
        }
        if "S" in values:
            rep["{S}"] = str(int(round(float(values["S"]))))
        if "W" in values:
            rep["{W}"] = self.num(values["W"])
        for key in ("TEXT", "NAME", "NR"):
            if key in values:
                rep["{" + key + "}"] = str(values[key])
        out = []
        for part in str(template).split("|"):
            for k, v in rep.items():
                part = part.replace(k, v)
            part = part.strip()
            if part:
                out.append(part)
        return out

    def line(self, template: str, **values: Any) -> str:
        return " ".join(self.fill(template, **values))

    def comment(self, text: str) -> str:
        return self.line(self.kommentar or ";{TEXT}", TEXT=nc_text(text))

    def tool_lines(self, t: int, d: int, feed: str, plane: str, speed: Optional[float] = None) -> List[str]:
        """Werkzeugaufruf mit Vorschubart, Ebene, ggf. Hauptspindel und Kühlmittel."""
        wz = self.line(self.wz, T=t, D=d)
        spindle = self.line(self.spindel_ein, S=speed) if speed is not None else ""
        cool = self.line(self.kuehl_ein)
        if self.eine_zeile:
            return [" ".join(x for x in (wz, feed, plane, spindle, cool) if x)]
        return [x for x in (wz, " ".join(x for x in (plane, feed) if x), spindle, cool) if x]

    # -- Prüfen -----------------------------------------------------------------

    def pruefen(self, mit_fraesen: bool = True) -> Tuple[List[str], List[str]]:
        """(Fehler, Hinweise) zu den Vorlagen."""
        err: List[str] = []
        warn: List[str] = []
        if "{T" not in self.wz:
            err.append("Postprozessor: Im Werkzeugaufruf fehlt {T} bzw. {T2}.")
        if "{S}" not in self.spindel_ein:
            err.append("Postprozessor: Bei „Hauptspindel ein“ fehlt {S} für die Drehzahl.")
        if not self.ebene_drehen.strip():
            err.append("Postprozessor: Ebene Drehen fehlt (meist G18).")
        if self.wechsel_x <= 0 or self.wechsel_z <= 0:
            err.append("Postprozessor: Werkzeugwechselpunkt X und Z müssen größer als 0 sein.")
        if "{TEXT}" not in self.kommentar:
            err.append("Postprozessor: In der Kommentar-Vorlage fehlt {TEXT}.")
        if mit_fraesen:
            if "{W}" not in self.c_pos:
                err.append("Postprozessor: Bei „Spindel positionieren“ fehlt {W} für den Winkel.")
            if "{S}" not in self.awz_ein:
                err.append("Postprozessor: Bei „Angetriebenes Werkzeug ein“ fehlt {S} für die Drehzahl.")
            if not self.awz_aus.strip():
                err.append("Postprozessor: „Angetriebenes Werkzeug aus“ fehlt.")
            if self.art == "fanuc" and not self.c_ein.strip():
                warn.append("Postprozessor: „C-Achse ein“ ist leer – bei Fanuc meist ein M-Befehl der Maschine "
                            "(siehe Maschinenhandbuch).")
            for name, label in (("vorschub_min", "Vorschub mm/min"), ("ebene_radial", "Ebene radial"),
                                ("ebene_axial", "Ebene axial")):
                if not getattr(self, name).strip():
                    err.append("Postprozessor: „{}“ fehlt.".format(label))
        if self.art == "fanuc" and not self.dezimalpunkt:
            warn.append("Postprozessor: Ohne Dezimalpunkt liest eine Fanuc X20 meist als 0,02 mm.")
        return err, warn

    def kurz(self) -> str:
        """Einzeilige Zusammenfassung für Listen."""
        return "{} · {} · {} · {}".format(
            ARTEN.get(self.art, self.art), self.line(self.wz, T=1, D=1),
            self.line(self.awz_ein, S=3000) or "–", self.line(self.c_pos, W=90))


# Felder für das Formular: (Schlüssel, Beschriftung, Hinweis, Reiter)
FELDER: List[Tuple[str, str, str, str]] = [
    ("art", "Programmformat", "", "format"),
    ("kommentar", "Kommentar", ";{TEXT} oder ({TEXT})", "format"),
    ("kopf", "Programmanfang", "| = neue Zeile · {NAME} {NR}", "format"),
    ("start", "Startsatz", "nach dem Kopf", "format"),
    ("ende", "Programmende", "| = neue Zeile", "format"),
    ("kreis", "Kreisradius", "CR= (Sinumerik) · R (Fanuc)", "format"),
    ("dezimalpunkt", "Immer mit Dezimalpunkt (X20. statt X20)", "", "format"),
    ("eine_zeile", "Werkzeug, Vorschubart, Ebene, Drehzahl, Kühlung in einer Zeile", "", "format"),
    ("wz", "Werkzeugaufruf", "T{T2}D{D} → T01D1 · T{T2}{D2} → T0101", "drehen"),
    ("vorschub_u", "Vorschub mm/U", "G95 · G99", "drehen"),
    ("ebene_drehen", "Ebene Drehen", "G18", "drehen"),
    ("spindel_ein", "Hauptspindel ein", "{S} = Drehzahl", "drehen"),
    ("spindel_aus", "Hauptspindel aus", "vor dem Positionieren", "drehen"),
    ("kuehl_ein", "Kühlmittel ein", "M8", "drehen"),
    ("kuehl_aus", "Kühlmittel aus", "leer = nicht ausschalten", "drehen"),
    ("wechsel_x", "Werkzeugwechsel X", "Durchmesser", "drehen"),
    ("wechsel_z", "Werkzeugwechsel Z", "", "drehen"),
    ("vorschub_min", "Vorschub mm/min", "G94 · G98", "achsen"),
    ("ebene_radial", "Ebene radial (Y-Achse)", "G19", "achsen"),
    ("ebene_axial", "Ebene axial (Stirn)", "G17", "achsen"),
    ("c_ein", "C-Achse ein", "leer = nicht nötig", "achsen"),
    ("c_pos", "Spindel positionieren", "{W} = Winkel", "achsen"),
    ("klemmen", "Spindel klemmen", "leer = keine Klemmung", "achsen"),
    ("loesen", "Klemmung lösen", "", "achsen"),
    ("c_aus", "C-Achse aus", "", "achsen"),
    ("awz_ein", "Angetr. Werkzeug ein", "{S} = Drehzahl", "achsen"),
    ("awz_aus", "Angetr. Werkzeug aus", "", "achsen"),
]

VORLAGEN_NAMEN = {
    "sin_s3": "Siemens Sinumerik – angetr. Werkzeug S3= / M3=",
    "sin_s4": "Siemens Sinumerik – angetr. Werkzeug S4= / M4=",
    "fanuc": "Fanuc – C-Achse, T0101, Dezimalpunkt",
}


def vorlage(key: str) -> Postprozessor:
    """Fertige Vorlage. Bei Fanuc sind die M-Befehle für angetriebenes Werkzeug und C-Achse
    Beispiele – jede Maschine hat eigene, siehe Maschinenhandbuch."""
    if key == "sin_s4":
        return Postprozessor(awz_ein="S4={S} M4=3", awz_aus="M4=5")
    if key == "fanuc":
        return Postprozessor(
            art="fanuc", kommentar="({TEXT})", kopf="%|O{NR} ({NAME})", start="G18 G21 G40 G54 G80 G99",
            ende="M30|%", kreis="R", dezimalpunkt=True, eine_zeile=False,
            wz="T{T2}{D2}", vorschub_u="G99", ebene_drehen="G18", spindel_ein="G97 S{S} M3", spindel_aus="M5",
            kuehl_ein="M8", kuehl_aus="M9", vorschub_min="G98", ebene_radial="G19", ebene_axial="G17",
            c_ein="", c_pos="G00 C{W}", klemmen="", loesen="", c_aus="", awz_ein="M13 S{S}", awz_aus="M15")
    return Postprozessor()


def fuer_maschine(machine) -> Postprozessor:
    """Postprozessor einer Maschine (MachineProfile); ohne eigene Einstellung die Vorlage zur Steuerung."""
    return Postprozessor.from_dict(getattr(machine, "post", None) or {}, getattr(machine, "control", ""))
