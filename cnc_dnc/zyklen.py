"""Zyklen je Maschine: Vorlage mit Platzhaltern (aus einem Foto gelesen oder von Hand), ausfüllen, als NC-Text.

Eine Vorlage ist NC-Text in der Schreibweise der Steuerung, jeder Parameter als ``{NAME}``:

* Siemens: ``CYCLE83({RTP},{RFP},{SDIS},{DP},,{FDEP})`` – leere Werte bleiben leere Stellen,
  leere Stellen am Ende fallen weg (``CYCLE83(10,0,2,-20)``).
* Fanuc/ISO: ``G83 X{X} Z{Z} R{R} Q{Q} F{F}`` – ein Adresswort ohne Wert fällt ganz weg.
* ``|`` trennt Sätze.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

_PH = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass
class ZParam:
    name: str
    bedeutung: str = ""
    einheit: str = ""
    standard: str = ""
    pflicht: bool = False


@dataclass
class Zyklus:
    name: str = "ZYKLUS"
    titel: str = ""
    steuerung: str = ""
    vorlage: str = ""
    parameter: List[ZParam] = field(default_factory=list)
    hinweise: List[str] = field(default_factory=list)
    bild: str = ""  # Dateiname im Bilder-Ordner der Maschine
    werte: Dict[str, str] = field(default_factory=dict)  # zuletzt eingetragene Werte
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])

    @property
    def anzeige(self) -> str:
        return "{} – {}".format(self.name, self.titel) if self.titel else self.name

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Zyklus":
        if not isinstance(data, dict):
            return cls()
        z = cls()
        for key in ("name", "titel", "steuerung", "vorlage", "bild", "id"):
            if key in data:
                setattr(z, key, str(data[key] or ""))
        z.id = z.id or uuid.uuid4().hex[:10]
        z.hinweise = [str(h) for h in data.get("hinweise", []) or []]
        z.werte = {str(k): str(v) for k, v in (data.get("werte") or {}).items()} \
            if isinstance(data.get("werte"), dict) else {}
        for p in data.get("parameter", []) or []:
            if isinstance(p, dict) and str(p.get("name", "")).strip():
                z.parameter.append(ZParam(str(p["name"]).strip(), str(p.get("bedeutung", "") or ""),
                                          str(p.get("einheit", "") or ""), str(p.get("standard", "") or ""),
                                          bool(p.get("pflicht", False))))
        return z

    # -- Ausfüllen ----------------------------------------------------------------

    def platzhalter(self) -> List[str]:
        seen: List[str] = []
        for m in _PH.finditer(self.vorlage):
            if m.group(1) not in seen:
                seen.append(m.group(1))
        return seen

    def pruefen(self, werte: Optional[Dict[str, str]] = None) -> Tuple[List[str], List[str]]:
        """(Fehler, Hinweise) zur Vorlage und – wenn ``werte`` – zu den eingetragenen Werten."""
        err: List[str] = []
        warn: List[str] = []
        if not self.vorlage.strip():
            err.append("Die Vorlage ist leer.")
        names = [p.name for p in self.parameter]
        for n in names:
            if not _NAME.match(n):
                err.append("Parametername „{}“: nur Buchstaben, Ziffern und _.".format(n))
        if len(set(names)) != len(names):
            err.append("Ein Parametername kommt doppelt vor.")
        for ph in self.platzhalter():
            if ph not in names:
                err.append("In der Vorlage steht {{{}}}, aber es gibt keinen Parameter dazu.".format(ph))
        for n in names:
            if "{" + n + "}" not in self.vorlage:
                warn.append("Parameter {} kommt in der Vorlage nicht vor.".format(n))
        if werte is not None:
            for p in self.parameter:
                if p.pflicht and not str(werte.get(p.name, "")).strip():
                    err.append("{} ({}) fehlt.".format(p.name, p.bedeutung or "Pflichtwert"))
        return err, warn

    def code(self, werte: Dict[str, str]) -> str:
        """Vorlage ausfüllen (siehe Modul-Beschreibung)."""
        vals = {p.name: _wert(werte.get(p.name, "")) for p in self.parameter}
        out = []
        for line in self.vorlage.split("|"):
            line = _klammern(line, vals)
            line = _woerter(line, vals)
            line = re.sub(r"[ \t]+", " ", line).strip()
            if line:
                out.append(line)
        return "\n".join(out)


def _wert(text: Any) -> str:
    s = str(text or "").strip()
    if re.match(r"^[+-]?\d+,\d+$", s):  # Komma als Dezimaltrenner
        s = s.replace(",", ".")
    return s


def _ersetzen(text: str, vals: Dict[str, str]) -> str:
    return _PH.sub(lambda m: vals.get(m.group(1), ""), text)


def _klammern(line: str, vals: Dict[str, str]) -> str:
    """Parameterlisten in Klammern: leere Stellen bleiben, leere Stellen am Ende fallen weg."""
    def ersetze(m: "re.Match[str]") -> str:
        inhalt = m.group(1)
        if not _PH.search(inhalt):
            return m.group(0)  # z. B. Fanuc-Kommentar
        teile = [_ersetzen(t, vals).strip() for t in inhalt.split(",")]
        while teile and not teile[-1]:
            teile.pop()
        return "(" + ",".join(teile) + ")"

    return re.sub(r"\(([^()]*)\)", ersetze, line)


def _woerter(line: str, vals: Dict[str, str]) -> str:
    """Adresswörter außerhalb von Klammern: ohne Wert fällt das ganze Wort weg."""
    teile = re.split(r"(\([^()]*\))", line)
    out = []
    for k, teil in enumerate(teile):
        if k % 2:  # Klammerinhalt ist schon erledigt
            out.append(teil)
            continue
        woerter = []
        for w in teil.split(" "):
            namen = _PH.findall(w)
            if namen and all(not vals.get(n, "") for n in namen):
                continue
            woerter.append(_ersetzen(w, vals))
        out.append(" ".join(woerter))
    return "".join(out)


def aus_ki(data: Dict[str, Any]) -> Zyklus:
    """Antwort des Bild-Lesens in einen Zyklus; Namen werden vereinheitlicht, unbenutzte Platzhalter ergänzt."""
    z = Zyklus.from_dict({k: data.get(k) for k in ("name", "titel", "steuerung", "vorlage", "parameter",
                                                  "hinweise")})
    z.name = z.name.strip() or "ZYKLUS"
    gesehen = set()
    params = []
    for p in z.parameter:
        n = re.sub(r"[^A-Za-z0-9_]", "_", p.name.strip()) or "P"
        if n[0].isdigit():
            n = "P" + n
        if n in gesehen:
            continue
        if n != p.name:
            z.vorlage = z.vorlage.replace("{" + p.name + "}", "{" + n + "}")
            p.name = n
        gesehen.add(n)
        params.append(p)
    z.parameter = params
    for ph in z.platzhalter():  # Platzhalter ohne Beschreibung: als Parameter aufnehmen
        if ph not in gesehen:
            z.parameter.append(ZParam(ph))
            gesehen.add(ph)
    z.werte = {p.name: p.standard for p in z.parameter if p.standard}
    return z


def lade_liste(machine) -> List[Zyklus]:
    return [Zyklus.from_dict(d) for d in (getattr(machine, "zyklen", None) or []) if isinstance(d, dict)]


def speichere_liste(machine, zyklen: List[Zyklus]) -> None:
    machine.zyklen = [z.to_dict() for z in zyklen]


def als_text(z: Zyklus) -> str:
    """Parameterliste zum Bearbeiten: eine Zeile je Parameter ``NAME; Bedeutung; Einheit; Standard; Pflicht``."""
    return "\n".join("{}; {}; {}; {}; {}".format(p.name, p.bedeutung, p.einheit, p.standard,
                                                 "ja" if p.pflicht else "nein") for p in z.parameter)


def aus_text(text: str) -> List[ZParam]:
    out: List[ZParam] = []
    for line in str(text).splitlines():
        if not line.strip():
            continue
        teile = [t.strip() for t in line.split(";")]
        teile += [""] * (5 - len(teile))
        out.append(ZParam(teile[0], teile[1], teile[2], teile[3], teile[4].lower() in ("ja", "x", "1", "true",
                                                                                         "pflicht")))
    return out
