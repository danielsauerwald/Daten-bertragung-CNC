import os
import time

from cnc_dnc.files import (
    find_existing,
    list_folder,
    match_customer_folder,
    search,
    subfolders,
    suggest_folder,
    walk_files,
)


def _make(tmp_path):
    """Nachbau des Ordners vom Tablet: Kundenordner + lose Dateien."""
    root = tmp_path / "Emco-2"
    for d in ("Bamberg", "berg", "betzer", "FB_Boese", "Maweco", "Metlog", "Metlog/Archiv"):
        (root / d).mkdir(parents=True)
    (root / "TAKEOVER.txt").write_text("x")
    (root / "MAWECO_300210.txt").write_text("x")
    (root / "Metlog" / "METLOG_300012.txt").write_text("x")
    (root / "Metlog" / "Archiv" / "METLOG_100001.txt").write_text("x")
    (root / "betzer" / "BETZER_300245.txt").write_text("x")
    (root / ".versteckt").mkdir()
    return str(root)


def test_list_folder_dirs_first_sorted(tmp_path):
    root = _make(tmp_path)
    dirs, files = list_folder(root)
    assert [d.name for d in dirs] == ["Bamberg", "berg", "betzer", "FB_Boese", "Maweco", "Metlog"]
    assert sorted(f.name for f in files) == ["MAWECO_300210.txt", "TAKEOVER.txt"]


def test_subfolders_and_search(tmp_path):
    root = _make(tmp_path)
    labels = [lbl for lbl, _ in subfolders(root)]
    assert "Metlog › Archiv" in labels and "Metlog" in labels
    hits = walk_files(root)
    assert len(hits) == 5
    found = search(hits, "metlog")
    assert {h.name for h in found} == {"METLOG_300012.txt", "METLOG_100001.txt"}
    assert {h.folder for h in found} == {"Metlog", "Metlog › Archiv"}
    assert [h.name for h in search(hits, "metlog 1000")] == ["METLOG_100001.txt"]


def test_match_customer_folder():
    folders = ["Bamberg", "berg", "betzer", "FB_Boese", "Maweco", "Metlog", "mbs"]
    assert match_customer_folder("METLOG_300069", folders) == "Metlog"
    assert match_customer_folder("metlog300069", folders) == "Metlog"
    assert match_customer_folder("FB-BOESE_12", folders) == "FB_Boese"
    assert match_customer_folder("BERG_77", folders) == "berg"
    assert match_customer_folder("BERGMANN_1", folders) is None
    assert match_customer_folder("O1234_FLANSCH", folders) is None


def test_suggest_folder(tmp_path):
    root = _make(tmp_path)
    # gibt es schon -> dorthin (auch im Unterordner)
    path, why = suggest_folder(root, "METLOG_100001")
    assert path.endswith(os.path.join("Metlog", "Archiv")) and "schon" in why
    # neuer Auftrag -> Kundenordner nach Namen
    path, why = suggest_folder(root, "METLOG_300069")
    assert path.endswith("Metlog") and "Kundenordner" in why
    # unbekannt -> gerade geöffneter Ordner, sonst Hauptordner
    path, _ = suggest_folder(root, "O1234", browsed=os.path.join(root, "Bamberg"))
    assert path.endswith("Bamberg")
    assert suggest_folder(root, "O1234") == (root, "")


def test_find_existing_prefers_newest(tmp_path):
    root = _make(tmp_path)
    newer = os.path.join(root, "Maweco", "MAWECO_300210.nc")
    open(newer, "w").write("y")
    t = time.time() + 10
    os.utime(newer, (t, t))
    assert find_existing(root, "maweco_300210").endswith("Maweco")
