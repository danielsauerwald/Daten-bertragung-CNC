"""Serielle Schnittstelle: Senden und Empfangen in einem Hintergrund-Thread.

Die Oberfläche bleibt dadurch immer bedienbar. Der Thread meldet alles über
eine Queue zurück (Ereignisse als Tupel), die die Oberfläche regelmäßig abholt.

Ereignisse:
    ("progress", gesendete_bytes, gesamt_bytes, gesendete_zeilen, gesamt_zeilen)
    ("text", empfangener_text)          – bereits normalisiert (nur \\n)
    ("rx", anzahl_bytes)
    ("tx", anzahl_bytes)
    ("done", art, ok, meldung)          – art: "send" | "receive"
"""

from __future__ import annotations

import codecs
import queue
import threading
import time
from typing import Dict, List, Optional

try:
    import serial
    import serial.tools.list_ports
except ImportError:  # pragma: no cover - wird in der Oberfläche gemeldet
    serial = None  # type: ignore[assignment]

from .config import MachineProfile
from .textutil import ENCODING, EndDetector, LineEndingNormalizer

WRITE_BLOCK = 64  # Bytes pro write() – klein genug für flüssigen Fortschritt und Abbruch


class SerialError(Exception):
    pass


def list_ports() -> List[Dict[str, str]]:
    """Verfügbare Schnittstellen, z. B. [{"device": "COM1", "description": "..."}]."""
    if serial is None:
        return []
    result = []
    try:
        ports = serial.tools.list_ports.comports()
    except Exception:  # pragma: no cover - treiberabhängig
        return []
    for p in sorted(ports, key=lambda p: _port_sort_key(p.device)):
        result.append({"device": p.device, "description": p.description or ""})
    return result


def _port_sort_key(name: str):
    digits = "".join(ch for ch in name if ch.isdigit())
    return (name.rstrip("0123456789"), int(digits) if digits else 0)


def _serial_kwargs(profile: MachineProfile) -> Dict[str, object]:
    return {
        "port": profile.port,
        "baudrate": int(profile.baudrate),
        "bytesize": int(profile.bytesize),
        "parity": profile.parity,
        "stopbits": profile.stopbits,
        "xonxoff": profile.flow == "xonxoff",
        "rtscts": profile.flow == "rtscts",
        "dsrdtr": profile.flow == "dsrdtr",
        "timeout": 0.1,
        "write_timeout": None,
    }


def friendly_error(exc: Exception, port: str) -> str:
    text = str(exc)
    low = text.lower()
    if "access" in low or "zugriff" in low or "permission" in low or "busy" in low:
        return ("{} ist belegt. Läuft noch ein anderes Programm, das die Schnittstelle benutzt "
                "(z. B. das alte Datenübertragungsprogramm)?".format(port))
    if ("could not open" in low or "cannot find" in low or "filenotfound" in low or "no such" in low
            or "nicht gefunden" in low or "nicht finden" in low):
        return ("{} wurde nicht gefunden. Ist das Kabel bzw. der USB-Adapter eingesteckt? "
                "Unter Maschinen → Bearbeiten kann der richtige Port gewählt werden.".format(port))
    return "Fehler an {}: {}".format(port, text)


