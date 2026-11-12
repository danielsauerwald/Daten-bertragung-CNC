"""Bilder lesen mit Claude (Anthropic): M-/G-Code-Listen → Postprozessor, Zyklus-Fotos → Zyklus.

Nur in der PC-Version (Windows 10/11): sie bringt das Paket ``anthropic`` und Pillow mit. In der
Windows-7-Version (Toughbook) fehlen beide – dann meldet :func:`verfuegbar` das.

Ablauf: Bild verkleinern (lange Kante ≤ 2576 px, höchstens 3,75 Megapixel – mehr liest das Modell
nicht), als JPEG an die Messages-API schicken, Antwort als JSON nach festem Schema
(Structured Outputs). Lehnt das Modell ab, springt serverseitig ein Ersatzmodell ein
(``fallbacks: "default"``).
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

MODELL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_KANTE = 2576
MAX_PIXEL = 3_750_000
MAX_BYTES = 4_500_000  # Grenze je Bild bei der API: 5 MB (Base64 etwas größer)
BILD_ENDUNGEN = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic")

MEDIA = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
         ".gif": "image/gif"}


class KIFehler(Exception):
    """Meldung für den Bediener (deutsch, ohne Fachchinesisch)."""


# --------------------------------------------------------------------------
# Einstellungen: API-Schlüssel (in der config.json) oder Umgebungsvariable
# --------------------------------------------------------------------------

_cfg = None  # AppConfig – vom Hauptfenster gesetzt


def einrichten(cfg) -> None:
    global _cfg
    _cfg = cfg


def schluessel() -> str:
    own = getattr(_cfg, "ki_schluessel", "") if _cfg is not None else ""
    return (own or os.environ.get("ANTHROPIC_API_KEY", "")).strip()


def schluessel_speichern(wert: str) -> None:
    if _cfg is None:
        return
    _cfg.ki_schluessel = (wert or "").strip()
    try:
        _cfg.save()
    except OSError:
        pass


def verfuegbar() -> Tuple[bool, str]:
    """(ok, Hinweis) – ob diese Programmversion Bilder lesen kann."""
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False, ("Bilder lesen gibt es nur in der PC-Version (Windows 10/11). Diese Version läuft auch "
                       "unter Windows 7 und enthält die nötigen Bausteine nicht.")
    return True, ""


def pillow_da() -> bool:
    try:
        import PIL  # noqa: F401
    except ImportError:
        return False
    return True


# --------------------------------------------------------------------------
# Bilder
# --------------------------------------------------------------------------


def bild_laden(pfad: str) -> Tuple[str, bytes]:
    """Bild für die Anfrage: (Media-Type, Bytes). Mit Pillow gedreht (EXIF), verkleinert, als JPEG."""
    if not os.path.isfile(pfad):
        raise KIFehler("Bild nicht gefunden: {}".format(pfad))
    try:
        from PIL import Image, ImageOps
    except ImportError:
        ext = os.path.splitext(pfad)[1].lower()
        if ext not in MEDIA:
            raise KIFehler("Dieses Bildformat geht nur mit Pillow (PC-Version): {}".format(os.path.basename(pfad)))
        with open(pfad, "rb") as fh:
            data = fh.read()
        if len(data) > MAX_BYTES:
            raise KIFehler("Bild zu groß ({:.1f} MB) – bitte kleiner speichern.".format(len(data) / 1e6))
        return MEDIA[ext], data
    try:
        with Image.open(pfad) as im:
            im = ImageOps.exif_transpose(im)  # Handyfotos stehen sonst oft quer
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            w, h = im.size
            f = min(1.0, MAX_KANTE / float(max(w, h)), (MAX_PIXEL / float(w * h)) ** 0.5)
            if f < 1.0:
                im = im.resize((max(1, int(w * f)), max(1, int(h * f))), Image.LANCZOS)
            for q in (90, 82, 72, 60):
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=q, optimize=True)
                if buf.tell() <= MAX_BYTES:
                    return "image/jpeg", buf.getvalue()
    except KIFehler:
        raise
    except Exception as exc:  # beschädigte Datei, unbekanntes Format …
        raise KIFehler("Bild kann nicht gelesen werden ({}): {}".format(os.path.basename(pfad), exc))
    raise KIFehler("Bild ist auch verkleinert noch zu groß: {}".format(os.path.basename(pfad)))


def bilder_ordner(machine_id: str) -> str:
    from .config import config_dir

    base = os.path.dirname(getattr(_cfg, "path", "") or "") or config_dir()
    return os.path.join(base, "bilder", re.sub(r"[^A-Za-z0-9_-]", "_", machine_id or "allgemein"))


def bild_ablegen(machine_id: str, pfad: str, art: str) -> str:
    """Verkleinerte Kopie bei der Maschine ablegen; Ergebnis: Dateiname (relativ zum Bilder-Ordner)."""
    media, data = bild_laden(pfad)
    ordner = bilder_ordner(machine_id)
    os.makedirs(ordner, exist_ok=True)
    stamm = re.sub(r"[^A-Za-z0-9_-]+", "_", os.path.splitext(os.path.basename(pfad))[0])[:40] or "bild"
    ext = ".jpg" if media == "image/jpeg" else os.path.splitext(pfad)[1].lower()
    name = "{}_{}_{}{}".format(art, time.strftime("%Y%m%d_%H%M%S"), stamm, ext)
    with open(os.path.join(ordner, name), "wb") as fh:
        fh.write(data)
    return name


# --------------------------------------------------------------------------
# Anfrage
# --------------------------------------------------------------------------

SYSTEM = ("Du liest Fotos und Bildschirmfotos aus Handbüchern und von Bedienfeldern von CNC-Drehmaschinen "
          "(Siemens Sinumerik, Fanuc und ähnliche) für ein Programm, das NC-Programme erzeugt. Lies Codes, "
          "Adressen und Parameternamen buchstabengenau ab. Was im Bild nicht steht oder nicht sicher lesbar "
          "ist, lässt du weg oder kennzeichnest es als unsicher – nichts dazuerfinden, keine Werte aus dem "
          "Gedächtnis ergänzen. Antworte auf Deutsch.")


def client_erzeugen(schluessel_wert: str = ""):
    try:
        import anthropic
    except ImportError:
        raise KIFehler(verfuegbar()[1])
    key = schluessel_wert or schluessel()
    if not key:
        raise KIFehler("Kein API-Schlüssel eingetragen (⚙ Maschinen → „Bilder lesen“).")
    return anthropic.Anthropic(api_key=key, timeout=240.0, max_retries=2)


def _fehlertext(exc: Exception) -> Optional[str]:
    """Fehler der Anthropic-Bibliothek in eine verständliche Meldung übersetzen."""
    try:
        import anthropic
    except ImportError:
        return None
    if isinstance(exc, anthropic.AuthenticationError):
        return "Der API-Schlüssel wird nicht angenommen. Bitte unter ⚙ Maschinen → „Bilder lesen“ prüfen."
    if isinstance(exc, anthropic.PermissionDeniedError):
        return "Der API-Schlüssel hat keine Berechtigung für dieses Modell."
    if isinstance(exc, anthropic.RateLimitError):
        return "Zu viele Anfragen kurz hintereinander – bitte eine Minute warten und nochmal versuchen."
    if isinstance(exc, anthropic.BadRequestError):
        return "Anfrage abgelehnt: {}".format(getattr(exc, "message", exc))
    if isinstance(exc, anthropic.APIStatusError):
        code = getattr(exc, "status_code", 0)
        if code == 402 or "credit" in str(exc).lower():
            return "Das Guthaben beim API-Konto ist aufgebraucht (console.anthropic.com → Billing)."
        return "Der Dienst meldet einen Fehler ({}). Bitte später nochmal versuchen.".format(code)
    if isinstance(exc, anthropic.APITimeoutError):
        return "Keine Antwort in der Zeit – bitte nochmal versuchen (weniger Bilder auf einmal)."
    if isinstance(exc, anthropic.APIConnectionError):
        return "Keine Verbindung zu api.anthropic.com – ist der PC mit dem Internet verbunden?"
    return None


def anfrage(bilder: Sequence[Tuple[str, bytes]], aufgabe: str, schema: Dict[str, Any], client=None,
            effort: str = "high") -> Dict[str, Any]:
    """Bilder + Aufgabe an Claude, Antwort als JSON nach ``schema``."""
    if not bilder:
        raise KIFehler("Kein Bild ausgewählt.")
    content: List[Dict[str, Any]] = []
    for media, data in bilder:
        content.append({"type": "image", "source": {"type": "base64", "media_type": media,
                                                     "data": base64.standard_b64encode(data).decode("ascii")}})
    content.append({"type": "text", "text": aufgabe})
    if client is None:
        client = client_erzeugen()
    try:
        resp = client.beta.messages.create(
            model=MODELL,
            max_tokens=16000,
            betas=[FALLBACK_BETA],
            fallbacks="default",  # lehnt das Modell ab, rechnet serverseitig ein Ersatzmodell
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            system=SYSTEM,
            messages=[{"role": "user", "content": content}],
        )
    except KIFehler:
        raise
    except Exception as exc:
        text = _fehlertext(exc)
        if text is None:
            raise
        raise KIFehler(text)
    if getattr(resp, "stop_reason", "") == "refusal":
        raise KIFehler("Das Modell hat die Anfrage abgelehnt. Bitte ein anderes Bild (nur die Code-Liste bzw. "
                       "den Zyklus) versuchen.")
    if getattr(resp, "stop_reason", "") == "max_tokens":
        raise KIFehler("Die Antwort wurde zu lang – bitte weniger Bilder auf einmal lesen.")
    texte = [b.text for b in getattr(resp, "content", []) if getattr(b, "type", "") == "text"]
    for text in reversed(texte):  # nach einem Ersatzmodell steht die gültige Antwort zuletzt
        try:
            data = json.loads(text)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    raise KIFehler("Die Antwort war nicht lesbar – bitte nochmal versuchen.")


# --------------------------------------------------------------------------
# M-/G-Code-Listen → Postprozessor
# --------------------------------------------------------------------------

BOOL_FELDER = ("dezimalpunkt", "eine_zeile", "zyl_entgraten", "zyl_grad")
ZAHL_FELDER = ("wechsel_x", "wechsel_z")
# Platzhalter, ohne die ein Feld nicht funktioniert
PFLICHT_PLATZHALTER = {"wz": ("{T",), "spindel_ein": ("{S}",), "awz_ein": ("{S}",), "c_pos": ("{W}",),
                       "kommentar": ("{TEXT}",)}


@dataclass
class Vorschlag:
    feld: str
    label: str
    alt: str
    neu: str
    begruendung: str
    sicher: bool
    problem: str = ""

    @property
    def uebernehmen(self) -> bool:
        """Vorbelegung im Prüffenster: sichere Vorschläge ohne Problem."""
        return self.sicher and not self.problem


@dataclass
class PostproErgebnis:
    vorschlaege: List[Vorschlag] = field(default_factory=list)
    codes: List[Dict[str, str]] = field(default_factory=list)
    steuerung: str = ""
    hinweise: List[str] = field(default_factory=list)


def _postpro_schema(felder: Sequence[str]) -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "steuerung": {"type": "string", "description": "Steuerung/Hersteller, falls im Bild erkennbar, sonst leer"},
            "codes": {"type": "array", "items": {
                "type": "object",
                "properties": {"code": {"type": "string"}, "bedeutung": {"type": "string"}},
                "required": ["code", "bedeutung"], "additionalProperties": False}},
            "vorschlaege": {"type": "array", "items": {
                "type": "object",
                "properties": {"feld": {"type": "string", "enum": list(felder)},
                               "wert": {"type": "string"},
                               "begruendung": {"type": "string"},
                               "sicher": {"type": "boolean"}},
                "required": ["feld", "wert", "begruendung", "sicher"], "additionalProperties": False}},
            "hinweise": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["steuerung", "codes", "vorschlaege", "hinweise"],
        "additionalProperties": False,
    }


def _postpro_aufgabe(pp, steuerung: str) -> str:
    from .postpro import FELDER

    zeilen = []
    for key, label, hint, _tab in FELDER:
        wert = getattr(pp, key)
        zeilen.append("- {}: {}{} – jetzt: {}".format(key, label, " ({})".format(hint) if hint else "",
                                                      json.dumps(wert, ensure_ascii=False)))
    return (
        "Die Bilder zeigen eine M-Code- und/oder G-Code-Liste einer CNC-Drehmaschine (Steuerung laut "
        "Maschinenliste: {st}).\n\n"
        "1. Schreibe in „codes“ jeden lesbaren Code mit seiner Bedeutung ab (z. B. M13 – angetriebenes "
        "Werkzeug Rechtslauf), in der Reihenfolge der Liste.\n"
        "2. Schlage in „vorschlaege“ Werte für die Felder des Postprozessors vor – aber nur, wenn ein Code "
        "aus den Bildern die Funktion eindeutig erfüllt. Gib in „begruendung“ den Code und seine Bedeutung "
        "aus dem Bild an. „sicher“ = false, wenn die Zuordnung eine Vermutung ist.\n"
        "3. Schreibweise der Felder: Platzhalter {{T}} {{T2}} Werkzeug, {{D}} {{D2}} Korrektur, {{S}} Drehzahl, "
        "{{W}} Winkel, {{TEXT}} Kommentar, {{NAME}} Programmname, {{NR}} Programmnummer, {{ZD}}/{{ZR}} "
        "Zylinderdurchmesser/-radius für die Zylinder-Interpolation (TRACYL bzw. G07.1); „|“ trennt Zeilen. "
        "Beispiele: Siemens angetriebenes Werkzeug „S3={{S}} M3=3“, Fanuc „M13 S{{S}}“; Spindel "
        "positionieren „SPOS=DC({{W}})“ oder „G00 C{{W}}“; C-Achse ein z. B. „M34“. Felder "
        "dezimalpunkt/eine_zeile/zyl_entgraten/zyl_grad als „ja“/„nein“, wechsel_x/wechsel_z als Zahl.\n"
        "4. Felder, zu denen die Bilder nichts sagen, lässt du weg. Werkzeugwechselpunkt nur, wenn er im Bild "
        "steht.\n"
        "5. In „hinweise“: Auffälligkeiten, z. B. mehrere mögliche Codes, unleserliche Stellen, Codes, die "
        "nur mit Option gehen.\n\n"
        "Felder des Postprozessors (Schlüssel: Bedeutung – jetziger Wert):\n{felder}"
    ).format(st=steuerung or "unbekannt", felder="\n".join(zeilen))


def _wert_text(key: str, value: Any) -> str:
    from .postpro import ARTEN, fmt

    if key == "art":
        return ARTEN.get(value, str(value))
    if isinstance(value, bool):
        return "ja" if value else "nein"
    if isinstance(value, float):
        return fmt(value)
    return str(value)


def wert_umwandeln(key: str, text: str) -> Any:
    """Text aus dem Vorschlag in den Feldtyp; ValueError bei Unsinn."""
    t = str(text).strip()
    if key in BOOL_FELDER:
        low = t.lower()
        if low in ("ja", "true", "1", "yes", "an", "ein"):
            return True
        if low in ("nein", "false", "0", "no", "aus"):
            return False
        raise ValueError(t)
    if key in ZAHL_FELDER:
        return float(t.replace(",", "."))
    if key == "art":
        low = t.lower()
        if "fanuc" in low or "iso" in low:
            return "fanuc"
        if "sin" in low or "siemens" in low:
            return "sinumerik"
        raise ValueError(t)
    return t


def postpro_auswerten(data: Dict[str, Any], pp) -> PostproErgebnis:
    """Antwort des Modells prüfen und mit dem jetzigen Postprozessor vergleichen."""
    from .postpro import FELDER

    labels = {k: lab for k, lab, _h, _t in FELDER}
    res = PostproErgebnis(steuerung=str(data.get("steuerung", "") or ""))
    for c in data.get("codes", []) or []:
        if isinstance(c, dict) and str(c.get("code", "")).strip():
            res.codes.append({"code": str(c["code"]).strip(), "bedeutung": str(c.get("bedeutung", "")).strip()})
    res.hinweise = [str(h) for h in data.get("hinweise", []) or [] if str(h).strip()]
    gesehen = set()
    for v in data.get("vorschlaege", []) or []:
        if not isinstance(v, dict):
            continue
        key = str(v.get("feld", ""))
        if key not in labels or key in gesehen:
            continue
        gesehen.add(key)
        roh = str(v.get("wert", "")).strip()
        problem = ""
        try:
            neu = wert_umwandeln(key, roh)
        except ValueError:
            neu, problem = roh, "unverständlicher Wert"
        if not problem:
            for ph in PFLICHT_PLATZHALTER.get(key, ()):
                if ph not in str(neu):
                    problem = "Platzhalter {} fehlt".format("{T} bzw. {T2}" if ph == "{T" else ph)
        alt = getattr(pp, key)
        if not problem and neu == alt:
            continue  # steht schon so drin
        res.vorschlaege.append(Vorschlag(key, labels[key], _wert_text(key, alt),
                                         _wert_text(key, neu) if not problem else roh,
                                         str(v.get("begruendung", "")).strip(), bool(v.get("sicher", False)),
                                         problem))
    order = [k for k, *_r in FELDER]
    res.vorschlaege.sort(key=lambda x: order.index(x.feld))
    return res


def postpro_anwenden(pp, vorschlaege: Sequence[Vorschlag]):
    """Neue Kopie des Postprozessors mit den gewählten Vorschlägen."""
    from .postpro import ARTEN

    neu = pp.copy()
    inv = {b: a for a, b in ARTEN.items()}
    for v in vorschlaege:
        if v.problem:
            continue
        text = inv.get(v.neu, v.neu) if v.feld == "art" else v.neu
        setattr(neu, v.feld, wert_umwandeln(v.feld, text))
    return neu


def postpro_aus_bildern(pfade: Sequence[str], pp, steuerung: str = "", client=None) -> PostproErgebnis:
    from .postpro import FELDER

    bilder = [bild_laden(p) for p in pfade]
    data = anfrage(bilder, _postpro_aufgabe(pp, steuerung), _postpro_schema([k for k, *_r in FELDER]),
                   client=client)
    return postpro_auswerten(data, pp)


# --------------------------------------------------------------------------
# Zyklus-Foto → Zyklus
# --------------------------------------------------------------------------

ZYKLUS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "Kurzname, z. B. CYCLE83 oder G83"},
        "titel": {"type": "string", "description": "Was der Zyklus macht, z. B. Tieflochbohren"},
        "steuerung": {"type": "string"},
        "vorlage": {"type": "string",
                    "description": "NC-Text mit Platzhaltern {NAME} für jeden Parameter; | trennt Zeilen"},
        "parameter": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "bedeutung": {"type": "string"},
                "einheit": {"type": "string"},
                "standard": {"type": "string"},
                "pflicht": {"type": "boolean"},
            },
            "required": ["name", "bedeutung", "einheit", "standard", "pflicht"],
            "additionalProperties": False}},
        "hinweise": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["name", "titel", "steuerung", "vorlage", "parameter", "hinweise"],
    "additionalProperties": False,
}


def _zyklus_aufgabe(steuerung: str) -> str:
    return (
        "Die Bilder zeigen einen Bearbeitungszyklus einer CNC-Steuerung (Steuerung laut Maschinenliste: {st}) – "
        "z. B. die Aufrufzeile mit Parameterliste aus dem Handbuch, eine Parametertabelle oder die "
        "Eingabemaske an der Maschine. Erstelle daraus eine Vorlage, mit der man den Zyklus ausfüllen und als "
        "NC-Satz ins Programm schreiben kann.\n\n"
        "- „vorlage“: der NC-Text genau in der Schreibweise der Steuerung, jeder Parameter als Platzhalter in "
        "geschweiften Klammern mit genau dem Namen aus „parameter“. Siemens z. B. "
        "„CYCLE83({{RTP}},{{RFP}},{{SDIS}},{{DP}},{{DPR}})“ – Reihenfolge und Anzahl der Kommas wie im Bild. "
        "Fanuc/ISO z. B. „G83 X{{X}} Z{{Z}} R{{R}} Q{{Q}} F{{F}}“. Mehrere Sätze mit „|“ trennen "
        "(z. B. „G74 R{{R}}|G74 Z{{Z}} Q{{Q}} F{{F}}“).\n"
        "- „parameter“: in der Reihenfolge der Vorlage; „name“ nur aus Großbuchstaben, Ziffern und _ "
        "(wie im Bild, z. B. RTP, _VARI, Q); „bedeutung“ kurz auf Deutsch; „einheit“ (mm, mm/U, s, U/min, "
        "Grad oder leer); „standard“ nur, wenn das Bild einen Vorgabe- oder Beispielwert zeigt, sonst leer; "
        "„pflicht“ = true, wenn der Zyklus ohne den Wert nicht läuft.\n"
        "- Nichts ergänzen, was nicht im Bild steht; Unsicheres in „hinweise“."
    ).format(st=steuerung or "unbekannt")


def zyklus_aus_bildern(pfade: Sequence[str], steuerung: str = "", client=None):
    from .zyklen import Zyklus, aus_ki

    bilder = [bild_laden(p) for p in pfade]
    data = anfrage(bilder, _zyklus_aufgabe(steuerung), ZYKLUS_SCHEMA, client=client)
    z: Zyklus = aus_ki(data)
    return z


# --------------------------------------------------------------------------
# Selbsttest (PC-EXE): kompletter Weg über HTTP, aber gegen einen lokalen Schein-Server
# --------------------------------------------------------------------------


def selbsttest() -> bool:
    """Prüft, ob Bibliothek, Bildaufbereitung und Antwortverarbeitung in der EXE vollständig sind."""
    import http.server
    import threading

    import anthropic

    antwort = {"id": "msg_test", "type": "message", "role": "assistant", "model": MODELL,
               "content": [{"type": "text", "text": json.dumps({"ok": True})}],
               "stop_reason": "end_turn", "stop_sequence": None,
               "usage": {"input_tokens": 1, "output_tokens": 1}}

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            n = int(self.headers.get("content-length", "0"))
            body = json.loads(self.rfile.read(n) or b"{}")
            ok = body.get("model") == MODELL and body.get("output_config", {}).get("format")
            data = json.dumps(antwort if ok else {"type": "error", "error": {"type": "invalid_request_error",
                                                                              "message": "bad"}}).encode()
            self.send_response(200 if ok else 400)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (40, 20), "white").save(buf, "PNG")
        client = anthropic.Anthropic(api_key="test", base_url="http://127.0.0.1:{}".format(srv.server_port),
                                     max_retries=0, timeout=10.0)
        data = anfrage([("image/png", buf.getvalue())], "Test", {"type": "object", "properties": {
            "ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}, client=client)
        return data == {"ok": True}
    finally:
        srv.shutdown()


def im_hintergrund(widget, arbeit: Callable[[], Any], fertig: Callable[[Any, Optional[Exception]], None],
                   abfrage_ms: int = 150) -> None:
    """``arbeit`` in einem eigenen Thread ausführen (die Oberfläche bleibt bedienbar), danach ``fertig``
    im Tk-Thread mit (Ergebnis, Fehler) aufrufen."""
    import threading

    box: Dict[str, Any] = {}

    def run():
        try:
            box["res"] = arbeit()
        except Exception as exc:  # wird im Tk-Thread gemeldet
            box["err"] = exc
        box["done"] = True

    threading.Thread(target=run, daemon=True).start()

    def poll():
        if not box.get("done"):
            try:
                widget.after(abfrage_ms, poll)
            except Exception:
                pass
            return
        fertig(box.get("res"), box.get("err"))

    widget.after(abfrage_ms, poll)
