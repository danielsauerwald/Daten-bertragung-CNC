"""3D-Modell (STEP oder STL) einlesen und daraus ein Drehteil für den Kontur-Assistenten machen.

* STL (ASCII oder binär) liest das Programm selbst – überall, auch unter Windows 7.
* STEP liest die PC-Version mit „cascadio“ (Open CASCADE): Es liefert Dreiecke und zu jeder Fläche
  ihre genaue Form (Ebene, Zylinder, Kegel, Torus). Damit werden Drehachse, Bohrungen und
  Flächen exakt erkannt.

Ablauf: Drehachse finden → Lage des Teils (Z-Richtung, Nullpunkt Z0, Richtung C0) → Profil
(größter Radius je Z, Bohrung) → Konturpunkte mit Fasen/Radien, Innenkontur, Vorbohren →
Schlüsselflächen, Bohrungen radial und an der Stirnseite.
"""

from __future__ import annotations

import json
import math
import os
import struct
import tempfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .kontur import Fraesung, KPunkt, Teil, fmt

Vec = Tuple[float, float, float]
TOL = 1e-6


class CADFehler(Exception):
    """Meldung für den Bediener."""


# --------------------------------------------------------------------------
# Vektoren
# --------------------------------------------------------------------------


def _sub(a: Vec, b: Vec) -> Vec:
    return a[0] - b[0], a[1] - b[1], a[2] - b[2]


def _dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vec, b: Vec) -> Vec:
    return a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]


def _norm(a: Vec) -> float:
    return math.sqrt(_dot(a, a))


def _unit(a: Vec) -> Vec:
    n = _norm(a)
    return (a[0] / n, a[1] / n, a[2] / n) if n > 1e-12 else (0.0, 0.0, 1.0)


def _scale(a: Vec, s: float) -> Vec:
    return a[0] * s, a[1] * s, a[2] * s


def _add(a: Vec, b: Vec) -> Vec:
    return a[0] + b[0], a[1] + b[1], a[2] + b[2]


# --------------------------------------------------------------------------
# Modell
# --------------------------------------------------------------------------


@dataclass
class Modell:
    name: str = ""
    dreiecke: List[Tuple[Vec, Vec, Vec]] = field(default_factory=list)  # mm
    flaeche: List[int] = field(default_factory=list)  # je Dreieck: Index in ``flaechen`` oder -1
    flaechen: List[Dict[str, Any]] = field(default_factory=list)  # genaue Form (nur STEP), mm

    @property
    def grenzen(self) -> Tuple[Vec, Vec]:
        xs = [p[0] for t in self.dreiecke for p in t]
        ys = [p[1] for t in self.dreiecke for p in t]
        zs = [p[2] for t in self.dreiecke for p in t]
        return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))


_STEP_OK: Optional[bool] = None


def step_verfuegbar() -> bool:
    """Lässt sich Open CASCADE (cascadio) wirklich laden? Einmal prüfen, dann merken."""
    global _STEP_OK
    if _STEP_OK is None:
        try:
            import cascadio  # noqa: F401

            _STEP_OK = True
        except Exception:
            _STEP_OK = False
    return _STEP_OK


def lade(pfad: str) -> Modell:
    ext = os.path.splitext(pfad)[1].lower()
    name = os.path.splitext(os.path.basename(pfad))[0]
    if ext == ".stl":
        m = lade_stl(pfad)
    elif ext in (".step", ".stp"):
        m = lade_step(pfad)
    elif ext == ".glb":
        with open(pfad, "rb") as fh:
            m = lade_glb(fh.read())
    else:
        raise CADFehler("Dateityp {} wird nicht unterstützt (STEP, STP oder STL).".format(ext or "?"))
    m.name = name
    if len(m.dreiecke) < 4:
        raise CADFehler("Im Modell sind keine Flächen (Dreiecke) – ist es ein Volumenkörper?")
    return m


def drehkoerper(kontur: Sequence[Tuple[float, float]], n: int = 64, ring: float = 0.0) -> Modell:
    """Drehkörper um die Modell-Z-Achse aus einem geschlossenen Halbschnitt [(z, r), …] – für Tests und
    den Selbsttest. ``ring`` > 0 teilt lange Kanten in Stücke dieser Länge."""
    pts: List[Tuple[float, float]] = []
    kontur = list(kontur)
    for (z1, r1), (z2, r2) in zip(kontur, kontur[1:] + kontur[:1]):
        k = max(1, int(round(math.hypot(z2 - z1, r2 - r1) / ring))) if ring else 1
        for j in range(k):
            pts.append((z1 + (z2 - z1) * j / k, r1 + (r2 - r1) * j / k))
    m = Modell("drehteil")
    for (z1, r1), (z2, r2) in zip(pts, pts[1:] + pts[:1]):
        for i in range(n):
            a1, a2 = 2 * math.pi * i / n, 2 * math.pi * (i + 1) / n
            p11 = (r1 * math.cos(a1), r1 * math.sin(a1), z1)
            p12 = (r1 * math.cos(a2), r1 * math.sin(a2), z1)
            p21 = (r2 * math.cos(a1), r2 * math.sin(a1), z2)
            p22 = (r2 * math.cos(a2), r2 * math.sin(a2), z2)
            for t in ((p11, p21, p22), (p11, p22, p12)):
                if len({tuple(round(c, 9) for c in q) for q in t}) == 3:
                    m.dreiecke.append(t)
                    m.flaeche.append(-1)
    return m


def speichere_stl(m: Modell, pfad: str) -> None:
    """Binäre STL schreiben (Normalen 0 – die rechnet jedes Programm selbst)."""
    with open(pfad, "wb") as fh:
        fh.write(b"\0" * 80)
        fh.write(struct.pack("<I", len(m.dreiecke)))
        for t in m.dreiecke:
            fh.write(struct.pack("<12f", 0.0, 0.0, 0.0, *t[0], *t[1], *t[2]))
            fh.write(b"\0\0")


