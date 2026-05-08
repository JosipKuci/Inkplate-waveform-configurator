#!/usr/bin/env python3
"""
Inkplate Waveform Editor
========================
GUI tool for creating, editing and sending custom EPD waveforms to
Inkplate e-paper displays running the Inkplate_Custom_Waveform sketch.

Controls:
  Left-click      : cycle cell value  0 → 1 → 2 → 0
  Right-click     : set exact value via context menu
  +Ph / −Ph       : add / remove a phase column  (max 16)
  +Color / −Color : add / remove a grayscale-level row  (max 16)

Values:  0 = discharge  |  1 = black drive  |  2 = white drive

Serial protocol (newline-terminated):
  TS;<colors>;<phases>;<v00>;<v01>;...;<vN>;TE

Dependencies:
  pip install -r requirements.txt          (customtkinter + pyserial)
"""

import contextlib
import copy
import io
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox

try:
    import customtkinter as ctk
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
except ImportError:
    raise SystemExit(
        "customtkinter not found.\n"
        "Install with:  pip install customtkinter\n"
        "or:            pip install -r requirements.txt"
    )

try:
    import serial
    import serial.tools.list_ports
    _SERIAL_OK = True
except ImportError:
    _SERIAL_OK = False

try:
    import esptool
    _ESPTOOL_OK = True
except ImportError:
    _ESPTOOL_OK = False

# ──────────────────────────────────────────────────────────────────────────────
# Waveform templates
# ──────────────────────────────────────────────────────────────────────────────
TEMPLATES: dict[str, list[list[int]]] = {
    "Empty (8×9)":        [[0] * 9  for _ in range(8)],
    "Empty 4-bit (16×9)": [[0] * 9  for _ in range(16)],
    "Inkplate4TEMPERA": [
        [0, 0, 1, 1, 1, 1, 1, 0, 0],
        [1, 1, 1, 2, 1, 1, 0, 0, 0],
        [2, 1, 1, 0, 2, 1, 1, 0, 0],
        [0, 0, 0, 1, 1, 1, 2, 0, 0],
        [2, 1, 1, 2, 1, 1, 2, 0, 0],
        [1, 2, 1, 1, 2, 1, 2, 0, 0],
        [1, 1, 1, 2, 1, 2, 2, 0, 0],
        [0, 0, 0, 0, 0, 2, 2, 0, 0],
    ],
    "Inkplate5": [
        [0, 0, 1, 1, 0, 1, 1, 1, 0],
        [0, 1, 1, 1, 1, 2, 0, 1, 0],
        [1, 2, 2, 0, 2, 1, 1, 1, 0],
        [1, 1, 1, 2, 0, 1, 1, 2, 0],
        [0, 1, 1, 1, 2, 0, 1, 2, 0],
        [0, 0, 0, 1, 1, 2, 1, 2, 0],
        [1, 1, 1, 2, 0, 2, 1, 2, 0],
        [0, 0, 0, 0, 0, 0, 0, 0, 0],
    ],
    "Inkplate5V2": [
        [0, 0, 1, 1, 2, 1, 1, 1, 0],
        [1, 1, 2, 2, 1, 2, 1, 1, 0],
        [0, 1, 2, 2, 1, 1, 2, 1, 0],
        [0, 0, 1, 1, 1, 1, 1, 2, 0],
        [1, 2, 1, 2, 1, 1, 1, 2, 0],
        [0, 1, 1, 1, 2, 0, 1, 2, 0],
        [1, 1, 1, 2, 2, 2, 1, 2, 0],
        [0, 0, 0, 0, 0, 0, 0, 0, 0],
    ],
    "Inkplate6 / 6V2": [
        [0, 0, 0, 0, 1, 1, 1, 1, 0],
        [0, 0, 0, 1, 1, 1, 1, 0, 0],
        [1, 1, 1, 1, 0, 2, 1, 0, 0],
        [1, 1, 1, 2, 2, 1, 1, 0, 0],
        [1, 1, 1, 1, 2, 2, 1, 0, 0],
        [0, 1, 1, 1, 2, 2, 1, 0, 0],
        [0, 0, 0, 0, 1, 1, 2, 0, 0],
        [0, 0, 0, 0, 0, 0, 2, 0, 0],
    ],
    "Inkplate6FLICK": [
        [0, 0, 0, 0, 0, 1, 1, 1, 0],
        [0, 0, 1, 2, 1, 1, 2, 1, 0],
        [0, 1, 1, 2, 1, 1, 1, 2, 0],
        [1, 1, 1, 2, 2, 1, 1, 2, 0],
        [1, 1, 1, 2, 1, 2, 1, 2, 0],
        [0, 1, 1, 2, 1, 2, 1, 2, 0],
        [1, 2, 1, 1, 2, 2, 1, 2, 0],
        [0, 0, 0, 0, 0, 0, 0, 2, 0],
    ],
    "Inkplate6PLUS / 6PLUSV2": [
        [0, 0, 0, 0, 0, 2, 1, 1, 0],
        [0, 0, 2, 1, 1, 1, 2, 1, 0],
        [0, 2, 2, 2, 1, 1, 2, 1, 0],
        [0, 0, 2, 2, 2, 1, 2, 1, 0],
        [0, 0, 0, 0, 2, 2, 2, 1, 0],
        [0, 0, 2, 1, 2, 1, 1, 2, 0],
        [0, 0, 2, 2, 2, 1, 1, 2, 0],
        [0, 0, 0, 0, 2, 2, 2, 2, 0],
    ],
    "Inkplate10 / 10V2": [
        [0, 0, 0, 0, 0, 0, 0, 1, 0],
        [0, 0, 0, 2, 2, 2, 1, 1, 0],
        [0, 0, 2, 1, 1, 2, 2, 1, 0],
        [0, 1, 2, 2, 1, 2, 2, 1, 0],
        [0, 0, 2, 1, 2, 2, 2, 1, 0],
        [0, 2, 2, 2, 2, 2, 2, 1, 0],
        [0, 0, 0, 0, 0, 2, 1, 2, 0],
        [0, 0, 0, 2, 2, 2, 2, 2, 0],
    ],
}

