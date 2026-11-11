"""Fenster zum Ausfüllen des Einrichteblatts (Werkzeuge je Seite, Teil & Spannung, Notizen)."""

from __future__ import annotations

import datetime
import tkinter as tk
from tkinter import ttk
from typing import Dict, List

from . import dialogs
from .dialogs import Dialog, FormDialog
from .setupsheet import (
    KorrRow,
    korr_pdf_path,
    korrektur_rows,
    FIELDS,
    Parameter,
    SheetData,
    ToolUse,
    pdf_path,
    save_sheet,
    side_title,
    tools_by_side,
)
from .setupsheet_pdf import SheetContext, render_correction_sheet, render_setup_sheet
from .system import IS_WIN, open_external, toggle_touch_keyboard
from .theme import C, Fonts, px


class SetupSheetDialog(Dialog):
    """Ergebnis: Pfad der erzeugten PDF, "saved" (nur gespeichert) oder None (abgebrochen)."""

    def __init__(self, parent, fonts: Fonts, ctx: SheetContext, program_path: str, tools: List[ToolUse],
                 params: List[Parameter], data: SheetData) -> None:
        super().__init__(parent, fonts, "Einrichte- & Korrekturblatt – " + ctx.program)
        self.ctx = ctx
        self.program_path = program_path
        self.tools = tools
        self.params = params
        self.data = data
        self._rows: Dict[str, ToolUse] = {}
        self._korr_rows: Dict[str, KorrRow] = {}
        bg = C["bg"]

        head = tk.Frame(self, bg=bg)
        head.pack(fill="x", padx=px(self, 20), pady=(px(self, 14), px(self, 6)))
        tk.Label(head, text="Einrichte- & Korrekturblatt  ·  " + ctx.program, bg=bg, fg=C["text"],
                 font=fonts.title).pack(
            anchor="w")
        sub = "  ·  ".join(x for x in (ctx.customer and "Kunde " + ctx.customer, ctx.machine) if x)
        tk.Label(head, text=sub, bg=bg, fg=C["muted"], font=fonts.ui).pack(anchor="w")

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=px(self, 16))
        self._build_tools_tab()
        self._build_korr_tab()
        self._build_part_tab()
        self._build_notes_tab()
        self._build_params_tab()

        bar = tk.Frame(self, bg=bg)
        bar.pack(fill="x", padx=px(self, 16), pady=px(self, 12))
        ttk.Button(bar, text="Einrichteblatt-PDF", style="Primary.TButton",
                   command=lambda: self.make_pdf("setup")).pack(side="right")
        ttk.Button(bar, text="Korrekturblatt-PDF", style="Primary.TButton",
                   command=lambda: self.make_pdf("korr")).pack(side="right", padx=(0, px(self, 12)))
        ttk.Button(bar, text="Speichern", command=self.save_only).pack(side="right", padx=(0, px(self, 12)))
        ttk.Button(bar, text="Abbrechen", command=self.cancel).pack(side="right", padx=(0, px(self, 12)))
        tk.Label(bar, text="Eingaben bleiben neben dem Programm gespeichert.",
                 bg=bg, fg=C["muted"], font=fonts.small).pack(side="left")

    # -- Werkzeuge ---------------------------------------------------------------

    def _build_tools_tab(self) -> None:
        f = tk.Frame(self.nb, bg=C["surface"], padx=px(self, 14), pady=px(self, 12))
        self.nb.add(f, text="Werkzeuge")
        tk.Label(f, text="„Bearbeitung“ kommt aus dem Kommentar über dem Werkzeug im Programm. "
                         "Zeile antippen, um sie zu ändern oder eine Bemerkung einzutragen.",
                 bg=C["surface"], fg=C["muted"], font=self.fonts.ui, anchor="w", justify="left",
                 wraplength=px(self, 1100)).pack(anchor="w", pady=(0, px(self, 8)))
        cols = tk.Frame(f, bg=C["surface"])
        cols.pack(fill="both", expand=True)
        groups = tools_by_side(self.tools)
        order = [s for s in (1, 2, 0) if s in groups] or [1]
        self.trees: List[ttk.Treeview] = []
        for i, side in enumerate(order):
            cols.grid_columnconfigure(i, weight=1, uniform="col")
            cols.grid_rowconfigure(1, weight=1)
            title, sub = side_title(side, groups.get(side, []))
            if side == 1 and 2 not in groups and 0 not in groups:
                title = "WERKZEUGE"
            cap = tk.Frame(cols, bg=C["header"])
            cap.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else px(self, 10), 0))
            tk.Label(cap, text=title, bg=C["header"], fg="#FFFFFF", font=self.fonts.ui_bold).pack(
                side="left", padx=px(self, 8), pady=px(self, 6))
            tk.Label(cap, text=sub, bg=C["header"], fg=C["header_muted"], font=self.fonts.small).pack(
                side="right", padx=px(self, 8))
            box = tk.Frame(cols, bg=C["surface"], highlightthickness=1, highlightbackground=C["border"])
            box.grid(row=1, column=i, sticky="nsew", padx=(0 if i == 0 else px(self, 10), 0))
            tree = ttk.Treeview(box, columns=("tool", "desc", "remark"), show="headings", selectmode="browse",
                                height=7)
            tree.heading("tool", text="T", anchor="center")
            tree.heading("desc", text="BEARBEITUNG", anchor="w")
            tree.heading("remark", text="BEMERKUNG", anchor="w")
            tree.column("tool", width=px(self, 52), anchor="center", stretch=False)
            tree.column("desc", width=px(self, 215), anchor="w", stretch=True)
            tree.column("remark", width=px(self, 115), anchor="w", stretch=True)
            tree.tag_configure("remark", foreground=C["primary"])
            sb = ttk.Scrollbar(box, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=sb.set)
            tree.pack(side="left", fill="both", expand=True)
            sb.pack(side="right", fill="y")
            for t in groups.get(side, []):
                e = self.data.tool_entry(t)
                iid = tree.insert("", "end", values=(t.tool, e["description"], e["remark"]))
                self._rows[str(tree) + iid] = t
            if not groups.get(side):
                tree.insert("", "end", values=("", "Keine Werkzeuge gefunden", ""))
            tree.bind("<ButtonRelease-1>", lambda ev, tr=tree: self._edit_row(tr, ev))
            self.trees.append(tree)

    def _edit_row(self, tree: ttk.Treeview, event) -> None:
        iid = tree.identify_row(event.y)
        t = self._rows.get(str(tree) + iid) if iid else None
        if t is None:
            return
        tool, desc, remark = tree.item(iid, "values")
        res = FormDialog(self, self.fonts, "Werkzeug {}".format(tool),
                         [("desc", "Bearbeitung", desc), ("remark", "Bemerkung", remark)],
                         message="Aus dem Programm: {}".format(t.description or "(kein Kommentar)")).show()
        if res is not None:
            tree.item(iid, values=(tool, res["desc"], res["remark"]))
            self.data.set_tool(t, res["desc"], res["remark"])
            if hasattr(self, "korr_trees"):
                self._fill_korr()  # Bezeichnung auch im Korrekturblatt

    # -- Korrekturblatt ------------------------------------------------------------

    def _build_korr_tab(self) -> None:
        f = tk.Frame(self.nb, bg=C["surface"], padx=px(self, 14), pady=px(self, 12))
        self.nb.add(f, text="Korrekturblatt")
        tk.Label(f, text="Seite 1 links, Seite 2 rechts. Abstechwerkzeug und Anschlag stehen automatisch auf "
                         "Seite 1. Zeile antippen: Seite wechseln, Namen ändern oder Bemerkung eintragen.",
                 bg=C["surface"], fg=C["muted"], font=self.fonts.ui, anchor="w", justify="left",
                 wraplength=px(self, 1100)).pack(anchor="w", pady=(0, px(self, 8)))
        cols = tk.Frame(f, bg=C["surface"])
        cols.pack(fill="both", expand=True)
        self.korr_trees: Dict[int, ttk.Treeview] = {}
        self.korr_titles: Dict[int, tk.Label] = {}
        for i, side in enumerate((1, 2)):
            cols.grid_columnconfigure(i, weight=1, uniform="korr")
            cols.grid_rowconfigure(1, weight=1)
            pad = (0 if i == 0 else px(self, 12), 0)
            cap = tk.Frame(cols, bg=C["header"])
            cap.grid(row=0, column=i, sticky="ew", padx=pad)
            tk.Label(cap, text="SEITE {}".format(side), bg=C["header"], fg="#FFFFFF", font=self.fonts.ui_bold).pack(
                side="left", padx=px(self, 8), pady=px(self, 6))
            count = tk.Label(cap, text="", bg=C["header"], fg=C["header_muted"], font=self.fonts.small)
            count.pack(side="right", padx=px(self, 8))
            self.korr_titles[side] = count
            box = tk.Frame(cols, bg=C["surface"], highlightthickness=1, highlightbackground=C["border"])
            box.grid(row=1, column=i, sticky="nsew", padx=pad)
            tree = ttk.Treeview(box, columns=("tool", "name", "remark"), show="headings", selectmode="browse",
                                height=7)
            tree.heading("tool", text="T", anchor="center")
            tree.heading("name", text="WERKZEUG", anchor="w")
            tree.heading("remark", text="BEMERKUNG", anchor="w")
            tree.column("tool", width=px(self, 52), anchor="center", stretch=False)
            tree.column("name", width=px(self, 250), anchor="w", stretch=True)
            tree.column("remark", width=px(self, 200), anchor="w", stretch=True)
            sb = ttk.Scrollbar(box, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=sb.set)
            tree.pack(side="left", fill="both", expand=True)
            sb.pack(side="right", fill="y")
            tree.bind("<ButtonRelease-1>", lambda ev, tr=tree: self._edit_korr(tr, ev))
            self.korr_trees[side] = tree
        self._fill_korr()

    def _fill_korr(self) -> None:
        rows = korrektur_rows(self.tools, self.data)
        self._korr_rows = {}
        for side, tree in self.korr_trees.items():
            tree.delete(*tree.get_children())
            for r in rows[side]:
                iid = tree.insert("", "end", values=(r.tool, r.name, r.remark))
                self._korr_rows[str(tree) + iid] = r
            n = len(rows[side])
            text = "{} Werkzeuge".format(n) if n != 1 else "1 Werkzeug"
            if n > 12:
                text += "  ·  mehr als 12 – Zeilen werden enger"
            self.korr_titles[side].configure(text=text)

    def _edit_korr(self, tree: ttk.Treeview, event) -> None:
        iid = tree.identify_row(event.y)
        r = self._korr_rows.get(str(tree) + iid) if iid else None
        if r is None:
            return
        res = FormDialog(self, self.fonts, "Korrekturblatt: {}".format(r.tool),
                         [("side", "Seite", "Seite {}".format(r.side), ("Seite 1", "Seite 2")),
                          ("name", "Werkzeug", r.name), ("remark", "Bemerkung", r.remark)],
                         message="Aus dem Programm: {}".format(r.programs or "–")).show()
        if res is None:
            return
        side = 2 if res["side"] == "Seite 2" else 1
        for k in r.keys or (r.key,):
            entry = self.data.korrektur.setdefault(k, {})
            entry["side"] = side  # zusammengefasste Zeile wandert komplett
            if k != r.key:
                entry.pop("name", None)
                entry.pop("remark", None)
        entry = self.data.korrektur[r.key]
        if res["name"] != r.name or "name" in entry:
            entry["name"] = res["name"]
        entry["remark"] = res["remark"]
        self._fill_korr()

    # -- Teil & Spannung ---------------------------------------------------------

    def _build_part_tab(self) -> None:
        f = tk.Frame(self.nb, bg=C["surface"], padx=px(self, 20), pady=px(self, 12))
        self.nb.add(f, text="Teil & Spannung")
        self.field_vars: Dict[str, tk.StringVar] = {}
        if not self.data.fields.get("datum"):
            self.data.fields["datum"] = datetime.date.today().strftime("%d.%m.%Y")
        groups = (("teil", "TEIL", 0), ("hsp", "HAUPTSPINDEL (SEITE 1)", 1), ("gsp", "GEGENSPINDEL (SEITE 2)", 1))
        rows = {0: 0, 1: 0}
        f.grid_columnconfigure(1, weight=1)
        f.grid_columnconfigure(4, weight=1)
        for group, caption, col in groups:
            c0 = col * 3
            r = rows[col]
            tk.Label(f, text=caption, bg=C["surface"], fg=C["faint"], font=self.fonts.small_bold).grid(
                row=r, column=c0, columnspan=2, sticky="w", pady=(px(self, 6) if r else 0, px(self, 2)))
            r += 1
            for key, label, g in FIELDS:
                if g != group:
                    continue
                tk.Label(f, text=label, bg=C["surface"], fg=C["text"], font=self.fonts.ui, anchor="w").grid(
                    row=r, column=c0, sticky="w", padx=(0, px(self, 12)), pady=px(self, 3))
                var = tk.StringVar(value=self.data.fields.get(key, ""))
                ttk.Entry(f, textvariable=var, font=self.fonts.ui, width=26).grid(
                    row=r, column=c0 + 1, sticky="ew", pady=px(self, 3))
                self.field_vars[key] = var
                r += 1
            rows[col] = r
        tk.Frame(f, bg=C["surface"], width=px(self, 30)).grid(row=0, column=2)
        if IS_WIN:
            ttk.Button(f, text="⌨  Tastatur", style="Small.TButton", command=toggle_touch_keyboard).grid(
                row=max(rows.values()), column=4, sticky="e", pady=(px(self, 10), 0))

    # -- Notizen & Parameter -----------------------------------------------------

    def _build_notes_tab(self) -> None:
        f = tk.Frame(self.nb, bg=C["surface"], padx=px(self, 14), pady=px(self, 12))
        self.nb.add(f, text="Notizen")
        tk.Label(f, text="Besonderheiten beim Einrichten, worauf beim nächsten Mal zu achten ist …",
                 bg=C["surface"], fg=C["muted"], font=self.fonts.ui).pack(anchor="w", pady=(0, px(self, 6)))
        box = tk.Frame(f, bg=C["surface"], highlightthickness=1, highlightbackground=C["border"])
        box.pack(fill="both", expand=True)
        self.notes = tk.Text(box, height=9, wrap="word", font=self.fonts.list, relief="flat", bd=0,
                             padx=px(self, 8), pady=px(self, 6), bg=C["surface"], fg=C["text"],
                             insertbackground=C["text"], highlightthickness=0)
        sb = ttk.Scrollbar(box, orient="vertical", command=self.notes.yview)
        self.notes.configure(yscrollcommand=sb.set)
        self.notes.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.notes.insert("1.0", self.data.notes)

    def _build_params_tab(self) -> None:
        f = tk.Frame(self.nb, bg=C["surface"], padx=px(self, 14), pady=px(self, 12))
        self.nb.add(f, text="Parameter ({})".format(len(self.params)))
        if not self.params:
            tk.Label(f, text="Im Programm wurde kein Parameter-Programm mit kommentierten Werten gefunden "
                             "(z. B. %_N_PARAMETER_MPF mit „R72=32 ;DURCHMESSER STANGE“).",
                     bg=C["surface"], fg=C["muted"], font=self.fonts.ui, wraplength=px(self, 900),
                     justify="left").pack(anchor="w")
            return
        tk.Label(f, text="Werden automatisch aus dem Programm übernommen und auf das Blatt gedruckt.",
                 bg=C["surface"], fg=C["muted"], font=self.fonts.ui).pack(anchor="w", pady=(0, px(self, 6)))
        tree = ttk.Treeview(f, columns=("name", "value", "comment"), show="headings", height=8)
        for col, text, w, anchor in (("name", "PARAMETER", 150, "w"), ("value", "WERT", 90, "e"),
                                     ("comment", "BEDEUTUNG", 600, "w")):
            tree.heading(col, text=text, anchor=anchor)
            tree.column(col, width=px(self, w), anchor=anchor, stretch=(col == "comment"))
        for p in self.params:
            tree.insert("", "end", values=(p.name, p.value, p.comment))
        tree.pack(fill="both", expand=True)

    # -- Speichern / PDF -----------------------------------------------------------

    def _collect(self) -> None:
        for key, var in self.field_vars.items():
            self.data.fields[key] = var.get().strip()
        self.data.notes = self.notes.get("1.0", "end-1c").strip()

    def save_only(self) -> None:
        self._collect()
        try:
            save_sheet(self.program_path, self.data)
        except OSError as exc:
            dialogs.error(self, self.fonts, "Speichern fehlgeschlagen", str(exc))
            return
        self.result = "saved"
        self.destroy()

    def make_pdf(self, kind: str = "setup") -> None:
        self._collect()
        target = korr_pdf_path(self.program_path) if kind == "korr" else pdf_path(self.program_path)
        try:
            save_sheet(self.program_path, self.data)
            if kind == "korr":
                render_correction_sheet(target, self.ctx, korrektur_rows(self.tools, self.data), self.data)
            else:
                render_setup_sheet(target, self.ctx, self.tools, self.params, self.data)
        except OSError as exc:
            hint = "Ist die PDF vielleicht noch in einem anderen Programm geöffnet?" \
                if "denied" in str(exc).lower() or "zugriff" in str(exc).lower() else ""
            dialogs.error(self, self.fonts, "PDF konnte nicht erstellt werden", str(exc), detail=hint)
            return
        self.result = target
        self.destroy()
        if not open_external(target):
            dialogs.info(self.parent, self.fonts, "PDF erstellt",
                         "Die PDF wurde gespeichert, konnte aber nicht automatisch geöffnet werden.",
                         detail=target)