def lade_stl(pfad: str) -> Modell:
    with open(pfad, "rb") as fh:
        data = fh.read()
    m = Modell()
    if len(data) >= 84:
        n = struct.unpack("<I", data[80:84])[0]
        if 84 + 50 * n == len(data):  # binär
            for i in range(n):
                o = 84 + 50 * i + 12
                v = struct.unpack("<9f", data[o:o + 36])
                m.dreiecke.append(((v[0], v[1], v[2]), (v[3], v[4], v[5]), (v[6], v[7], v[8])))
            m.flaeche = [-1] * len(m.dreiecke)
            return m
    text = data.decode("latin-1", "replace")
    pts: List[Vec] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 4 and parts[0].lower() == "vertex":
            try:
                pts.append((float(parts[1]), float(parts[2]), float(parts[3])))
            except ValueError:
                raise CADFehler("STL-Datei ist beschädigt (Zeile „{}“).".format(line.strip()[:40]))
            if len(pts) == 3:
                m.dreiecke.append((pts[0], pts[1], pts[2]))
                pts = []
    if not m.dreiecke:
        raise CADFehler("Keine Dreiecke in der STL-Datei gefunden.")
    m.flaeche = [-1] * len(m.dreiecke)
    return m


def lade_step(pfad: str, toleranz: float = 0.01) -> Modell:
    try:
        import cascadio
    except Exception as exc:
        raise CADFehler("STEP-Dateien liest nur die PC-Version (Windows 10/11). Bitte das Modell im CAD als STL "
                        "speichern oder die PC-Version benutzen. ({})".format(exc))
    try:
        with open(pfad, "rb") as fh:
            roh = fh.read()
    except OSError as exc:
        raise CADFehler("Die Datei konnte nicht geöffnet werden: {}".format(exc))
    if hasattr(cascadio, "load"):  # im Speicher – auch bei Umlauten im Pfad
        try:
            data = cascadio.load(roh, "step", tol_linear=toleranz, tol_angular=0.5, include_brep=True)
        except Exception as exc:
            raise CADFehler("Die STEP-Datei konnte nicht gelesen werden: {}".format(exc))
        if not data or data[:4] != b"glTF":
            raise CADFehler("In der STEP-Datei wurde kein lesbares Modell gefunden.")
        return lade_glb(data)
    fd, tmp = tempfile.mkstemp(suffix=".glb")
    os.close(fd)
    try:
        try:
            cascadio.step_to_glb(pfad, tmp, tol_linear=toleranz, tol_angular=0.5, include_brep=True)
        except Exception as exc:
            raise CADFehler("Die STEP-Datei konnte nicht gelesen werden: {}".format(exc))
        with open(tmp, "rb") as fh:
            data = fh.read()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return lade_glb(data)


_KOMP = {5126: ("f", 4), 5125: ("I", 4), 5123: ("H", 2), 5121: ("B", 1)}
_ANZ = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def lade_glb(data: bytes) -> Modell:
    """Binäres glTF (wie von cascadio): Dreiecke in mm (glTF rechnet in Metern), Knoten-Transformationen,
    dazu die genauen Flächen aus der Erweiterung TM_brep_faces."""
    if len(data) < 20 or data[:4] != b"glTF":
        raise CADFehler("Keine gültige GLB-Datei.")
    off = 12
    js: Dict[str, Any] = {}
    binary = b""
    while off + 8 <= len(data):
        clen, ctype = struct.unpack("<II", data[off:off + 8])
        chunk = data[off + 8:off + 8 + clen]
        if ctype == 0x4E4F534A:
            js = json.loads(chunk.decode("utf-8"))
        elif ctype == 0x004E4942:
            binary = chunk
        off += 8 + clen

    def accessor(i: int) -> List[Tuple]:
        acc = js["accessors"][i]
        bv = js["bufferViews"][acc["bufferView"]]
        fmt_c, size = _KOMP[acc["componentType"]]
        n = _ANZ[acc["type"]]
        stride = bv.get("byteStride") or size * n
        base = bv.get("byteOffset", 0) + acc.get("byteOffset", 0)
        out = []
        st = struct.Struct("<" + fmt_c * n)
        for k in range(acc["count"]):
            out.append(st.unpack_from(binary, base + k * stride))
        return out

    def mat_mul(a: List[float], b: List[float]) -> List[float]:  # Spaltenweise 4x4 wie glTF
        return [sum(a[r + 4 * k] * b[k + 4 * c] for k in range(4)) for c in range(4) for r in range(4)]

    def node_matrix(node: Dict[str, Any]) -> List[float]:
        if "matrix" in node:
            return list(node["matrix"])
        tx, ty, tz = node.get("translation", [0, 0, 0])
        qx, qy, qz, qw = node.get("rotation", [0, 0, 0, 1])
        sx, sy, sz = node.get("scale", [1, 1, 1])
        rot = [1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy + qz * qw), 2 * (qx * qz - qy * qw), 0,
               2 * (qx * qy - qz * qw), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz + qx * qw), 0,
               2 * (qx * qz + qy * qw), 2 * (qy * qz - qx * qw), 1 - 2 * (qx * qx + qy * qy), 0,
               0, 0, 0, 1]
        sc = [sx, 0, 0, 0, 0, sy, 0, 0, 0, 0, sz, 0, 0, 0, 0, 1]
        tr = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, tx, ty, tz, 1]
        return mat_mul(tr, mat_mul(rot, sc))

    ident = [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]
    instanzen: List[Tuple[int, List[float]]] = []

    def walk(i: int, parent: List[float]) -> None:
        node = js["nodes"][i]
        mtx = mat_mul(parent, node_matrix(node))
        if "mesh" in node:
            instanzen.append((node["mesh"], mtx))
        for ch in node.get("children", []):
            walk(ch, mtx)

    scene = js.get("scenes", [{}])[js.get("scene", 0)] if js.get("scenes") else {}
    roots = scene.get("nodes") or list(range(len(js.get("nodes", []))))
    for r in roots:
        walk(r, ident)
    if not instanzen:
        instanzen = [(i, ident) for i in range(len(js.get("meshes", [])))]

    m = Modell()
    teile: List[Tuple[List[Tuple[Vec, Vec, Vec]], List[int], List[Dict[str, Any]]]] = []
    for mesh_i, mtx in instanzen:
        def tf(p: Sequence[float], mtx=mtx) -> Vec:
            x, y, z = p[0], p[1], p[2]
            return (1000.0 * (mtx[0] * x + mtx[4] * y + mtx[8] * z + mtx[12]),
                    1000.0 * (mtx[1] * x + mtx[5] * y + mtx[9] * z + mtx[13]),
                    1000.0 * (mtx[2] * x + mtx[6] * y + mtx[10] * z + mtx[14]))

        def tf_dir(d: Sequence[float], mtx=mtx) -> Vec:
            return _unit((mtx[0] * d[0] + mtx[4] * d[1] + mtx[8] * d[2],
                          mtx[1] * d[0] + mtx[5] * d[1] + mtx[9] * d[2],
                          mtx[2] * d[0] + mtx[6] * d[1] + mtx[10] * d[2]))

        for prim in js["meshes"][mesh_i].get("primitives", []):
            if prim.get("mode", 4) != 4:
                continue
            pos = [tf(p) for p in accessor(prim["attributes"]["POSITION"])]
            if "indices" in prim:
                idx = [t[0] for t in accessor(prim["indices"])]
            else:
                idx = list(range(len(pos)))
            tris = [(pos[idx[k]], pos[idx[k + 1]], pos[idx[k + 2]]) for k in range(0, len(idx) - 2, 3)]
            ext = (prim.get("extensions") or {}).get("TM_brep_faces")
            faces: List[Dict[str, Any]] = []
            fidx = [-1] * len(tris)
            if ext:
                fi = [t[0] for t in accessor(ext["faceIndices"])]
                if len(fi) == len(tris):
                    nummer = {}
                    for f in ext.get("faces", []):
                        g = _flaeche_mm(f, tf, tf_dir)
                        if g is not None:
                            nummer[f.get("face_index")] = len(faces)
                            faces.append(g)
                    fidx = [nummer.get(v, -1) for v in fi]
            teile.append((tris, fidx, faces))
    if not teile:
        raise CADFehler("Im Modell sind keine Dreiecke.")
    groesste = max(len(t[0]) for t in teile)
    for tris, fidx, faces in teile:
        if len(tris) < 0.02 * groesste:
            continue  # Splitter (z. B. einzelne Flächen neben dem Körper)
        base = len(m.flaechen)
        m.flaechen.extend(faces)
        m.dreiecke.extend(tris)
        m.flaeche.extend([f + base if f >= 0 else -1 for f in fidx])
    return m


