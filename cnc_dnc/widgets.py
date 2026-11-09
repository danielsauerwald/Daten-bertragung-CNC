"""Eigene Bedienelemente: große Touch-Buttons, Status-LEDs und der Programm-Editor."""

from __future__ import annotations

import re
import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

from .theme import C, SYNTAX, Fonts, px

# --------------------------------------------------------------------------
# Großer, flacher Button
# --------------------------------------------------------------------------

PALETTES = {
    # Hintergrund, Hover, gedrückt, Schrift, Untertitel
    "primary": (C["primary"], C["primary_hover"], C["primary_press"], "#FFFFFF", "#D5E3FF"),
    "success": (C["success"], C["success_hover"], C["success_press"], "#FFFFFF", "#C9F0DE"),
    "danger": (C["danger"], C["danger_hover"], C["danger_press"], "#FFFFFF", "#FFD9D9"),
    "neutral": (C["neutral"], C["neutral_hover"], C["neutral_press"], C["text"], C["muted"]),
    "header": (C["header"], C["header_hover"], C["header_press"], C["header_text"], C["header_muted"]),
}


class ActionButton(tk.Frame):
    """Flacher Button mit Symbol, Titel und optionalem Untertitel."""

    def __init__(self, master, fonts: Fonts, text: str, command: Optional[Callable[[], None]] = None,
                 subtitle: str = "", icon: str = "", kind: str = "neutral", big: bool = False,
                 compact: bool = False, anchor: str = "w", outline: Optional[str] = None) -> None:
        self.kind = kind
        self.outline = outline
        self.command = command
        self.enabled = True
        self._pressed = False
        self._hover = False
        bg = PALETTES[kind][0]
        border = outline or (C["border"] if kind == "neutral" else bg)
        super().__init__(master, bg=bg, highlightthickness=1, highlightbackground=border,
                         highlightcolor=border, cursor="hand2")
        padx = px(self, 10 if compact else 14)
        pady = px(self, 6 if compact else (14 if big else 10))
        self.inner = tk.Frame(self, bg=bg)
        self.inner.pack(fill="both", expand=True, padx=padx, pady=pady)

        title_font = fonts.big if big else (fonts.ui_bold if compact else fonts.button)
        self.icon_label = tk.Label(self.inner, text=icon, bg=bg, font=fonts.big if (big or not compact) else fonts.button)
        self.title_label = tk.Label(self.inner, text=text, bg=bg, font=title_font, anchor="w", justify="left")
        self.sub_label = tk.Label(self.inner, text=subtitle, bg=bg, font=fonts.button_sub, anchor="w", justify="left")

        col = 0
        if icon:
            self.icon_label.grid(row=0, column=0, rowspan=2, sticky="w", padx=(0, px(self, 10)))
            col = 1
        self._col = col
        self._sticky = "w" if anchor == "w" else ""
        self._layout_labels(bool(subtitle))
        if anchor != "w":
            self.inner.grid_columnconfigure(col, weight=1)
            if icon:
                self.inner.grid_columnconfigure(0, weight=0)

        for w in (self, self.inner, self.icon_label, self.title_label, self.sub_label):
            w.bind("<Enter>", self._on_enter)
            w.bind("<Leave>", self._on_leave)
            w.bind("<ButtonPress-1>", self._on_press)
            w.bind("<ButtonRelease-1>", self._on_release)
        self._paint()

    def _layout_labels(self, with_subtitle: bool) -> None:
        self._has_sub = with_subtitle
        if with_subtitle:
            self.title_label.grid(row=0, column=self._col, rowspan=1, sticky=self._sticky)
            self.sub_label.grid(row=1, column=self._col, sticky=self._sticky)
        else:
            self.sub_label.grid_remove()
            self.title_label.grid(row=0, column=self._col, rowspan=2, sticky=self._sticky)

    def _colors(self):
        bg, hover, press, fg, sub = PALETTES[self.kind]
        if not self.enabled:
            if self.kind == "header":
                return C["header"], C["header_muted"], C["header_muted"], self.outline or C["header"]
            return C["disabled_bg"], C["disabled_text"], C["disabled_text"], C["disabled_bg"]
        if self._pressed:
            bg = press
        elif self._hover:
            bg = hover
        border = self.outline or (C["border"] if self.kind == "neutral" else bg)
        return bg, fg, sub, border

    def _paint(self) -> None:
        bg, fg, sub, border = self._colors()
        self.configure(bg=bg, highlightbackground=border, highlightcolor=border,
                       cursor="hand2" if self.enabled else "arrow")
        self.inner.configure(bg=bg)
        self.icon_label.configure(bg=bg, fg=fg)
        self.title_label.configure(bg=bg, fg=fg)
        self.sub_label.configure(bg=bg, fg=sub)

    def _on_enter(self, _e=None) -> None:
        self._hover = True
        self._paint()

    def _on_leave(self, _e=None) -> None:
        self._hover = False
        self._pressed = False
        self._paint()

    def _on_press(self, _e=None) -> None:
        if not self.enabled:
            return
        self._pressed = True
        self._paint()

    def _on_release(self, event=None) -> None:
        was_pressed = self._pressed
        self._pressed = False
        self._paint()
        if not (was_pressed and self.enabled and self.command):
            return
        # nur auslösen, wenn der Finger noch auf dem Button ist
        if event is not None:
            x, y = event.x_root, event.y_root
            x0, y0 = self.winfo_rootx(), self.winfo_rooty()
            if not (x0 <= x < x0 + self.winfo_width() and y0 <= y < y0 + self.winfo_height()):
                return
        self.after_idle(self.command)

    def set_enabled(self, enabled: bool) -> None:
        if self.enabled != enabled:
            self.enabled = enabled
            self._paint()

    def set_text(self, text: Optional[str] = None, subtitle: Optional[str] = None, icon: Optional[str] = None) -> None:
        if text is not None:
            self.title_label.configure(text=text)
        if subtitle is not None:
            self.sub_label.configure(text=subtitle)
            if bool(subtitle) != self._has_sub:
                self._layout_labels(bool(subtitle))
        if icon is not None:
            self.icon_label.configure(text=icon)

    def set_kind(self, kind: str) -> None:
        self.kind = kind
        self._paint()