# ── cell style ────────────────────────────────────────────────────────────────
_STYLE = {
    0: {"bg": "#888888", "fg": "#111111", "label": "0\nDIS"},
    1: {"bg": "#1e1e1e", "fg": "#f0f0f0", "label": "1\nBLK"},
    2: {"bg": "#e8e8e8", "fg": "#111111", "label": "2\nWHT"},
}

# Dark theme palette
_BG  = "#1c1c1c"
_FG  = "#d0d0d0"
_BG2 = "#2b2b2b"
_BG3 = "#333333"

# Grid drawing constants
_LABEL_W  = 92    # row-label column width (px)
_HEADER_H = 26    # phase-header row height (px)
_PAD      = 3     # gap between cells (px)
_MIN_CW   = 38    # minimum cell width
_MIN_CH   = 30    # minimum cell height


# ──────────────────────────────────────────────────────────────────────────────
class WaveformEditor(ctk.CTk):
    """Main application window."""

    def __init__(self) -> None:
        super().__init__()
        self.title("Inkplate Waveform Editor")
        self.geometry("1000x780")
        self.minsize(700, 520)
        self.configure(fg_color=_BG)

        self._wf: list[list[int]] = copy.deepcopy(TEMPLATES["Inkplate10 / 10V2"])

        self._tpl_var   = tk.StringVar(value="Inkplate10 / 10V2")
        self._port_var  = tk.StringVar()
        self._baud_var  = tk.StringVar(value="115200")
        self._status    = tk.StringVar(value="Ready.")
        self._grid_info = tk.StringVar(value="")

        self._cell_w: int = _MIN_CW
        self._cell_h: int = _MIN_CH
        self._resize_job = None

        self._paned: tk.PanedWindow | None = None  # set in _build_ui
        self._build_ui()
        self._refresh_ports()
        # After window maps: set initial sash position then redraw grid
        self.after(120, self._init_sash_and_redraw)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:

        # ── toolbar ───────────────────────────────────────────────────────────
        toolbar = ctk.CTkFrame(self, corner_radius=10, fg_color=_BG2)
        toolbar.pack(fill=tk.X, padx=10, pady=(10, 4))

        row1 = ctk.CTkFrame(toolbar, fg_color="transparent")
        row1.pack(fill=tk.X, padx=10, pady=(8, 2))
        ctk.CTkLabel(row1, text="Template", width=72, anchor="w",
                     font=ctk.CTkFont(size=12)).pack(side=tk.LEFT)
        ctk.CTkComboBox(
            row1, variable=self._tpl_var, values=list(TEMPLATES.keys()),
            width=230, state="readonly", font=ctk.CTkFont(size=12),
        ).pack(side=tk.LEFT, padx=(0, 8))
        ctk.CTkButton(row1, text="Load", width=70, height=30,
                      command=self._load_template).pack(side=tk.LEFT)

        row2 = ctk.CTkFrame(toolbar, fg_color="transparent")
        row2.pack(fill=tk.X, padx=10, pady=(2, 8))
        ctk.CTkLabel(row2, text="Port", width=40, anchor="w",
                     font=ctk.CTkFont(size=12)).pack(side=tk.LEFT)
        self._port_combo = ctk.CTkComboBox(
            row2, variable=self._port_var, values=[],
            width=160, state="readonly", font=ctk.CTkFont(size=12),
        )
        self._port_combo.pack(side=tk.LEFT, padx=(0, 4))
        ctk.CTkButton(row2, text="↻", width=34, height=30,
                      command=self._refresh_ports,
                      font=ctk.CTkFont(size=14)).pack(side=tk.LEFT, padx=(0, 12))
        ctk.CTkLabel(row2, text="Baud", anchor="w",
                     font=ctk.CTkFont(size=12)).pack(side=tk.LEFT)
        ctk.CTkComboBox(
            row2, variable=self._baud_var,
            values=["9600", "57600", "115200", "230400"],
            width=110, state="readonly", font=ctk.CTkFont(size=12),
        ).pack(side=tk.LEFT, padx=(4, 0))

        state = "normal" if _SERIAL_OK else "disabled"
        ctk.CTkButton(row2, text="▶  Send to Device", command=self._send,
                      state=state, width=160, height=30,
                      font=ctk.CTkFont(size=12, weight="bold"),
                      ).pack(side=tk.RIGHT, padx=(6, 0))
        ctk.CTkButton(row2, text="💾  Export .h", command=self._export_h,
                      width=130, height=30, font=ctk.CTkFont(size=12),
                      fg_color=_BG3, hover_color="#444",
                      ).pack(side=tk.RIGHT, padx=(0, 4))
        if not _SERIAL_OK:
            ctk.CTkLabel(row2, text="pyserial not installed",
                         text_color="#ff6b6b",
                         font=ctk.CTkFont(size=11)).pack(side=tk.RIGHT, padx=8)

        row3 = ctk.CTkFrame(toolbar, fg_color="transparent")
        row3.pack(fill=tk.X, padx=10, pady=(2, 8))
        self._upload_btn = ctk.CTkButton(
            row3, text="⬆  Upload Firmware", command=self._upload_firmware,
            width=170, height=30,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#2e6e2e", hover_color="#3a8a3a",
        )
        self._upload_btn.pack(side=tk.LEFT)
        ctk.CTkLabel(row3,
                     text="Firmware auto-selected from template  (./firmware/<board>/firmware.bin)",
                     text_color="#555555", font=ctk.CTkFont(size=10),
                     anchor="w").pack(side=tk.LEFT, padx=(14, 0))

        # ── status + legend (packed at bottom so paned window fills the rest) ──
        bot = ctk.CTkFrame(self, corner_radius=10, fg_color=_BG2)
        bot.pack(fill=tk.X, padx=10, pady=(0, 10), side=tk.BOTTOM)

        leg = ctk.CTkFrame(bot, fg_color="transparent")
        leg.pack(side=tk.LEFT, padx=10, pady=6)
        ctk.CTkLabel(leg, text="Legend:", font=ctk.CTkFont(size=11),
                     anchor="w").pack(side=tk.LEFT, padx=(0, 8))
        for val, st in _STYLE.items():
            lbl = {0: "Discharge", 1: "Black drive", 2: "White drive"}[val]
            tk.Label(leg, text=f"  {val}  {lbl}  ",
                     bg=st["bg"], fg=st["fg"],
                     relief=tk.RAISED, font=("Courier", 9, "bold"),
                     padx=4).pack(side=tk.LEFT, padx=3)

        ctk.CTkLabel(bot, textvariable=self._status, anchor="e",
                     font=ctk.CTkFont(size=11),
                     text_color="#aaaaaa").pack(side=tk.RIGHT, padx=14, pady=6)

        # ── paned area (grid top, monitor bottom, draggable sash) ────────────
        self._paned = tk.PanedWindow(self, orient=tk.VERTICAL,
                                    bg="#555555", sashwidth=6, sashpad=2,
                                    sashrelief=tk.FLAT, opaqueresize=True)
        self._paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=4)
        paned = self._paned

        # ── grid area ─────────────────────────────────────────────────────────
        grid_outer = ctk.CTkFrame(paned, corner_radius=10, fg_color=_BG2)
        paned.add(grid_outer, minsize=150, stretch="always")

        ctk.CTkLabel(
            grid_outer,
            text="Waveform Grid  —  left-click: cycle 0→1→2   right-click: set value",
            anchor="w", text_color="#888888", font=ctk.CTkFont(size=11),
        ).pack(fill=tk.X, padx=12, pady=(8, 2))

        # Canvas with scrollbars (scrollbars activate only when min cell size is hit)
        cf = tk.Frame(grid_outer, bg=_BG2, bd=0)
        cf.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 4))
        cf.rowconfigure(0, weight=1)
        cf.columnconfigure(0, weight=1)

        self._canvas = tk.Canvas(cf, bg=_BG, highlightthickness=0)
        vsb = tk.Scrollbar(cf, orient=tk.VERTICAL,   command=self._canvas.yview,
                           bg=_BG2, troughcolor=_BG, relief=tk.FLAT, bd=0)
        hsb = tk.Scrollbar(cf, orient=tk.HORIZONTAL, command=self._canvas.xview,
                           bg=_BG2, troughcolor=_BG, relief=tk.FLAT, bd=0)
        self._canvas.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self._canvas.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        self._canvas.bind("<Button-1>",        self._on_canvas_click)
        self._canvas.bind("<Button-3>",        self._on_canvas_rclick)
        self._canvas.bind("<Configure>",       self._on_canvas_configure)
        self._canvas.bind("<MouseWheel>",
                          lambda e: self._canvas.yview_scroll(-1 * (e.delta // 120), "units"))
        self._canvas.bind("<Shift-MouseWheel>",
                          lambda e: self._canvas.xview_scroll(-1 * (e.delta // 120), "units"))

        # Grid controls row
        ctrl = ctk.CTkFrame(grid_outer, fg_color="transparent")
        ctrl.pack(fill=tk.X, padx=8, pady=(0, 8))

        ctk.CTkLabel(ctrl, textvariable=self._grid_info, anchor="w",
                     text_color="#666666",
                     font=ctk.CTkFont(size=10)).pack(side=tk.LEFT, padx=(0, 20))

        ctk.CTkLabel(ctrl, text="Phases:", font=ctk.CTkFont(size=11),
                     anchor="w").pack(side=tk.LEFT, padx=(0, 4))
        ctk.CTkButton(ctrl, text="+Ph", width=50, height=26,
                      command=self._add_phase,
                      font=ctk.CTkFont(size=10)).pack(side=tk.LEFT, padx=2)
        ctk.CTkButton(ctrl, text="−Ph", width=50, height=26,
                      command=self._remove_phase,
                      fg_color=_BG3, hover_color="#444",
                      font=ctk.CTkFont(size=10)).pack(side=tk.LEFT, padx=2)

        ctk.CTkLabel(ctrl, text="Colors:", font=ctk.CTkFont(size=11),
                     anchor="w").pack(side=tk.LEFT, padx=(16, 4))
        ctk.CTkButton(ctrl, text="+Color", width=66, height=26,
                      command=self._add_color,
                      font=ctk.CTkFont(size=10)).pack(side=tk.LEFT, padx=2)
        ctk.CTkButton(ctrl, text="−Color", width=66, height=26,
                      command=self._remove_color,
                      fg_color=_BG3, hover_color="#444",
                      font=ctk.CTkFont(size=10)).pack(side=tk.LEFT, padx=2)

        # ── serial monitor ────────────────────────────────────────────────────
        mon_outer = ctk.CTkFrame(paned, corner_radius=10, fg_color=_BG2)
        paned.add(mon_outer, minsize=60, stretch="always")

        mon_hdr = ctk.CTkFrame(mon_outer, fg_color="transparent")
        mon_hdr.pack(fill=tk.X, padx=10, pady=(6, 2))
        ctk.CTkLabel(mon_hdr, text="Serial Monitor",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     anchor="w").pack(side=tk.LEFT)
        ctk.CTkButton(mon_hdr, text="Clear", width=60, height=26,
                      command=self._monitor_clear,
                      fg_color=_BG3, hover_color="#444").pack(side=tk.RIGHT)

        self._monitor = ctk.CTkTextbox(
            mon_outer, state="disabled",
            font=ctk.CTkFont(family="Courier", size=10),
            fg_color=_BG, text_color=_FG,
            activate_scrollbars=True, corner_radius=6,
        )
        self._monitor.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 8))
        self._monitor._textbox.tag_configure("TX",   foreground="#4fc3f7")
        self._monitor._textbox.tag_configure("RX",   foreground="#81c784")
        self._monitor._textbox.tag_configure("ERR",  foreground="#ef5350")
        self._monitor._textbox.tag_configure("INFO", foreground="#9e9e9e")

    # ── canvas-based grid ─────────────────────────────────────────────────────

    def _init_sash_and_redraw(self) -> None:
        """Place sash so monitor starts at ~160 px tall, then draw grid."""
        h = self._paned.winfo_height()
        if h > 300:
            self._paned.sash_place(0, 0, h - 160)
        self._redraw()

    def _on_canvas_configure(self, _event=None) -> None:
        """Debounce canvas resize → redraw."""
        if self._resize_job is not None:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(60, self._redraw)

    def _redraw(self, *_) -> None:
        """Draw the entire waveform grid onto the canvas, scaled to fit."""
        self._resize_job = None
        self._canvas.delete("all")

        nc  = len(self._wf)
        np_ = len(self._wf[0]) if nc else 0
        if nc == 0 or np_ == 0:
            return

        cw = max(200, self._canvas.winfo_width())
        ch = max(150, self._canvas.winfo_height())

        # Cell size: expand to fill, but enforce minimum so scroll kicks in
        avail_w = cw - _LABEL_W - _PAD * (np_ + 2)
        avail_h = ch - _HEADER_H - _PAD * (nc + 2)
        cell_w  = max(_MIN_CW, avail_w // max(1, np_))
        cell_h  = max(_MIN_CH, avail_h // max(1, nc))
        self._cell_w = cell_w
        self._cell_h = cell_h

        # Font sizes scale with cell
        hdr_fs  = max(8,  min(12, cell_w  // 5))
        cell_fs = max(8,  min(12, min(cell_w // 6, cell_h // 4)))
        row_fs  = max(8,  min(11, cell_h  // 4))

        # ── Phase headers ────────────────────────────────────────────────────
        for p in range(np_):
            cx = _LABEL_W + _PAD + p * (cell_w + _PAD) + cell_w // 2
            self._canvas.create_text(
                cx, _HEADER_H // 2,
                text=f"Ph {p}", fill="#888888",
                font=("TkDefaultFont", hdr_fs),
            )

        # ── Rows ─────────────────────────────────────────────────────────────
        for r in range(nc):
            y1 = _HEADER_H + _PAD + r * (cell_h + _PAD)
            y2 = y1 + cell_h
            cy = (y1 + y2) // 2

            # Row label
            self._canvas.create_text(
                _LABEL_W - _PAD * 2, cy,
                text=f"C{r}  (g{r})", anchor="e",
                fill="#cccccc", font=("TkDefaultFont", row_fs),
            )

            # Cells
            for c in range(np_):
                x1  = _LABEL_W + _PAD + c * (cell_w + _PAD)
                x2  = x1 + cell_w
                val = self._wf[r][c]
                st  = _STYLE[val]
                tag = f"cell_{r}_{c}"

                self._canvas.create_rectangle(
                    x1, y1, x2, y2,
                    fill=st["bg"], outline="#444444", width=1,
                    tags=(tag, "cell"),
                )

                lbl = st["label"] if (cell_h >= 38 and cell_w >= 44) else str(val)
                self._canvas.create_text(
                    (x1 + x2) // 2, cy,
                    text=lbl, fill=st["fg"],
                    font=("Courier", cell_fs, "bold"),
                    tags=(tag, "cell"),
                )

        # ── Scrollregion ─────────────────────────────────────────────────────
        total_w = _LABEL_W + _PAD + np_ * (cell_w + _PAD) + _PAD
        total_h = _HEADER_H + _PAD + nc * (cell_h + _PAD) + _PAD
        self._canvas.configure(scrollregion=(0, 0, total_w, total_h))
        self._grid_info.set(f"{nc} colors × {np_} phases")

    def _canvas_coords_to_cell(self, x: float, y: float) -> tuple[int | None, int | None]:
        nc  = len(self._wf)
        np_ = len(self._wf[0]) if nc else 0
        cw  = self._cell_w
        ch  = self._cell_h

        gx = x - _LABEL_W - _PAD
        gy = y - _HEADER_H - _PAD
        if gx < 0 or gy < 0:
            return None, None

        col = int(gx // (cw + _PAD))
        row = int(gy // (ch + _PAD))
        if not (0 <= row < nc and 0 <= col < np_):
            return None, None

        # Reject clicks that land on the padding gap
        if (gx - col * (cw + _PAD)) > cw or (gy - row * (ch + _PAD)) > ch:
            return None, None

        return row, col

    def _on_canvas_click(self, event: tk.Event) -> None:
        x = self._canvas.canvasx(event.x)
        y = self._canvas.canvasy(event.y)
        r, c = self._canvas_coords_to_cell(x, y)
        if r is not None:
            self._left_click(r, c)

    def _on_canvas_rclick(self, event: tk.Event) -> None:
        x = self._canvas.canvasx(event.x)
        y = self._canvas.canvasy(event.y)
        r, c = self._canvas_coords_to_cell(x, y)
        if r is not None:
            self._right_click(event, r, c)

    # ── cell interaction ──────────────────────────────────────────────────────

    def _left_click(self, row: int, col: int) -> None:
        self._set_cell(row, col, (self._wf[row][col] + 1) % 3)

    def _right_click(self, event: tk.Event, row: int, col: int) -> None:
        menu = tk.Menu(self, tearoff=0, bg=_BG2, fg=_FG,
                       activebackground="#3a3a3a", activeforeground=_FG,
                       relief=tk.FLAT, bd=1)
        for v in (0, 1, 2):
            st  = _STYLE[v]
            lbl = {0: "0  —  Discharge",
                   1: "1  —  Black drive",
                   2: "2  —  White drive"}[v]
            menu.add_command(
                label=lbl,
                command=lambda val=v: self._set_cell(row, col, val),
                background=st["bg"], foreground=st["fg"],
            )
        menu.tk_popup(event.x_root, event.y_root)

    def _set_cell(self, row: int, col: int, val: int) -> None:
        self._wf[row][col] = val
        st   = _STYLE[val]
        tag  = f"cell_{row}_{col}"
        show_label = self._cell_h >= 38 and self._cell_w >= 44
        lbl  = st["label"] if show_label else str(val)
        for item in self._canvas.find_withtag(tag):
            if self._canvas.type(item) == "rectangle":
                self._canvas.itemconfig(item, fill=st["bg"])
            elif self._canvas.type(item) == "text":
                self._canvas.itemconfig(item, text=lbl, fill=st["fg"])

    # ── structure ─────────────────────────────────────────────────────────────

    def _add_phase(self) -> None:
        if len(self._wf[0]) >= 16:
            messagebox.showwarning("Max phases", "Maximum 16 phases.", parent=self)
            return
        for row in self._wf:
            row.append(0)
        self._redraw()
        self._set_status(f"Phase added — {len(self._wf[0])} phases.")

    def _remove_phase(self) -> None:
        if len(self._wf[0]) <= 1:
            messagebox.showwarning("Min phases", "Minimum 1 phase required.", parent=self)
            return
        for row in self._wf:
            row.pop()
        self._redraw()
        self._set_status(f"Phase removed — {len(self._wf[0])} phases.")

    def _add_color(self) -> None:
        if len(self._wf) >= 16:
            messagebox.showwarning("Max colors", "Maximum 16 color levels.", parent=self)
            return
        self._wf.append([0] * len(self._wf[0]))
        self._redraw()
        self._set_status(f"Color level added — {len(self._wf)} colors.")

    def _remove_color(self) -> None:
        if len(self._wf) <= 1:
            messagebox.showwarning("Min colors", "Minimum 1 color level required.", parent=self)
            return
        self._wf.pop()
        self._redraw()
        self._set_status(f"Color level removed — {len(self._wf)} colors.")

    def _load_template(self) -> None:
        name = self._tpl_var.get()
        self._wf = copy.deepcopy(TEMPLATES[name])
        self._redraw()
        nc  = len(self._wf)
        np_ = len(self._wf[0])
        self._set_status(f'Loaded "{name}"  ({nc} colors × {np_} phases)')

    # ── serial ────────────────────────────────────────────────────────────────

    def _refresh_ports(self) -> None:
        if not _SERIAL_OK:
            self._set_status("pyserial not installed — serial features disabled.")
            return
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self._port_combo.configure(values=ports)
        if ports:
            if self._port_var.get() not in ports:
                self._port_combo.set(ports[0])
            self._set_status(f"Found {len(ports)} port(s).")
        else:
            self._port_combo.set("")
            self._set_status("No serial ports found.")

    def _build_uart_string(self) -> str:
        nc   = len(self._wf)
        np_  = len(self._wf[0])
        parts = ["TS", str(nc), str(np_)]
        for row in self._wf:
            parts.extend(str(v) for v in row)
        parts.append("TE")
        return ";".join(parts) + "\n"

    def _send(self) -> None:
        port = self._port_var.get()
        if not port:
            messagebox.showerror("No port", "Select a serial port first.", parent=self)
            return
        baud = int(self._baud_var.get())
        msg  = self._build_uart_string()
        try:
            ser = serial.Serial(port, baud, timeout=0.1)
            ser.reset_input_buffer()
            ser.write(msg.encode("ascii"))
            ser.flush()
            self._log("TX", msg.rstrip("\n"))
            self._set_status(f"Sent {len(msg) - 1} chars to {port} @ {baud} baud.")
            threading.Thread(target=self._rx_reader,
                             args=(ser, 4.0), daemon=True).start()
        except serial.SerialException as exc:
            messagebox.showerror("Serial error", str(exc), parent=self)
            self._set_status(f"Send failed: {exc}")
            self._log("ERR", str(exc))

    # ── export ────────────────────────────────────────────────────────────────

    def _export_h(self) -> None:
        nc  = len(self._wf)
        np_ = len(self._wf[0])

        safe = (self._tpl_var.get()
                .replace(" ", "_").replace("/", "_").replace("×", "x")
                .replace(".", "").strip("_").lower())
        filepath = filedialog.asksaveasfilename(
            title="Export waveform as C header",
            defaultextension=".h",
            filetypes=[("C header", "*.h"), ("All files", "*.*")],
            initialfile=f"waveform_{safe}.h",
            parent=self,
        )
        if not filepath:
            return

        base  = os.path.basename(filepath)
        guard = base.upper().replace(".", "_").replace("-", "_").replace(" ", "_")
        ts    = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        ph_hdr = "   ".join(str(p) for p in range(np_))
        rows   = []
        for r, row in enumerate(self._wf):
            vals  = ", ".join(str(v) for v in row)
            comma = "," if r < nc - 1 else " "
            rows.append(f"    /* C{r} gray{r} */  {{ {vals} }}{comma}")

        content = (
            f"#ifndef {guard}\n#define {guard}\n\n"
            f"// Inkplate Waveform Editor — exported header\n"
            f"// Template  : {self._tpl_var.get()}\n"
            f"// Generated : {ts}\n"
            f"// Colors    : {nc}   Phases: {np_}\n"
            f"//   0=discharge  1=black drive  2=white drive\n\n"
            f"#define WAVEFORM_COLORS {nc}\n"
            f"#define WAVEFORM_PHASES {np_}\n\n"
            f"static const uint8_t customWaveform[WAVEFORM_COLORS][WAVEFORM_PHASES] = {{\n"
            f"    // Ph: {ph_hdr}\n"
            + "\n".join(rows) + "\n"
            f"}};\n\n#endif // {guard}\n"
        )

        try:
            with open(filepath, "w", encoding="utf-8") as fh:
                fh.write(content)
            self._set_status(f"Exported → {base}  ({nc} colors × {np_} phases)")
        except OSError as exc:
            messagebox.showerror("Export failed", str(exc), parent=self)

    # ── serial monitor ────────────────────────────────────────────────────────

    def _log(self, tag: str, text: str) -> None:
        ts     = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        prefix = {"TX": "→ TX", "RX": "← RX", "ERR": "!! ERR", "INFO": "   "}.get(tag, tag)
        line   = f"[{ts}] {prefix}  {text}\n"
        self._monitor.configure(state="normal")
        self._monitor._textbox.insert(tk.END, line, tag)
        self._monitor._textbox.see(tk.END)
        self._monitor.configure(state="disabled")

    def _monitor_clear(self) -> None:
        self._monitor.configure(state="normal")
        self._monitor.delete("0.0", tk.END)
        self._monitor.configure(state="disabled")

    def _rx_reader(self, ser: "serial.Serial", timeout_s: float = 4.0) -> None:
        deadline = time.monotonic() + timeout_s
        try:
            while time.monotonic() < deadline:
                try:
                    raw = ser.readline()
                except serial.SerialException as exc:
                    self.after(0, lambda e=str(exc): self._log("ERR", e))
                    break
                if raw:
                    line = raw.decode("ascii", errors="replace").rstrip("\r\n")
                    if line:
                        self.after(0, lambda ln=line: self._log("RX", ln))
                        deadline = time.monotonic() + timeout_s
        finally:
            try:
                ser.close()
            except Exception:
                pass
            self.after(0, lambda: self._log("INFO", "Port closed."))

    # ── firmware upload ───────────────────────────────────────────────────────

    @staticmethod
    def _fw_dir() -> Path:
        # When frozen by PyInstaller (--onefile), files land in sys._MEIPASS
        base = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
        return base / "firmware"

    def _fw_board_for_template(self) -> str | None:
        """Return firmware folder name that best matches the selected template."""
        import re
        def norm(s: str) -> str:
            return re.sub(r'[^a-z0-9]', '', s.lower())

        tpl = norm(self._tpl_var.get())
        fw_dir = self._fw_dir()
        if not fw_dir.is_dir():
            return None
        best, best_len = None, 0
        for p in fw_dir.iterdir():
            if not p.is_dir():
                continue
            key = norm(p.name)
            if key and tpl.startswith(key) and len(key) > best_len:
                best, best_len = p.name, len(key)
        return best

    def _upload_firmware(self) -> None:
        if not _ESPTOOL_OK:
            messagebox.showerror("esptool missing",
                                 "esptool not installed.\n"
                                 "Install with:  pip install esptool", parent=self)
            return

        port = self._port_var.get()
        if not port:
            messagebox.showerror("No port", "Select a serial port first.", parent=self)
            return

        board = self._fw_board_for_template()
        if not board:
            messagebox.showerror("No firmware",
                                 f'No firmware folder matches template "{self._tpl_var.get()}".\n'
                                 "Place firmware.bin in ./firmware/<board>/", parent=self)
            return

        fw_bin = self._fw_dir() / board / "firmware.bin"
        if not fw_bin.exists():
            messagebox.showerror("Missing file",
                                 f"Not found: firmware/{board}/firmware.bin", parent=self)
            return

        args = [
            "--chip", "esp32",
            "--port", port,
            "--baud", "460800",
            "--before", "default-reset",
            "--after", "hard-reset",
            "write-flash",
            "-z",
            "--flash-mode", "keep",
            "--flash-freq", "keep",
            "--flash-size", "keep",
            "0x0", str(fw_bin),
        ]
        self._upload_btn.configure(state="disabled", text="Uploading…")
        self._log("INFO", f"esptool {' '.join(args)}")
        threading.Thread(target=self._upload_thread, args=(args,), daemon=True).start()

    def _upload_thread(self, args: list[str]) -> None:
        class _LineWriter(io.RawIOBase):
            """Wraps a line callback as a writable text stream."""
            def __init__(self, cb):
                self._cb = cb
                self._buf = ""
            def write(self, s):
                if isinstance(s, bytes):
                    s = s.decode("utf-8", errors="replace")
                self._buf += s
                while "\n" in self._buf:
                    line, self._buf = self._buf.split("\n", 1)
                    if line.strip():
                        self._cb(line)
                return len(s)
            def flush(self): pass
            # make it look like a text stream
            @property
            def encoding(self): return "utf-8"
            @property
            def errors(self): return "replace"

        def on_line(line: str) -> None:
            ll = line.lower()
            tag = "ERR" if any(w in ll for w in ("error", "failed", "invalid")) and "warning" not in ll else "RX"
            self.after(0, lambda l=line, t=tag: self._log(t, l))

        writer = _LineWriter(on_line)
        try:
            with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
                esptool.main(args)
            self.after(0, lambda: self._log("INFO", "Upload complete."))
            self.after(0, lambda: self._set_status("Upload complete."))
        except SystemExit as exc:
            if exc.code not in (None, 0):
                self.after(0, lambda: self._log("ERR", f"Upload failed (exit {exc.code})."))
                self.after(0, lambda: self._set_status("Upload failed."))
            else:
                self.after(0, lambda: self._log("INFO", "Upload complete."))
                self.after(0, lambda: self._set_status("Upload complete."))
        except Exception as exc:
            self.after(0, lambda e=str(exc): self._log("ERR", e))
            self.after(0, lambda: self._set_status("Upload error."))
        finally:
            self.after(0, lambda: self._upload_btn.configure(state="normal", text="⬆  Upload Firmware"))

    # ── helpers ───────────────────────────────────────────────────────────────

    def _set_status(self, msg: str) -> None:
        self._status.set(msg)


# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = WaveformEditor()
    app.mainloop()
