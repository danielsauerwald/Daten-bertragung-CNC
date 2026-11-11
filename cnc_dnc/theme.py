"""Farben, Schriften und ttk-Stile – auf Touch-Bedienung ausgelegt."""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

IS_WIN = sys.platform.startswith("win")

C = {
    "bg": "#EDF0F4",
    "surface": "#FFFFFF",
    "surface_alt": "#F6F8FA",
    "border": "#D4DAE2",
    "text": "#16202B",
    "muted": "#5D6A79",
    "faint": "#8B96A4",
    "header": "#18222E",
    "header_hover": "#253242",
    "header_press": "#2E3E52",
    "header_text": "#FFFFFF",
    "header_muted": "#A3AFBD",
    "primary": "#1C64D8",
    "primary_hover": "#1756BD",
    "primary_press": "#12479D",
    "success": "#0E8A5B",
    "success_hover": "#0B754D",
    "success_press": "#095F3F",
    "danger": "#C93A3A",
    "danger_hover": "#AE2F2F",
    "danger_press": "#922626",
    "neutral": "#FFFFFF",
    "neutral_hover": "#E8EDF3",
    "neutral_press": "#D8E0EA",
    "disabled_bg": "#E2E6EB",
    "disabled_text": "#A0AAB6",
    "led_off": "#3B4858",
    "led_on": "#2FD47A",
    "led_tx": "#FFB020",
    "led_rx": "#47A6FF",
    "ok": "#0E8A5B",
    "warn": "#B26B00",
    "error": "#C93A3A",
    "select": "#CFE0FF",
    "curline": "#F3F7FD",
    "found": "#FFE58A",
    "gutter": "#F4F6F9",
    "gutter_text": "#9AA4B1",
    "gutter_cur": "#16202B",
}

SYNTAX = {
    "comment": {"foreground": "#5C8A5E"},
    "prog": {"foreground": "#7A3EB1"},
    "blocknr": {"foreground": "#9AA4B1"},
    "gcode": {"foreground": "#1C5FC9"},
    "mcode": {"foreground": "#C2410C"},
    "tool": {"foreground": "#0F766E"},
    "feed": {"foreground": "#A16207"},
}

UI_FAMILY = "Segoe UI" if IS_WIN else "DejaVu Sans"
MONO_FAMILY = "Consolas" if IS_WIN else "DejaVu Sans Mono"


class Fonts:
    """Benannte Schriften, damit sie zentral angepasst werden können."""

    def __init__(self, root: tk.Misc, editor_size: int = 14) -> None:
        f = UI_FAMILY
        self.ui = tkfont.Font(root, family=f, size=11)
        self.ui_bold = tkfont.Font(root, family=f, size=11, weight="bold")
        self.small = tkfont.Font(root, family=f, size=9)
        self.small_bold = tkfont.Font(root, family=f, size=9, weight="bold")
        self.button = tkfont.Font(root, family=f, size=12, weight="bold")
        self.button_sub = tkfont.Font(root, family=f, size=9)
        self.big = tkfont.Font(root, family=f, size=16, weight="bold")
        self.title = tkfont.Font(root, family=f, size=14, weight="bold")
        self.heading = tkfont.Font(root, family=f, size=12, weight="bold")
        self.list = tkfont.Font(root, family=f, size=12)
        self.mono = tkfont.Font(root, family=MONO_FAMILY, size=editor_size)
        self.mono_bold = tkfont.Font(root, family=MONO_FAMILY, size=editor_size, weight="bold")
        self.gutter = tkfont.Font(root, family=MONO_FAMILY, size=max(8, editor_size - 3))

    def set_editor_size(self, size: int) -> None:
        self.mono.configure(size=size)
        self.mono_bold.configure(size=size)
        self.gutter.configure(size=max(8, size - 3))