# --------------------------------------------------------------------------
# LED für Steuerleitungen
# --------------------------------------------------------------------------

class Led(tk.Frame):
    def __init__(self, master, fonts: Fonts, label: str, on_color: str = C["led_on"], tooltip: str = "") -> None:
        super().__init__(master, bg=C["header"])
        size = px(self, 14)
        self.on_color = on_color
        self.canvas = tk.Canvas(self, width=size + 2, height=size + 2, bg=C["header"], highlightthickness=0, bd=0)
        self.dot = self.canvas.create_oval(1, 1, size, size, fill=C["led_off"], outline="")
        self.canvas.pack()
        tk.Label(self, text=label, bg=C["header"], fg=C["header_muted"], font=fonts.small).pack()
        self._state: Optional[bool] = None
        self.set(False)

    def set(self, on: bool) -> None:
        if on != self._state:
            self._state = on
            self.canvas.itemconfigure(self.dot, fill=self.on_color if on else C["led_off"])


# --------------------------------------------------------------------------
# Editor mit Zeilennummern und G-Code-Hervorhebung
# --------------------------------------------------------------------------

_NB = r"(?<![A-Za-z_])"
SYNTAX_RULES = [
    ("blocknr", re.compile(_NB + r"N\d+")),
    ("gcode", re.compile(_NB + r"G\d+(?:\.\d+)?")),
    ("mcode", re.compile(_NB + r"M\d+")),
    ("tool", re.compile(_NB + r"[TDH]\d+")),
    ("feed", re.compile(_NB + r"[FS][-+]?(?:\d+\.?\d*|\.\d+)")),
    ("prog", re.compile(r"^\s*(?:%.*|[O:]\d+|<[^>]*>|\d*\s*(?:BEGIN|END)\s+PGM.*)", re.IGNORECASE)),
    ("comment", re.compile(r"\([^)]*\)?|;.*")),
]