def _flaeche_mm(f: Dict[str, Any], tf, tf_dir) -> Optional[Dict[str, Any]]:
    typ = f.get("type")
    try:
        if typ == "plane":
            return {"typ": "ebene", "punkt": tf(f["origin"]), "normale": tf_dir(f["normal"])}
        if typ == "cylinder":
            return {"typ": "zylinder", "punkt": tf(f["origin"]), "achse": tf_dir(f["axis"]),
                    "radius": 1000.0 * f["radius"]}
        if typ == "cone":
            return {"typ": "kegel", "punkt": tf(f["apex"]), "achse": tf_dir(f["axis"]),
                    "winkel": float(f["semi_angle"])}
        if typ == "torus":
            return {"typ": "torus", "punkt": tf(f["center"]), "achse": tf_dir(f["axis"]),
                    "R": 1000.0 * f["major_radius"], "r": 1000.0 * f["minor_radius"]}
    except (KeyError, TypeError, ValueError):
        return None
    return None


# --------------------------------------------------------------------------
# Drehachse und Lage des Teils
# --------------------------------------------------------------------------


@dataclass
class Lage:
    punkt: Vec  # Punkt auf der Drehachse
    z: Vec  # +Z des Teils (aus der Stirnseite heraus)
    e1: Vec  # Richtung C0 (Stirnansicht oben)
    z0: float  # Nullpunkt: Wert von (P − punkt)·z an der Stirnseite
    quelle: str = ""

    @property
    def e2(self) -> Vec:
        return _cross(self.z, self.e1)

    def koord(self, p: Vec) -> Tuple[float, float, float]:
        """(z, u, v) im Teil: z entlang der Achse ab Nullpunkt, u/v quer (u = C0, v = C90)."""
        d = _sub(p, self.punkt)
        return _dot(d, self.z) - self.z0, _dot(d, self.e1), _dot(d, self.e2)

    def winkel(self, richtung: Vec) -> float:
        """C-Winkel (Grad, 0…360) einer Richtung quer zur Achse."""
        a = math.degrees(math.atan2(_dot(richtung, self.e2), _dot(richtung, self.e1)))
        return round(a % 360.0, 3) % 360.0


def _quer(z: Vec) -> Vec:
    """Eine Richtung senkrecht zu z (bevorzugt Modell-Y, sonst Modell-Z)."""
    for ref in ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0)):
        q = _sub(ref, _scale(z, _dot(ref, z)))
        if _norm(q) > 0.3:
            return _unit(q)
    return (1.0, 0.0, 0.0)


def achsen_kandidaten(m: Modell) -> List[Tuple[str, Vec, Vec]]:
    """Mögliche Drehachsen (Name, Punkt, Richtung) – die beste zuerst."""
    lo, hi = m.grenzen
    mitte = _scale(_add(lo, hi), 0.5)
    out: List[Tuple[str, Vec, Vec, float]] = []
    # genaue Achsen aus Zylindern/Kegeln/Tori (STEP), gewichtet mit der Zahl ihrer Dreiecke
    gewicht = [0] * len(m.flaechen)
    for f in m.flaeche:
        if f >= 0:
            gewicht[f] += 1
    gruppen: List[List[Any]] = []
    for i, f in enumerate(m.flaechen):
        if f["typ"] not in ("zylinder", "kegel", "torus"):
            continue
        d = f["achse"]
        if d[0] < -1e-9 or (abs(d[0]) < 1e-9 and (d[1] < -1e-9 or (abs(d[1]) < 1e-9 and d[2] < 0))):
            d = _scale(d, -1.0)
        p = f["punkt"]
        p = _sub(p, _scale(d, _dot(p, d)))  # Fußpunkt der Achse (nächster Punkt zum Ursprung)
        for g in gruppen:
            if _norm(_cross(g[1], d)) < 0.01 and _norm(_sub(g[0], p)) < 0.05:
                g[2] += gewicht[i] + 1
                break
        else:
            gruppen.append([p, d, gewicht[i] + 1])
    gruppen.sort(key=lambda g: -g[2])
    for k, (p, d, w) in enumerate(gruppen[:3]):
        name = "genau (aus dem STEP)" if k == 0 else "Achse {}".format(k + 1)
        out.append((name, p, d, 1e9 + w))
    # Modellachsen: rund ist, was quer gleich breit ist
    ext = _sub(hi, lo)
    for i, name in enumerate(("Modell-X", "Modell-Y", "Modell-Z")):
        d = [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)][i]
        a, b = [ext[j] for j in range(3) if j != i]
        score = -abs(a - b) / max(a, b, 1e-9)
        p = list(mitte)
        p[i] = 0.0
        out.append((name, (p[0], p[1], p[2]), d, score))
    out.sort(key=lambda t: -t[3])
    return [(n, p, d) for n, p, d, _s in out]