class SerialLink:
    """Verwaltet die geöffnete Schnittstelle und den Übertragungs-Thread."""

    def __init__(self, events: "queue.Queue") -> None:
        self.events = events
        self.ser = None
        self.profile: Optional[MachineProfile] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.mode: Optional[str] = None  # "send" | "receive" | None
        self.last_activity = 0.0

    # -- Öffnen / Schließen --------------------------------------------------

    @property
    def is_open(self) -> bool:
        return self.ser is not None and bool(getattr(self.ser, "is_open", False))

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def open(self, profile: MachineProfile) -> None:
        if serial is None:
            raise SerialError("Das Python-Modul 'pyserial' fehlt (pip install pyserial).")
        self.close()
        try:
            self.ser = serial.Serial(**_serial_kwargs(profile))
        except (serial.SerialException, ValueError, OSError) as exc:
            self.ser = None
            raise SerialError(friendly_error(exc, profile.port)) from exc
        self.profile = profile

    def close(self) -> None:
        self.cancel()
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None
        self.mode = None

    def ensure_open(self, profile: MachineProfile) -> None:
        """Port öffnen bzw. mit neuen Einstellungen neu öffnen."""
        if self.is_open and self.profile is not None and _serial_kwargs(self.profile) == _serial_kwargs(profile):
            self.profile = profile
            return
        self.open(profile)

    def modem_lines(self) -> Optional[Dict[str, bool]]:
        """Zustand der Steuerleitungen (für die Anzeige-LEDs)."""
        if not self.is_open:
            return None
        try:
            return {"CTS": bool(self.ser.cts), "DSR": bool(self.ser.dsr),
                    "CD": bool(self.ser.cd), "RI": bool(self.ser.ri)}
        except Exception:
            return None

    # -- Übertragungen -------------------------------------------------------

    def start_send(self, chunks: List[bytes], profile: MachineProfile) -> None:
        self._start("send", self._send_worker, chunks, profile)

    def start_receive(self, profile: MachineProfile) -> None:
        self._start("receive", self._receive_worker, profile)

    def _start(self, mode: str, target, *args) -> None:
        if self._thread is not None:
            # Der vorige Thread hat sein "done" evtl. gerade erst gemeldet
            self._thread.join(timeout=1.0)
        if self.busy:
            raise SerialError("Es läuft bereits eine Übertragung.")
        if not self.is_open:
            raise SerialError("Die Schnittstelle ist nicht geöffnet.")
        self._stop.clear()
        self._clear_abort_flags()
        self.mode = mode
        self.last_activity = time.monotonic()
        self._thread = threading.Thread(target=self._run, args=(mode, target) + args, daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        """Senden abbrechen bzw. Empfang beenden."""
        self._stop.set()
        ser = self.ser
        if ser is None or not self.busy:
            return
        # Ein blockierendes write()/read() sofort abbrechen (z. B. wenn die
        # Maschine per Handshake "Stopp" signalisiert und nie wieder freigibt).
        fn = "cancel_write" if self.mode == "send" else "cancel_read"
        try:
            getattr(ser, fn)()
        except Exception:
            pass

    def _clear_abort_flags(self) -> None:
        # pyserial (Linux) merkt sich einen Abbruch in einer Pipe; ein alter
        # Abbruch darf die nächste Übertragung nicht sofort beenden.
        import os
        for name in ("pipe_abort_read_r", "pipe_abort_write_r"):
            fd = getattr(self.ser, name, None)
            if fd is None:
                continue
            try:
                while os.read(fd, 1000):
                    pass
            except OSError:
                pass

    def _post(self, *event) -> None:
        self.events.put(event)

    def _run(self, mode: str, target, *args) -> None:
        try:
            target(*args)
        except Exception as exc:  # Port weg, Kabel gezogen, ...
            port = self.profile.port if self.profile else "?"
            self._post("done", mode, False, friendly_error(exc, port))
        finally:
            self.mode = None

    def _send_worker(self, chunks: List[bytes], profile: MachineProfile) -> None:
        ser = self.ser
        total = sum(len(c) for c in chunks)
        total_lines = len(chunks)
        sent = 0
        char_delay = max(0, profile.char_delay_ms) / 1000.0
        line_delay = max(0, profile.line_delay_ms) / 1000.0
        self._post("progress", 0, total, 0, total_lines)

        for line_no, chunk in enumerate(chunks, start=1):
            if char_delay > 0:
                pieces = [chunk[i:i + 1] for i in range(len(chunk))]
            else:
                pieces = [chunk[i:i + WRITE_BLOCK] for i in range(0, len(chunk), WRITE_BLOCK)]
            for piece in pieces:
                if self._stop.is_set():
                    self._abort_send(sent, total)
                    return
                n = ser.write(piece)
                n = len(piece) if n is None else n
                sent += n
                self.last_activity = time.monotonic()
                self._post("tx", n)
                if n < len(piece):  # Schreiben wurde abgebrochen
                    self._abort_send(sent, total)
                    return
                if char_delay > 0:
                    self._drain()
                    self._stop.wait(char_delay)
            self._post("progress", sent, total, line_no, total_lines)
            if line_delay > 0:
                self._drain()
                self._stop.wait(line_delay)

        # Warten, bis wirklich alles aus dem Puffer raus ist – sonst würden beim
        # Schließen die letzten Zeilen abgeschnitten.
        self._drain()
        if self._stop.is_set():
            self._abort_send(sent, total)
            return
        self._post("done", "send", True, "{} Zeilen ({} Bytes) gesendet.".format(_fmt_int(total_lines), _fmt_int(total)))

    def _drain(self) -> None:
        """Warten, bis der Sendepuffer leer ist (abbrechbar)."""
        ser = self.ser
        try:
            while not self._stop.is_set() and ser.out_waiting > 0:
                time.sleep(0.02)
        except Exception:
            pass

    def _abort_send(self, sent: int, total: int) -> None:
        try:
            self.ser.reset_output_buffer()
        except Exception:
            pass
        self._post("done", "send", False, "Senden abgebrochen ({} von {} Bytes).".format(_fmt_int(sent), _fmt_int(total)))

    def _receive_worker(self, profile: MachineProfile) -> None:
        ser = self.ser
        try:
            ser.reset_input_buffer()
        except Exception:
            pass
        decoder = codecs.getincrementaldecoder(ENCODING)(errors="replace")
        norm = LineEndingNormalizer(strip_control=profile.strip_control_chars)
        detector = EndDetector(profile.end_mode)
        idle_timeout = max(0.0, float(profile.idle_timeout_s))
        total = 0
        got_data = False
        end_found = False
        last = time.monotonic()
        reason = "manual"

        while True:
            if self._stop.is_set():
                reason = "manual"
                break
            waiting = 0
            try:
                waiting = ser.in_waiting
            except Exception:
                pass
            data = ser.read(max(1, min(waiting, 4096)))
            now = time.monotonic()
            if data:
                total += len(data)
                got_data = True
                last = now
                self.last_activity = now
                self._post("rx", len(data))
                text = norm.feed(decoder.decode(data))
                if text:
                    self._post("text", text)
                    # kann sich wieder ändern (z. B. "%" gefolgt von "_N_...")
                    end_found = detector.feed(text)
                continue
            if end_found and now - last >= 0.5:
                reason = "end"
                break
            if got_data and idle_timeout > 0 and now - last >= idle_timeout:
                reason = "idle"
                break

        tail = norm.feed(decoder.decode(b"", final=True)) + norm.flush()
        if tail:
            self._post("text", tail)
        if not got_data:
            self._post("done", "receive", False, "Empfang beendet – es wurden keine Daten empfangen.")
            return
        why = {"end": "Programmende erkannt", "idle": "keine weiteren Daten", "manual": "von Hand beendet"}[reason]
        self._post("done", "receive", True, "{} Bytes empfangen ({}).".format(_fmt_int(total), why))


def _fmt_int(n: int) -> str:
    return "{:,}".format(n).replace(",", ".")