class CodeEditor(tk.Frame):
    def __init__(self, master, fonts: Fonts, on_change: Optional[Callable[[], None]] = None,
                 on_cursor: Optional[Callable[[int, int, int], None]] = None) -> None:
        super().__init__(master, bg=C["surface"], highlightthickness=1, highlightbackground=C["border"])
        self.fonts = fonts
        self.on_change = on_change
        self.on_cursor = on_cursor
        self._refresh_pending = False
        self._gutter_digits = 0
        self._drag_y: Optional[int] = None
        self._drag_acc = 0.0

        self.gutter = tk.Canvas(self, bg=C["gutter"], highlightthickness=0, bd=0, width=px(self, 56))
        self.text = tk.Text(
            self, wrap="none", undo=True, autoseparators=True, maxundo=-1, font=fonts.mono,
            bg=C["surface"], fg=C["text"], insertbackground=C["text"], selectbackground=C["select"],
            selectforeground=C["text"], inactiveselectbackground=C["select"], relief="flat", bd=0,
            highlightthickness=0, padx=px(self, 10), pady=px(self, 6), spacing1=px(self, 2),
            spacing3=px(self, 2), insertwidth=px(self, 2), exportselection=False,
        )
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        self.hsb = ttk.Scrollbar(self, orient="horizontal", command=self.text.xview)
        self.text.configure(yscrollcommand=self._on_yscroll, xscrollcommand=self.hsb.set)

        self.gutter.grid(row=0, column=0, sticky="ns")
        self.text.grid(row=0, column=1, sticky="nsew")
        self.vsb.grid(row=0, column=2, sticky="ns")
        self.hsb.grid(row=1, column=0, columnspan=2, sticky="ew")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        # Tag-Reihenfolge = Priorität (später angelegt = wichtiger)
        self.text.tag_configure("curline", background=C["curline"])
        for name in ("blocknr", "gcode", "mcode", "tool", "feed", "prog", "comment"):
            self.text.tag_configure(name, **SYNTAX[name])
        self.text.tag_configure("prog", font=fonts.mono_bold)
        self.text.tag_configure("found", background=C["found"])
        self.text.tag_raise("sel")

        self.text.bind("<<Modified>>", self._on_modified)
        for seq in ("<KeyRelease>", "<ButtonRelease-1>", "<Configure>"):
            self.text.bind(seq, lambda e: self.schedule_refresh(), add="+")

        # Auf der Zeilennummern-Spalte mit dem Finger scrollen
        self.gutter.bind("<ButtonPress-1>", self._drag_start)
        self.gutter.bind("<B1-Motion>", self._drag_move)
        self.gutter.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag_y", None))
        self.gutter.bind("<MouseWheel>", lambda e: self.text.yview_scroll(int(-e.delta / 40) or (-1 if e.delta > 0 else 1), "units"))
        self.gutter.bind("<Button-4>", lambda e: self.text.yview_scroll(-3, "units"))
        self.gutter.bind("<Button-5>", lambda e: self.text.yview_scroll(3, "units"))

    # -- Inhalt ----------------------------------------------------------------

    def get_text(self) -> str:
        return self.text.get("1.0", "end-1c")

    def set_text(self, content: str) -> None:
        was_disabled = self.text.cget("state") == "disabled"
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", content)
        self.text.edit_reset()
        self.text.edit_modified(False)
        self.text.mark_set("insert", "1.0")
        self.text.see("1.0")
        self.text.tag_remove("found", "1.0", "end")
        if was_disabled:
            self.text.configure(state="disabled")
        self.schedule_refresh()

    def append(self, content: str, follow: bool = True) -> None:
        was_disabled = self.text.cget("state") == "disabled"
        self.text.configure(state="normal")
        self.text.insert("end-1c", content)
        self.text.edit_modified(False)
        if was_disabled:
            self.text.configure(state="disabled")
        if follow:
            self.text.see("end-1c")
        self.schedule_refresh()

    def set_readonly(self, readonly: bool) -> None:
        self.text.configure(state="disabled" if readonly else "normal")

    def line_count(self) -> int:
        """Anzahl Zeilen (eine leere letzte Zeile nach dem Zeilenende zählt nicht)."""
        last = int(self.text.index("end-1c").split(".")[0])
        if last > 1 and self.text.get("end-2c", "end-1c") == "\n":
            last -= 1
        return last

    def is_empty(self) -> bool:
        return not self.get_text().strip()

    def clear_undo(self) -> None:
        self.text.edit_reset()

    def focus_editor(self) -> None:
        self.text.focus_set()

    # -- Suchen ----------------------------------------------------------------

    def highlight_all(self, term: str) -> int:
        self.text.tag_remove("found", "1.0", "end")
        if not term:
            return 0
        count = 0
        start = "1.0"
        length = tk.IntVar()
        while count < 5000:
            pos = self.text.search(term, start, stopindex="end", nocase=True, count=length)
            if not pos:
                break
            end = "{}+{}c".format(pos, max(1, length.get()))
            self.text.tag_add("found", pos, end)
            start = end
            count += 1
        return count

    def find_next(self, term: str, backwards: bool = False) -> bool:
        if not term:
            return False
        length = tk.IntVar()
        if backwards:
            start = self.text.index("insert-1c")
            pos = self.text.search(term, start, stopindex="1.0", nocase=True, backwards=True, count=length)
            if not pos:
                pos = self.text.search(term, "end", stopindex="1.0", nocase=True, backwards=True, count=length)
        else:
            start = self.text.index("insert+1c")
            pos = self.text.search(term, start, stopindex="end", nocase=True, count=length)
            if not pos:
                pos = self.text.search(term, "1.0", stopindex="end", nocase=True, count=length)
        if not pos:
            return False
        end = "{}+{}c".format(pos, max(1, length.get()))
        self.text.tag_remove("sel", "1.0", "end")
        self.text.tag_add("sel", pos, end)
        self.text.mark_set("insert", pos)
        self.text.see(pos)
        self.schedule_refresh()
        return True

    def goto_line(self, line: int) -> None:
        self.text.mark_set("insert", "{}.0".format(max(1, line)))
        self.text.see("insert")
        self.schedule_refresh()

    # -- Interne Aktualisierung ------------------------------------------------

    def _on_modified(self, _e=None) -> None:
        if self.text.edit_modified():
            self.text.edit_modified(False)
            if self.on_change:
                self.on_change()
            self.schedule_refresh()

    def _on_yscroll(self, first, last) -> None:
        self.vsb.set(first, last)
        self.schedule_refresh()

    def schedule_refresh(self) -> None:
        if not self._refresh_pending:
            self._refresh_pending = True
            self.after_idle(self._refresh)

    def _refresh(self) -> None:
        self._refresh_pending = False
        try:
            self._update_curline()
            self._redraw_gutter()
            self._highlight_visible()
        except tk.TclError:
            return
        if self.on_cursor:
            line, col = (int(x) for x in self.text.index("insert").split("."))
            self.on_cursor(line, col + 1, self.line_count())

    def _update_curline(self) -> None:
        self.text.tag_remove("curline", "1.0", "end")
        self.text.tag_add("curline", "insert linestart", "insert lineend+1c")

    def _redraw_gutter(self) -> None:
        total = self.line_count()
        digits = max(3, len(str(total)))
        if digits != self._gutter_digits:
            self._gutter_digits = digits
            self.gutter.configure(width=self.fonts.gutter.measure("0" * digits) + px(self, 22))
        g = self.gutter
        g.delete("all")
        width = int(g.cget("width"))
        cur = int(self.text.index("insert").split(".")[0])
        idx = self.text.index("@0,0")
        prev = None
        while idx != prev:
            info = self.text.dlineinfo(idx)
            if info is None:
                break
            y, h = info[1], info[3]
            line = int(idx.split(".")[0])
            g.create_text(width - px(self, 10), y + h / 2, anchor="e", text=str(line), font=self.fonts.gutter,
                          fill=C["gutter_cur"] if line == cur else C["gutter_text"])
            prev = idx
            idx = self.text.index("{}+1line".format(idx))
        g.create_line(width - 1, 0, width - 1, g.winfo_height(), fill=C["border"])

    def _highlight_visible(self) -> None:
        first = int(self.text.index("@0,0").split(".")[0])
        last = int(self.text.index("@0,{}".format(self.text.winfo_height())).split(".")[0])
        start, end = "{}.0".format(first), "{}.end".format(last)
        for name, _rx in SYNTAX_RULES:
            self.text.tag_remove(name, start, end)
        for ln in range(first, last + 1):
            s = self.text.get("{}.0".format(ln), "{}.end".format(ln))
            if not s:
                continue
            if len(s) > 2000:
                s = s[:2000]
            for name, rx in SYNTAX_RULES:
                for m in rx.finditer(s):
                    self.text.tag_add(name, "{}.{}".format(ln, m.start()), "{}.{}".format(ln, m.end()))

    def _drag_start(self, event) -> None:
        self._drag_y = event.y
        self._drag_acc = 0.0

    def _drag_move(self, event) -> None:
        if self._drag_y is None:
            return
        self._drag_acc += event.y - self._drag_y
        self._drag_y = event.y
        line_h = max(1, self.fonts.mono.metrics("linespace") + 2 * px(self, 2))
        steps = int(self._drag_acc / line_h)
        if steps:
            self.text.yview_scroll(-steps, "units")
            self._drag_acc -= steps * line_h