def lage_standard(m: Modell, punkt: Vec, richtung: Vec, quelle: str = "") -> Lage:
    z = _unit(richtung)
    werte = [_dot(_sub(p, punkt), z) for t in m.dreiecke for p in t]
    lage = Lage(punkt, z, _quer(z), max(werte), quelle)
    return lage


def wenden(m: Modell, lage: Lage) -> Lage:
    """Teil umdrehen: die andere Stirnseite wird Z0."""
    z = _scale(lage.z, -1.0)
    werte = [_dot(_sub(p, lage.punkt), z) for t in m.dreiecke for p in t]
    return Lage(lage.punkt, z, lage.e1, max(werte), lage.quelle)


# --------------------------------------------------------------------------
# Profil
# --------------------------------------------------------------------------


@dataclass
class Profil:
    dz: float
    z: List[float]  # Mitte je Schnitt, von vorn (0) nach hinten
    aussen: List[float]  # größter Radius
    innen: List[float]  # Bohrungsradius (0 = Mitte voll)
    z_vorn: float
    z_hinten: float
    plan: List[float]  # Z von Planflächen (quer zur Achse)


def profil(m: Modell, lage: Lage, dz: Optional[float] = None) -> Profil:
    pts = []
    for t in m.dreiecke:
        pts.append(tuple(lage.koord(p) for p in t))
    zs = [p[0] for t in pts for p in t]
    z_vorn, z_hinten = max(zs), min(zs)
    laenge = max(1e-6, z_vorn - z_hinten)
    if dz is None:
        dz = max(0.02, laenge / 2500.0)
    n = max(2, int(round(laenge / dz)))
    dz = laenge / n  # Schnitte genau zwischen Stirnseite und Ende
    # Schnitte knapp neben der Mitte: genau auf runden Z-Werten liegen oft Eckpunkte des Netzes, dort würde
    # ein Schnitt Kanten verfehlen
    zc = [z_vorn - (k + 0.5 + 0.0123) * dz for k in range(n)]
    rmax = [0.0] * n
    dmin = [float("inf")] * n
    kreuz = [0] * n
    rx, ry = math.cos(0.1234567), math.sin(0.1234567)  # Prüfstrahl für „Mitte im Material?“
    plan: Dict[float, float] = {}
    for tri in pts:
        (za, ua, va), (zb, ub, vb), (zc_, uc, vc) = tri
        lo, hi = min(za, zb, zc_), max(za, zb, zc_)
        if hi - lo < 1e-7:  # Dreieck quer zur Achse: Planfläche
            key = round((za + zb + zc_) / 3, 3)
            area = 0.5 * abs((ub - ua) * (vc - va) - (uc - ua) * (vb - va))
            plan[key] = plan.get(key, 0.0) + area
            continue
        k0 = max(0, int(math.ceil((z_vorn - hi) / dz - 0.5)))
        k1 = min(n - 1, int(math.floor((z_vorn - lo) / dz - 0.5)))
        edges = ((za, ua, va, zb, ub, vb), (zb, ub, vb, zc_, uc, vc), (zc_, uc, vc, za, ua, va))
        for k in range(k0, k1 + 1):
            z = zc[k]
            seg = []
            for z1, u1, v1, z2, u2, v2 in edges:
                if (z1 - z) * (z2 - z) < 0:
                    s = (z - z1) / (z2 - z1)
                    seg.append((u1 + (u2 - u1) * s, v1 + (v2 - v1) * s))
            if len(seg) != 2:
                continue
            (pu, pv), (qu, qv) = seg
            r1, r2 = math.hypot(pu, pv), math.hypot(qu, qv)
            if r1 > rmax[k]:
                rmax[k] = r1
            if r2 > rmax[k]:
                rmax[k] = r2
            du, dv = qu - pu, qv - pv
            # Bohrung: nächste Strecke zur Achse, gemessen an ihren Endpunkten (die liegen auf der echten
            # Fläche – die Mitte einer Sehne läge um die Facettierung zu weit innen)
            d = r1 if r1 > r2 else r2
            if d < dmin[k]:
                dmin[k] = d
            den = rx * dv - ry * du  # Schnitt Strahl t·r mit Strecke p + s·(q − p)
            if abs(den) > 1e-15:
                tr = (pu * dv - pv * du) / den
                ss = (pu * ry - pv * rx) / den
                if tr > 0 and 0 <= ss < 1:
                    kreuz[k] += 1
    innen = [0.0 if kreuz[k] % 2 == 1 or dmin[k] == float("inf") else dmin[k] for k in range(n)]
    # einzelne Ausreißer (Strahl genau durch eine Ecke, Lücke im Netz) mit den Nachbarn füllen
    for k in range(1, n - 1):
        if innen[k] == 0.0 and innen[k - 1] > 0 and innen[k + 1] > 0:
            innen[k] = min(innen[k - 1], innen[k + 1])
        lo_n = min(rmax[k - 1], rmax[k + 1])
        if rmax[k] < lo_n - 0.02:
            rmax[k] = lo_n
    plan_z = sorted((z for z, a in plan.items() if a > 0.05), reverse=True)
    return Profil(dz, zc, rmax, innen, z_vorn, z_hinten, plan_z)


# --------------------------------------------------------------------------
# Profil -> Konturpunkte
# --------------------------------------------------------------------------


def _rdp(pts: List[Tuple[float, float]], eps: float) -> List[Tuple[float, float]]:
    if len(pts) < 3:
        return list(pts)
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        (za, ra), (zb, rb) = pts[a], pts[b]
        dz, dr = zb - za, rb - ra
        ln = math.hypot(dz, dr)
        best, bi = -1.0, -1
        for i in range(a + 1, b):
            z, r = pts[i]
            d = abs(dr * (z - za) - dz * (r - ra)) / ln if ln > 1e-12 else math.hypot(z - za, r - ra)
            if d > best:
                best, bi = d, i
        if bi >= 0 and best > eps:
            keep[bi] = True
            stack.append((a, bi))
            stack.append((bi, b))
    return [p for p, k in zip(pts, keep) if k]


def _snap(z: float, plan: Sequence[float], tol: float) -> float:
    best = None
    for p in plan:
        if abs(p - z) <= tol and (best is None or abs(p - z) < abs(best - z)):
            best = p
    return best if best is not None else z