def px(root: tk.Misc, value: float) -> int:
    """Pixelwerte an die Bildschirm-DPI anpassen (96 DPI = 1:1)."""
    try:
        scale = float(root.winfo_fpixels("1i")) / 96.0
    except tk.TclError:
        scale = 1.0
    return int(round(value * max(1.0, scale)))


def enable_dpi_awareness() -> None:
    """Unter Windows scharfe Darstellung statt verschwommen hochskalierter Pixel."""
    if not IS_WIN:
        return
    try:
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # Windows 8.1+
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()  # Windows 7
    except Exception:
        pass


def setup_style(root: tk.Tk, fonts: Fonts) -> ttk.Style:
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    root.configure(background=C["bg"])
    root.option_add("*Font", fonts.ui)
    root.option_add("*TCombobox*Listbox.font", fonts.list)
    root.option_add("*TCombobox*Listbox.selectBackground", C["primary"])
    root.option_add("*TCombobox*Listbox.selectForeground", "#FFFFFF")

    style.configure(".", background=C["bg"], foreground=C["text"], font=fonts.ui,
                    bordercolor=C["border"], lightcolor=C["surface"], darkcolor=C["border"],
                    troughcolor=C["bg"], focuscolor=C["primary"], selectbackground=C["select"],
                    selectforeground=C["text"], insertcolor=C["text"])

    style.configure("TFrame", background=C["bg"])
    style.configure("Surface.TFrame", background=C["surface"])
    style.configure("Header.TFrame", background=C["header"])
    style.configure("Card.TFrame", background=C["surface"], bordercolor=C["border"], relief="solid", borderwidth=1)

    style.configure("TLabel", background=C["bg"], foreground=C["text"])
    style.configure("Surface.TLabel", background=C["surface"])
    style.configure("Muted.TLabel", foreground=C["muted"])
    style.configure("SurfaceMuted.TLabel", background=C["surface"], foreground=C["muted"])
    style.configure("Heading.TLabel", font=fonts.heading)
    style.configure("SurfaceHeading.TLabel", background=C["surface"], font=fonts.heading)
    style.configure("Title.TLabel", font=fonts.title)
    style.configure("Header.TLabel", background=C["header"], foreground=C["header_text"])
    style.configure("HeaderMuted.TLabel", background=C["header"], foreground=C["header_muted"], font=fonts.small)

    # Eingabefelder groß genug für den Finger
    style.configure("TEntry", padding=(px(root, 8), px(root, 7)), fieldbackground=C["surface"])
    style.configure("TCombobox", padding=(px(root, 8), px(root, 6)), arrowsize=px(root, 22),
                    fieldbackground=C["surface"], background=C["surface"], arrowcolor=C["muted"])
    style.map("TCombobox", fieldbackground=[("readonly", C["surface"])],
              selectbackground=[("readonly", C["surface"])], selectforeground=[("readonly", C["text"])])
    style.configure("TSpinbox", padding=(px(root, 8), px(root, 6)), arrowsize=px(root, 18),
                    fieldbackground=C["surface"], background=C["surface"], arrowcolor=C["muted"])
    style.configure("TCheckbutton", padding=(px(root, 4), px(root, 6)), indicatorsize=px(root, 20),
                    indicatorbackground=C["surface"])
    style.map("TCheckbutton", background=[("active", C["bg"])],
              indicatorbackground=[("selected", C["primary"]), ("pressed", C["neutral_press"])],
              indicatorforeground=[("selected", "#FFFFFF")])
    style.configure("Surface.TCheckbutton", background=C["surface"])
    style.map("Surface.TCheckbutton", background=[("active", C["surface"])])
    # große Auswahlpunkte (z. B. Satznummern-Schema)
    style.configure("Big.TRadiobutton", background=C["surface"], font=fonts.button, indicatorsize=px(root, 20),
                    padding=(px(root, 2), px(root, 4)), indicatorbackground=C["surface"])
    style.map("Big.TRadiobutton", background=[("active", C["surface"])],
              indicatorbackground=[("selected", C["primary"]), ("pressed", C["neutral_press"])])

    # Normale ttk-Buttons (in Dialogen)
    for name, bg, hover, press, fg in (
        ("TButton", C["neutral"], C["neutral_hover"], C["neutral_press"], C["text"]),
        ("Primary.TButton", C["primary"], C["primary_hover"], C["primary_press"], "#FFFFFF"),
        ("Danger.TButton", C["danger"], C["danger_hover"], C["danger_press"], "#FFFFFF"),
        ("Success.TButton", C["success"], C["success_hover"], C["success_press"], "#FFFFFF"),
    ):
        border = C["border"] if name == "TButton" else bg
        style.configure(name, background=bg, foreground=fg, bordercolor=border, lightcolor=bg, darkcolor=bg,
                        focuscolor=bg, font=fonts.button, padding=(px(root, 18), px(root, 11)),
                        relief="solid" if name == "TButton" else "flat", borderwidth=1, focusthickness=0)
        style.map(name,
                  background=[("disabled", C["disabled_bg"]), ("pressed", press), ("active", hover)],
                  lightcolor=[("disabled", C["disabled_bg"]), ("pressed", press), ("active", hover)],
                  darkcolor=[("disabled", C["disabled_bg"]), ("pressed", press), ("active", hover)],
                  foreground=[("disabled", C["disabled_text"])])
    style.configure("Small.TButton", font=fonts.ui, padding=(px(root, 10), px(root, 6)))

    style.configure("TNotebook", background=C["bg"], borderwidth=0, tabmargins=(0, 0, 0, 0))
    style.configure("TNotebook.Tab", padding=(px(root, 18), px(root, 10)), font=fonts.ui_bold,
                    background=C["bg"], foreground=C["muted"], bordercolor=C["border"],
                    lightcolor=C["bg"], darkcolor=C["bg"])
    style.map("TNotebook.Tab",
              background=[("selected", C["surface"]), ("active", C["neutral_hover"])],
              foreground=[("selected", C["primary"])],
              lightcolor=[("selected", C["surface"])])

    # Breite Scrollbalken für Finger
    sb = px(root, 22)
    for orient in ("Vertical", "Horizontal"):
        style.configure("{}.TScrollbar".format(orient), arrowsize=sb, width=sb, gripcount=0,
                        background=C["neutral_press"], troughcolor=C["surface_alt"], bordercolor=C["surface_alt"],
                        lightcolor=C["neutral_press"], darkcolor=C["neutral_press"], arrowcolor=C["muted"])
        style.map("{}.TScrollbar".format(orient),
                  background=[("pressed", C["faint"]), ("active", C["border"])])

    style.configure("Treeview", background=C["surface"], fieldbackground=C["surface"], foreground=C["text"],
                    rowheight=px(root, 40), font=fonts.list, borderwidth=0, relief="flat")
    style.map("Treeview", background=[("selected", C["select"])], foreground=[("selected", C["text"])])
    style.configure("Treeview.Heading", font=fonts.small_bold, background=C["surface_alt"], foreground=C["muted"],
                    padding=(px(root, 8), px(root, 6)), relief="flat", bordercolor=C["border"])
    style.map("Treeview.Heading", background=[("active", C["neutral_hover"])])
    style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])

    style.configure("Transfer.Horizontal.TProgressbar", thickness=px(root, 18), troughcolor=C["neutral_press"],
                    background=C["primary"], bordercolor=C["neutral_press"], lightcolor=C["primary"],
                    darkcolor=C["primary"])
    style.configure("Receive.Horizontal.TProgressbar", thickness=px(root, 18), troughcolor=C["neutral_press"],
                    background=C["success"], bordercolor=C["neutral_press"], lightcolor=C["success"],
                    darkcolor=C["success"])
    style.configure("TSeparator", background=C["border"])
    return style
