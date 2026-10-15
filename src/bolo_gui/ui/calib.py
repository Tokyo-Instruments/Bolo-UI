"""Calibration table: latest calibfit.log of every box, out-of-range rows highlighted."""

from __future__ import annotations

import datetime as dt
import tkinter as tk
from tkinter import ttk

from ..config import SENS_RANGE, TAU_RANGE
from ..device import CalResult

COLUMNS = (("uut", 140), ("ch", 50), ("sensitivity", 110), ("tau", 110),
           ("I0", 130), ("Q0", 130), ("status", 90))


class CalibPanel(ttk.Frame):
    def __init__(self, master):
        super().__init__(master)
        self.info = ttk.Label(self, text="No calibration yet. Remove the light source "
                                         "from the foils before calibrating.")
        self.info.pack(fill="x", padx=6, pady=4)
        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(frame, columns=[c for c, _ in COLUMNS], show="headings")
        for col, width in COLUMNS:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=width, anchor="e" if col not in ("uut", "status") else "w")
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.tag_configure("bad", background="#ffd6d6")
        ttk.Label(self, text=f"In range: sensitivity {SENS_RANGE[0]:g}–{SENS_RANGE[1]:g}, "
                             f"tau {TAU_RANGE[0]:g}–{TAU_RANGE[1]:g} s").pack(anchor="w", padx=6)
        self._results: dict[str, dict[int, CalResult]] = {}

    def bad_count(self) -> int:
        return sum(not r.in_range() for res in self._results.values() for r in res.values())

    def update_results(self, uut: str, results: dict[int, CalResult], source: str = "gui") -> None:
        self._results[uut] = results
        self.tree.delete(*self.tree.get_children())
        nbad = 0
        for name in sorted(self._results):
            for ch, r in sorted(self._results[name].items()):
                ok = r.in_range()
                nbad += not ok
                self.tree.insert("", "end", tags=() if ok else ("bad",), values=(
                    name, ch, f"{r.sensitivity:.4f}", f"{r.tau:.5f}",
                    f"{r.I0:.6g}", f"{r.Q0:.6g}", "Pass" if ok else "OUT OF RANGE"))
        when = dt.datetime.now().strftime("%H:%M:%S")
        src = " (started from panel)" if source == "external" else ""
        self.info.configure(text=f"Last calibration of {uut} at {when}{src}. "
                                 + (f"{nbad} channel(s) out of range." if nbad else "All channels in range."))
