"""Plot pane: the last (or a reopened) shot, one subplot per quantity.

Long traces are min/max decimated for drawing; zooming re-decimates the visible
range so detail appears as you zoom in.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import matplotlib
import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

from ..config import QUANTITIES, SAMPLE_RATE, SKIP_SAMPLES, parse_channels
from ..data import Shot

DRAW_POINTS = 3000          # min/max pairs per line on screen


def decimate(x: np.ndarray, y: np.ndarray, npts: int = DRAW_POINTS):
    """Min/max decimation that keeps spikes visible."""
    n = y.size
    if n <= 2 * npts:
        return x, y
    step = n // npts
    m = step * npts
    yb = y[:m].reshape(npts, step)
    xb = x[:m].reshape(npts, step)
    imin, imax = yb.argmin(axis=1), yb.argmax(axis=1)
    rows = np.arange(npts)
    first = np.minimum(imin, imax)
    second = np.maximum(imin, imax)
    xs = np.column_stack([xb[rows, first], xb[rows, second]]).ravel()
    ys = np.column_stack([yb[rows, first], yb[rows, second]]).ravel()
    return xs, ys


class PlotPanel(ttk.Frame):
    def __init__(self, master, default_quantity: str = "PWR"):
        super().__init__(master)
        self.shots: list[Shot] = []
        self._lines = []                 # (line, x_full, y_full)
        self._updating = False

        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=4, pady=2)
        ttk.Label(bar, text="Show:").pack(side="left")
        self.qty_vars: dict[str, tk.BooleanVar] = {}
        self.qty_buttons: dict[str, ttk.Checkbutton] = {}
        for q in QUANTITIES:
            v = tk.BooleanVar(value=(q == default_quantity))
            self.qty_vars[q] = v
            b = ttk.Checkbutton(bar, text=q, variable=v, command=self.redraw)
            b.pack(side="left", padx=2)
            self.qty_buttons[q] = b
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6)
        ttk.Label(bar, text="Channels:").pack(side="left")
        self.chan_var = tk.StringVar(value="all")
        e = ttk.Entry(bar, textvariable=self.chan_var, width=10)
        e.pack(side="left")
        e.bind("<Return>", lambda _e: self.redraw())
        self.skip_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text=f"skip first {SKIP_SAMPLES}", variable=self.skip_var,
                        command=self.redraw).pack(side="left", padx=6)
        self.secs_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="time in s", variable=self.secs_var,
                        command=self.redraw).pack(side="left")
        self.title = ttk.Label(bar, text="", foreground="#555")
        self.title.pack(side="right")

        self.fig = Figure(figsize=(8, 6), dpi=100, layout="constrained")
        self.canvas = FigureCanvasTkAgg(self.fig, master=self)
        toolbar = NavigationToolbar2Tk(self.canvas, self, pack_toolbar=False)
        toolbar.update()
        toolbar.pack(side="bottom", fill="x")
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        self._empty()

    def _empty(self) -> None:
        self.fig.clear()
        ax = self.fig.add_subplot()
        ax.text(0.5, 0.5, "No data yet: Capture, or File ▸ Open shot…",
                ha="center", va="center", transform=ax.transAxes, color="#888")
        ax.set_axis_off()
        self.canvas.draw_idle()

    def show(self, shots: list[Shot]) -> None:
        self.shots = shots
        modes = {s.mode for s in shots}
        avail = {q for s in shots for q in s.quantities}
        for q, b in self.qty_buttons.items():
            b.state(["!disabled"] if q in avail else ["disabled"])
        if not any(self.qty_vars[q].get() for q in avail):
            first = "PWR" if "PWR" in avail else ("MAG" if "MAG" in avail else sorted(avail)[0])
            self.qty_vars[first].set(True)
        if shots:
            s0 = shots[0]
            src = {"external": " · started from panel/external", "file": " · from file"}.get(s0.source, "")
            self.title.configure(text=f"{'/'.join(sorted(modes))} {s0.timestamp:%Y-%m-%d %H:%M:%S}{src}")
        self.redraw()

    def redraw(self) -> None:
        if not self.shots:
            self._empty()
            return
        avail = [q for q in QUANTITIES if any(q in s.quantities for s in self.shots)]
        qtys = [q for q in avail if self.qty_vars[q].get()]
        if not qtys:
            self._empty()
            return
        try:
            wanted = parse_channels(self.chan_var.get())
        except ValueError:
            wanted = []
        skip = SKIP_SAMPLES if self.skip_var.get() else 0
        secs = self.secs_var.get()

        self.fig.clear()
        self._lines = []
        axes = self.fig.subplots(len(qtys), 1, sharex=True, squeeze=False)[:, 0]
        multi = len(self.shots) > 1
        nseries = sum(len([c for c in sh.channels if not wanted or c in wanted]) for sh in self.shots)
        colors = matplotlib.colormaps["tab20" if nseries > 10 else "tab10"].colors
        for ax, q in zip(axes, qtys):
            scale, unit, title = QUANTITIES[q]
            ax.set_prop_cycle(color=colors)
            for si, shot in enumerate(self.shots):
                if q not in shot.quantities:
                    continue
                x = np.arange(shot.nsam, dtype=float)
                if secs:
                    x /= SAMPLE_RATE
                x = x[skip:]
                for ch in shot.channels:
                    if wanted and ch not in wanted:
                        continue
                    y = shot.values(ch, q)[skip:]
                    xd, yd = decimate(x, y)
                    label = f"{shot.uut} CH{ch}" if multi else f"CH{ch}"
                    (line,) = ax.plot(xd, yd, lw=0.8, label=label,
                                      ls="-" if si == 0 else ("--" if si == 1 else ":"))
                    self._lines.append((line, x, y))
            ax.set_ylabel(f"{title} [{unit}]")
            ax.grid(True, alpha=0.3)
            ax.callbacks.connect("xlim_changed", self._on_xlim)
        axes[-1].set_xlabel("time [s]" if secs else "samples")
        nlines = len(axes[0].get_lines())
        if 0 < nlines:
            axes[0].legend(loc="upper left", bbox_to_anchor=(1.01, 1.0), fontsize="small",
                           ncol=1 if nlines <= 16 else 2, frameon=False)
        self.canvas.draw_idle()

    def _on_xlim(self, ax) -> None:
        if self._updating:
            return
        self._updating = True
        try:
            lo, hi = ax.get_xlim()
            for line, x, y in self._lines:
                i0, i1 = np.searchsorted(x, [lo, hi])
                i0, i1 = max(i0 - 1, 0), min(i1 + 1, x.size)
                if i1 - i0 < 2:
                    continue
                line.set_data(*decimate(x[i0:i1], y[i0:i1]))
            self.canvas.draw_idle()
        finally:
            self._updating = False