def _polylinie(zs: Sequence[float], rs: Sequence[float], dz: float, plan: Sequence[float],
               z_start: float, z_ende: float, eps: float = 0.0) -> List[Tuple[float, float]]:
    """Abgetastete Kurve r(z) als Linienzug. Absätze werden senkrecht an den Planflächen des Modells
    eingesetzt; an Stirnseite, Ende und Absätzen wird der Radius bis an die Fläche verlängert."""
    n = len(zs)

    def extra(k_a: int, k_b: int, z: float) -> float:
        """Radius bei z aus den Schnitten k_a, k_b (linear), nicht über die Nachbarwerte hinaus."""
        if not (0 <= k_a < n and 0 <= k_b < n) or abs(zs[k_b] - zs[k_a]) < 1e-12:
            return rs[max(0, min(n - 1, k_a))]
        r = rs[k_a] + (rs[k_b] - rs[k_a]) * (z - zs[k_a]) / (zs[k_b] - zs[k_a])
        lo, hi = min(rs[k_a], rs[k_b]), max(rs[k_a], rs[k_b])
        spanne = hi - lo
        return max(lo - spanne, min(hi + spanne, r))

    out = [(z_start, extra(0, 1, z_start))]
    for k in range(n - 1):
        r0, r1 = rs[k], rs[k + 1]
        ebene = [p for p in plan if zs[k + 1] - 1e-9 <= p <= zs[k] + 1e-9]
        sprung = abs(r1 - r0) > 0.01
        if sprung and (ebene or abs(r1 - r0) > max(0.3, 10 * dz)):
            zm = ebene[0] if ebene else (zs[k] + zs[k + 1]) / 2
            out.append((zm, extra(k, k - 1, zm) if k > 0 else r0))
            out.append((zm, extra(k + 1, k + 2, zm) if k + 2 < n else r1))
        else:
            out.append((zs[k + 1], r1))
    out.append((z_ende, extra(n - 1, n - 2, z_ende)))
    return _rdp(out, eps or max(0.01, 0.5 * dz))


def _art(a: Tuple[float, float], b: Tuple[float, float]) -> str:
    dz, dr = b[0] - a[0], b[1] - a[1]
    ln = math.hypot(dz, dr)
    if ln < 1e-9:
        return "0"
    if abs(dr) / ln < 0.003:
        return "H"
    if abs(dz) / ln < 0.003:
        return "V"
    return "S"


def _schnitt(p1, d1, p2, d2) -> Optional[Tuple[float, float]]:
    den = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(den) < 1e-12:
        return None
    t = ((p2[0] - p1[0]) * d2[1] - (p2[1] - p1[1]) * d2[0]) / den
    return p1[0] + d1[0] * t, p1[1] + d1[1] * t


def _ecken(poly: List[Tuple[float, float]]) -> List[Tuple[float, float, str, float]]:
    """Linienzug -> Eckpunkte (z, r, Ecke, Wert): eine kurze Schräge zwischen zwei Elementen wird zur Fase
    (gleich lange Schenkel), mehrere kurze Stücke (Bogen) zum Radius; sonst bleiben die Punkte (Kegel)."""
    pts: List[Tuple[float, float]] = []
    for p in poly:
        if not pts or math.hypot(p[0] - pts[-1][0], p[1] - pts[-1][1]) > 1e-6:
            pts.append(p)
    segs = [(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    if not segs:
        return [(p[0], p[1], "", 0.0) for p in pts]

    def lang(sg) -> bool:
        a, b = sg
        return _art(a, b) in ("H", "V") or math.hypot(b[0] - a[0], b[1] - a[1]) > 4.0

    out: List[Tuple[float, float, str, float]] = [(pts[0][0], pts[0][1], "", 0.0)]
    i = 0
    while i < len(segs):
        j = i + 1
        while j < len(segs) and not lang(segs[j]):
            j += 1
        if j >= len(segs):
            for sg in segs[i:]:
                out.append((sg[1][0], sg[1][1], "", 0.0))
            break
        if j == i + 1:
            out.append((segs[i][1][0], segs[i][1][1], "", 0.0))
            i = j
            continue
        # mehrere fast gerade Stücke hintereinander sind eine Schräge (Abtastung an den Knicken)
        rich = [math.atan2(sg[1][1] - sg[0][1], sg[1][0] - sg[0][0]) for sg in segs[i + 1:j]]
        gerade = max(rich) - min(rich) < math.radians(6) if rich else False
        vorher, nachher = segs[i], segs[j]
        d1 = (vorher[1][0] - vorher[0][0], vorher[1][1] - vorher[0][1])
        d2 = (nachher[1][0] - nachher[0][0], nachher[1][1] - nachher[0][1])
        ecke = _schnitt(vorher[0], d1, nachher[0], d2)
        start, ende = segs[i + 1][0], segs[j - 1][1]
        erkannt = False
        if ecke is not None:
            l1 = math.hypot(ecke[0] - start[0], ecke[1] - start[1])
            l2 = math.hypot(ecke[0] - ende[0], ecke[1] - ende[1])
            cosw = (d1[0] * d2[0] + d1[1] * d2[1]) / (math.hypot(*d1) * math.hypot(*d2))
            theta = math.acos(max(-1.0, min(1.0, cosw)))
            if theta > math.radians(10):
                if (j == i + 2 or gerade) and abs(l1 - l2) <= 0.03 + 0.03 * max(l1, l2):
                    out.append((ecke[0], ecke[1], "fase", round((l1 + l2) / 2, 2)))
                    erkannt = True
                elif j > i + 2 and not gerade and abs(l1 - l2) <= 0.05 + 0.2 * max(l1, l2):
                    # Bogen: die abgetasteten Enden liegen etwas innerhalb – der längere Schenkel zählt
                    radius = max(l1, l2) / math.tan(theta / 2)
                    out.append((ecke[0], ecke[1], "radius", round(radius * 20) / 20.0))
                    erkannt = True
        if not erkannt:
            for sg in segs[i:j]:
                out.append((sg[1][0], sg[1][1], "", 0.0))
        i = j
    clean: List[Tuple[float, float, str, float]] = []
    for p in out:
        if clean and abs(clean[-1][0] - p[0]) < 1e-6 and abs(clean[-1][1] - p[1]) < 1e-6:
            if p[2]:
                clean[-1] = p
            continue
        clean.append(p)
    return clean


def _r3(v: float) -> float:
    return round(v + 0.0, 3) + 0.0


def _eckwert(v: float) -> float:
    """Fase/Radius: nahe an einem üblichen Maß (Raster 0,05) dorthin, sonst auf 0,01."""
    if v <= 0:
        return 0.0
    r = round(v * 20) / 20.0
    return round(r if abs(v - r) < 0.02 else v, 2) + 0.0


# --------------------------------------------------------------------------
# Erkennung
# --------------------------------------------------------------------------


@dataclass
class Erkennung:
    teil: Teil
    profil: Profil
    hinweise: List[str] = field(default_factory=list)
    merkmale: List[str] = field(default_factory=list)  # Beschreibung für die Liste im Fenster
    seite2: Optional[float] = None  # ab hier Rückseite (2. Aufspannung)


def teil_aus_modell(m: Modell, lage: Lage, basis: Optional[Teil] = None, prof: Optional[Profil] = None
                    ) -> Erkennung:
    """Drehteil aus dem Modell: Kontur außen, Bohrung/Innenkontur, Schlüsselflächen, Bohrungen."""
    import copy as _copy

    from .kontur import neues_teil
    from . import werkzeugkatalog as wk

    t = _copy.deepcopy(basis) if basis is not None else neues_teil()
    prof = prof or profil(m, lage)
    erk = Erkennung(t, prof)
    t.name = m.name.upper()[:24] or t.name
    t.fraesungen, t.innen, t.bohr_d, t.bohr_tiefe = [], [], 0.0, 0.0
    zs, ra = prof.z, prof.aussen
    laenge = prof.z_vorn - prof.z_hinten
    r_glob = max(ra)
    # Seite 1: von vorn bis zum Ende des größten Durchmessers
    k_max = max(k for k in range(len(ra)) if ra[k] >= r_glob - 0.01)
    run, mx = [], 0.0
    einstiche = []
    for k in range(k_max + 1):
        mx = max(mx, ra[k])
        run.append(mx)
        if ra[k] < mx - 0.02:
            einstiche.append(zs[k])
    if einstiche:
        erk.hinweise.append("Einstich bei Z{} … Z{} ist im Programm nicht enthalten (Einstiche gehen noch "
                            "nicht) – dort wird nur auf den größeren Durchmesser gedreht.".format(
                                fmt(round(max(einstiche), 2)), fmt(round(min(einstiche), 2))))
    z_ende = prof.z_hinten - prof.z_vorn if k_max == len(ra) - 1 else _snap(
        zs[k_max] - prof.dz / 2, [p - prof.z_vorn for p in prof.plan], 1.5 * prof.dz)
    plan_rel = [p - prof.z_vorn for p in prof.plan]
    if k_max < len(ra) - 1:
        erk.seite2 = z_ende
        erk.hinweise.append("Hinter Z{} wird das Teil wieder dünner – das ist die Rückseite (2. Aufspannung): "
                            "Teil wenden und Z0 auf die andere Stirnseite legen.".format(fmt(round(z_ende, 2))))
    zs_rel = [z - prof.z_vorn for z in zs[:k_max + 1]]
    poly = _polylinie(zs_rel, run, prof.dz, plan_rel, 0.0, z_ende)
    poly = [(0.0, 0.0)] + poly  # Stirnseite von der Mitte
    ecken = _ecken(poly)
    punkte = [KPunkt(0.0, 0.0)]
    for z, r, ecke, wert in ecken[1:]:
        punkte.append(KPunkt(_r3(2 * r), _r3(z), ecke, _eckwert(wert)))
    t.punkte = punkte
    t.rohteil_d = float(math.ceil(2 * r_glob + 2.0 - 1e-6))
    t.rohteil_l = float(math.ceil(laenge + max(0.0, t.planaufmass) + 3.0 - 1e-6))
    erk.merkmale.append("Kontur außen: {} Punkte, Ø{} max., Länge {}".format(
        len(punkte), fmt(round(2 * r_glob, 3)), fmt(round(-z_ende, 2))))
    erk.merkmale.append("Rohteil Ø{} × {} (bitte prüfen)".format(fmt(t.rohteil_d), fmt(t.rohteil_l)))

    # Bohrung von vorn
    ri = prof.innen
    if ri and ri[0] > 0.2:
        k_b = 0
        while k_b + 1 < len(ri) and ri[k_b + 1] > 0.05:
            k_b += 1
        durch = k_b == len(ri) - 1
        zb = [z - prof.z_vorn for z in zs[:k_b + 1]]
        rb = ri[:k_b + 1]
        stl = not m.flaechen
        if stl:  # facettierte Bohrung: Ausreißer der Abtastung glätten (gleitender Median)
            rb = [sorted(rb[max(0, k - 3):k + 4])[len(rb[max(0, k - 3):k + 4]) // 2] for k in range(len(rb))]
        # Bohrerspitze am Grund: vom Ende rückwärts bis hinter die Kegellänge (≈ 0,6·r bei 118°) gehen;
        # der größte Radius dort ist der Bohrungsradius, die Spitze beginnt, wo er unterschritten wird
        tol_r = 0.05 if stl else 0.01
        m_r, k = 0.0, k_b
        while k >= 0:
            m_r = max(m_r, rb[k])
            if (k_b - k) * prof.dz > 0.75 * m_r + 2 * prof.dz:
                break
            k -= 1
        k_spitze = k_b
        while k_spitze > 0 and rb[k_spitze] < m_r - tol_r:
            k_spitze -= 1
        r_klein = min(rb[:k_spitze + 1])
        tiefe = -zb[k_spitze] + prof.dz / 2 if not durch else laenge + 2.0
        # Spitze: Flanke als Gerade z(r) ausgleichen, Bohrtiefe dort, wo sie den Bohrungsradius erreicht
        kegel = [(zb[k], rb[k]) for k in range(k_spitze + 1, k_b + 1) if 0.2 * m_r < rb[k] < 0.9 * m_r]
        if not durch and len(kegel) >= 3:
            n_k = len(kegel)
            mz = sum(z for z, _r in kegel) / n_k
            mr = sum(r for _z, r in kegel) / n_k
            srr = sum((r - mr) ** 2 for _z, r in kegel)
            if srr > 1e-12:
                b = sum((r - mr) * (z - mz) for z, r in kegel) / srr
                t_fit = -(mz + b * (m_r - mr))
                if abs(t_fit - tiefe) < 3 * prof.dz + 0.05:
                    tiefe = t_fit
                    while k_spitze > 0 and zb[k_spitze] < -tiefe:
                        k_spitze -= 1  # Schnitte schon in der Spitze gehören nicht zur Bohrung
                    r_klein = min(rb[:k_spitze + 1])
        bohrer = sorted({w.dm for w in wk.RUNDWERKZEUGE if w.art == "bohrer"})
        d_soll = 2 * r_klein
        passt = [d for d in bohrer if abs(d - d_soll) < (0.06 if stl else 0.02)]
        d_bohr = passt[0] if passt else max([d for d in bohrer if d <= d_soll - 0.4] or [bohrer[0]])
        t.bohr_d, t.bohr_tiefe = d_bohr, round(tiefe, 2)
        drill = t.tool(t.wz_bohren)
        if drill is not None:
            code = next((w.code for w in wk.RUNDWERKZEUGE if w.art == "bohrer" and abs(w.dm - d_bohr) < 1e-6), "")
            drill.dm, drill.halter, drill.name = d_bohr, code or drill.halter, "BOHRER D{}".format(fmt(d_bohr))
        # Innenkontur, wenn mehr als nur gebohrt wird
        poly_i = _polylinie(zb[:k_spitze + 1], rb[:k_spitze + 1], prof.dz, plan_rel, 0.0,
                            zb[k_spitze] - prof.dz / 2 if not durch else -laenge, 0.05 if stl else 0.0)
        tol_d = 0.06 if stl else 0.02
        einfach = all(abs(r - r_klein) < tol_d for _z, r in poly_i) and abs(d_bohr - d_soll) < tol_d
        if not einfach:
            ecken_i = _ecken([(0.0, poly_i[0][1] + 10.0)] + poly_i)
            innen = []
            for z, r, ecke, wert in ecken_i[1:]:
                innen.append(KPunkt(_r3(2 * r), _r3(z), ecke, _eckwert(wert)))
            if innen and abs(innen[0].z) > 1e-6:
                innen.insert(0, KPunkt(innen[0].x, 0.0))
            if innen and innen[-1].x > d_bohr + 0.01:
                innen.append(KPunkt(d_bohr, innen[-1].z))  # Grund bis auf die Vorbohrung
            t.innen = innen
        erk.merkmale.append("Bohrung Ø{} {} – Vorbohren Ø{}{}".format(
            fmt(round(d_soll, 3)), "durch" if durch else "× {}".format(fmt(round(tiefe, 2))), fmt(d_bohr),
            ", Innenkontur {} Punkte".format(len(t.innen)) if t.innen else ""))
    elif any(r > 0.2 for r in ri):
        erk.hinweise.append("Bohrung von der Rückseite – gehört zur 2. Aufspannung.")

    _flaechen(m, lage, prof, t, erk)
    _bohrungen(m, lage, prof, t, erk)
    return erk


def _r_bei(prof: Profil, z_rel: float) -> float:
    k = int((-z_rel) / prof.dz)
    k = max(0, min(len(prof.aussen) - 1, k))
    return prof.aussen[k]


def _werkzeug(t: Teil, arten: Sequence[str], lage: str, dm: float = 0.0) -> int:
    kand = [w for w in sorted(t.werkzeuge, key=lambda w: w.t) if w.art in arten and w.lage in ("", lage)]
    for w in kand:
        if dm and abs(w.dm - dm) < 0.01:
            return w.t
    return kand[0].t if kand else 0


def _flaechen(m: Modell, lage: Lage, prof: Profil, t: Teil, erk: Erkennung) -> None:
    """Ebenen parallel zur Achse, die tiefer als der Drehdurchmesser liegen: Schlüsselflächen."""
    gruppen: Dict[Tuple[float, float], List[float]] = {}  # (Winkel, Abstand) -> [z min, z max, Fläche, ...]
    for tri, fi in zip(m.dreiecke, m.flaeche):
        a, b, c = tri
        n = _cross(_sub(b, a), _sub(c, a))
        area = 0.5 * _norm(n)
        if area < 1e-9:
            continue
        n = _unit(n)
        if abs(_dot(n, lage.z)) > 0.01:
            continue
        if fi >= 0 and m.flaechen[fi]["typ"] != "ebene":
            continue  # Zylinderfläche (STEP): keine Ebene
        h = _dot(_sub(a, lage.punkt), n)
        if h <= 0.05:
            continue
        zs = [lage.koord(p)[0] - (prof.z_vorn) for p in tri]
        z_mid = sum(zs) / 3
        if h > _r_bei(prof, z_mid) - max(0.2, 0.02 * _r_bei(prof, z_mid)):
            continue  # Facette des Zylinders, keine Fläche
        key = (round(lage.winkel(n) * 2) / 2.0, round(h, 2))
        quer = _cross(lage.z, n)
        breite = [_dot(_sub(p, lage.punkt), quer) for p in tri]
        aussen = max(math.hypot(*lage.koord(p)[1:]) for p in tri)
        g = gruppen.setdefault(key, [min(zs), max(zs), 0.0, min(breite), max(breite), 0.0, 0])
        g[0], g[1], g[2] = min(g[0], min(zs)), max(g[1], max(zs)), g[2] + area
        g[3], g[4], g[5], g[6] = min(g[3], min(breite)), max(g[4], max(breite)), max(g[5], aussen), g[6] + 1
    fl: Dict[Tuple[float, float, float], List[float]] = {}
    for (w, h), (z0, z1, area, b0, b1, aussen, anzahl) in gruppen.items():
        r_ort = _r_bei(prof, (z0 + z1) / 2)
        if area < 2.0 or b1 - b0 < 1.0:
            continue
        if aussen < r_ort - 0.15:
            continue  # liegt innen (z. B. Grund einer Bohrung), keine Fläche am Umfang
        if not m.flaechen and (anzahl < 2 or area < 5.0):
            continue  # STL: einzelne Facetten sind keine Fläche
        key = (h, round(z1, 2), round(z0, 2))
        fl.setdefault(key, []).append(w)
    nr = 0
    for (h, z1, z0), winkel in sorted(fl.items(), key=lambda kv: (-kv[0][1], kv[0][0])):
        z1 = 0.0 if z1 > -0.05 else z1
        winkel = sorted(set(winkel))
        fr = Fraesung("flaeche", winkel, _r3(z1), _r3(z0), _r3(2 * h), 0.0, 0.0,
                      _werkzeug(t, ("fraeser",), "radial"), False, _werkzeug(t, ("fase",), "radial"), 0.3)
        t.fraesungen.append(fr)
        nr += 1
        erk.merkmale.append("Fläche SW{} bei C {}°, Z{} … Z{}".format(
            fmt(2 * h), "/".join(fmt(w) for w in winkel), fmt(z1), fmt(z0)))
    if not m.flaechen:
        erk.hinweise.append("Aus STL-Dateien übernimmt das Programm Kontur, Bohrung und Schlüsselflächen – "
                            "Querbohrungen und Stirnbohrungen nur aus STEP-Dateien.")


def _bohrungen(m: Modell, lage: Lage, prof: Profil, t: Teil, erk: Erkennung) -> None:
    """Zylinder quer zur Achse: Bohrung radial; parallel, aber außermittig: Bohrung an der Stirnseite."""
    if not m.flaechen:
        return
    pts_je: Dict[int, List[Vec]] = {}
    for tri, fi in zip(m.dreiecke, m.flaeche):
        if fi >= 0:
            pts_je.setdefault(fi, []).extend(tri)
    radial: Dict[Tuple[float, float, float], List[float]] = {}
    axial: Dict[Tuple[float, float, float, float], List[float]] = {}
    for fi, f in enumerate(m.flaechen):
        if f["typ"] != "zylinder" or fi not in pts_je:
            continue
        d = f["achse"]
        p = f["punkt"]
        r = f["radius"]
        pts = pts_je[fi]
        quer = abs(_dot(d, lage.z))
        if quer < 0.01:  # Achse quer zur Drehachse: radiale Bohrung
            zk, uk, vk = lage.koord(p)
            z = zk - prof.z_vorn + lage.z0 * 0
            z_rel = lage.koord(p)[0] - prof.z_vorn
            # Richtung zur offenen Seite: dorthin, wo die Punkte der Fläche weiter außen liegen
            proj = [_dot(_sub(q, lage.punkt), d) for q in pts]
            richtung = d if max(proj) > -min(proj) else _scale(d, -1.0)
            tiefst = min(_dot(_sub(q, lage.punkt), richtung) for q in pts)
            ra = _r_bei(prof, z_rel)
            # Kegel (Bohrerspitze) am Grund auf derselben Achse
            spitze = 0.0
            for fj, g in enumerate(m.flaechen):
                if g["typ"] == "kegel" and fj in pts_je and _norm(_cross(g["achse"], d)) < 0.01:
                    werte = [_dot(_sub(q, lage.punkt), richtung) for q in pts_je[fj]]
                    if werte and min(werte) < tiefst and max(werte) <= tiefst + 0.01:
                        spitze = max(spitze, tiefst - min(werte))
            tiefe = ra - tiefst + spitze
            if tiefst < -ra + 0.05:
                tiefe = 2 * ra + 1.0  # durch
            key = (round(z_rel, 2), round(2 * r, 3), round(tiefe, 2))
            radial.setdefault(key, []).append(lage.winkel(richtung))
            del z, zk, uk, vk
        elif _norm(_cross(d, lage.z)) < 0.01:  # parallel zur Drehachse
            zk, uk, vk = lage.koord(p)
            abstand = math.hypot(uk, vk)
            if abstand < 0.05:
                continue  # Bohrung in der Mitte: kommt aus dem Profil
            zz = [lage.koord(q)[0] - prof.z_vorn for q in pts]
            oben, unten = max(zz), min(zz)
            spitze = 0.0
            for fj, g in enumerate(m.flaechen):
                if g["typ"] == "kegel" and fj in pts_je and _norm(_cross(g["achse"], d)) < 0.01:
                    gz = [lage.koord(q)[0] - prof.z_vorn for q in pts_je[fj]]
                    gu = [lage.koord(q)[1:] for q in pts_je[fj]]
                    if gz and min(gz) < unten and max(gz) <= unten + 0.01 and \
                            all(math.hypot(u - uk, v - vk) <= r + 0.05 for u, v in gu):
                        spitze = max(spitze, unten - min(gz))
            key = (round(2 * abstand, 3), round(2 * r, 3), round(oben - unten + spitze, 2), round(oben, 2))
            richtung_u = (uk / abstand, vk / abstand)
            w = math.degrees(math.atan2(richtung_u[1], richtung_u[0])) % 360.0
            axial.setdefault(key, []).append(round(w, 3) % 360.0)
    for (z, dm, tiefe), winkel in sorted(radial.items()):
        fr = Fraesung("bohrung_radial", sorted(set(round(w, 2) for w in winkel)), z, 0.0, dm, tiefe, 0.0,
                      _werkzeug(t, ("bohrer", "fraeser"), "radial", dm), False, _werkzeug(t, ("fase",), "radial"),
                      0.3)
        t.fraesungen.append(fr)
        w = t.tool(fr.wz)
        erk.merkmale.append("Bohrung radial Ø{} × {} bei Z{}, C {}°".format(
            fmt(dm), fmt(tiefe), fmt(z), "/".join(fmt(a) for a in fr.winkel)))
        if w is None or abs(w.dm - dm) > 0.01:
            erk.hinweise.append("Für die Querbohrung Ø{} gibt es keinen Bohrer mit diesem Ø in der Werkzeugliste "
                                "– bitte im Reiter Werkzeuge anlegen.".format(fmt(dm)))
    for (lk, dm, tiefe, oben), winkel in sorted(axial.items()):
        fr = Fraesung("bohrung_axial", sorted(set(winkel)), 0.0 if oben > -0.05 else oben, 0.0, dm, tiefe, lk,
                      _werkzeug(t, ("bohrer", "fraeser"), "axial", dm), False, _werkzeug(t, ("fase",), "axial"),
                      0.3)
        t.fraesungen.append(fr)
        erk.merkmale.append("Bohrung Stirnseite Ø{} × {} auf Lochkreis Ø{}, C {}°".format(
            fmt(dm), fmt(tiefe), fmt(lk), "/".join(fmt(a) for a in fr.winkel)))
        w = t.tool(fr.wz)
        if w is None or abs(w.dm - dm) > 0.01:
            erk.hinweise.append("Für die Stirnbohrung Ø{} gibt es keinen axialen Bohrer mit diesem Ø – bitte im "
                                "Reiter Werkzeuge anlegen.".format(fmt(dm)))
