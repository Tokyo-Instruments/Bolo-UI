"""Device list and active-channel selection."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

from ..config import MAX_CHANNELS

STATUS_COLORS = {"idle": "#2a7a2a", "busy": "#b36b00", "error": "#c00000",
                 "connecting": "#777", "needs flush": "#c00000", "external": "#1f5fbf"}


class DevicesPanel(ttk.LabelFrame):
    """One row per box: [use] name  nchan  status  [↻] [✕]."""

    def __init__(self, master, on_add: Callable[[], None], on_remove: Callable[[str], None],
                 on_reconnect: Callable[[str], None]):
        super().__init__(master, text="Devices")
        self.on_remove = on_remove
        self.on_reconnect = on_reconnect
        self.rows: dict[str, dict] = {}
        self.body = ttk.Frame(self)
        self.body.pack(fill="x", padx=4, pady=2)
        ttk.Button(self, text="Add device…", command=on_add).pack(anchor="w", padx=4, pady=(0, 4))

    def add(self, name: str) -> None:
        if name in self.rows:
            return
        r = len(self.rows)
        use = tk.BooleanVar(value=True)
        cb = ttk.Checkbutton(self.body, variable=use)
        lbl = ttk.Label(self.body, text=name, width=16)
        nch = ttk.Label(self.body, text="–", width=6)
        st = ttk.Label(self.body, text="connecting", width=14)
        rc = ttk.Button(self.body, text="↻", width=2, command=lambda: self.on_reconnect(name))
        rm = ttk.Button(self.body, text="✕", width=2, command=lambda: self.on_remove(name))
        widgets = [cb, lbl, nch, st, rc, rm]
        for c, w in enumerate(widgets):
            w.grid(row=r, column=c, sticky="w", padx=1)
        self.rows[name] = {"use": use, "nchan": nch, "status": st, "widgets": widgets}

    def remove(self, name: str) -> None:
        row = self.rows.pop(name, None)
        if row:
            for w in row["widgets"]:
                w.destroy()

    def set_nchan(self, name: str, nchan: int) -> None:
        if name in self.rows:
            self.rows[name]["nchan"].configure(text=f"{nchan} ch")

    def set_status(self, name: str, text: str) -> None:
        if name in self.rows:
            color = STATUS_COLORS.get(text.split(":")[0], "#000")
            self.rows[name]["status"].configure(text=text, foreground=color)

    def used(self) -> list[str]:
        return [n for n, r in self.rows.items() if r["use"].get()]


class ChannelsPanel(ttk.LabelFrame):
    """Active channels (foils connected) per box, 32-channel grid."""

    COLS = 8

    def __init__(self, master):
        super().__init__(master, text="Active channels")
        top = ttk.Frame(self)
        top.pack(fill="x", padx=4, pady=2)
        ttk.Label(top, text="Box:").pack(side="left")
        self.dev_var = tk.StringVar()
        self.combo = ttk.Combobox(top, textvariable=self.dev_var, state="readonly", width=16)
        self.combo.pack(side="left", padx=4)
        self.combo.bind("<<ComboboxSelected>>", lambda _e: self._load())
        ttk.Button(top, text="All", width=4, command=lambda: self._set_all(True)).pack(side="left")
        ttk.Button(top, text="None", width=5, command=lambda: self._set_all(False)).pack(side="left")
        grid = ttk.Frame(self)
        grid.pack(padx=4, pady=2)
        self.vars = [tk.BooleanVar() for _ in range(MAX_CHANNELS)]
        self.buttons = []
        for i in range(MAX_CHANNELS):
            b = ttk.Checkbutton(grid, text=f"{i + 1:2d}", variable=self.vars[i],
                                command=self._store, width=3)
            b.grid(row=i // self.COLS, column=i % self.COLS, sticky="w")
            self.buttons.append(b)
        self.nchan: dict[str, int] = {}
        self.active: dict[str, list[int]] = {}
        self._load()

    def set_device(self, name: str, nchan: int, active: list[int] | None = None) -> None:
        self.nchan[name] = nchan
        if active is not None:
            self.active[name] = [c for c in active if c <= nchan]
        elif name not in self.active:
            self.active[name] = list(range(1, nchan + 1))
        self.combo["values"] = list(self.nchan)
        if not self.dev_var.get():
            self.dev_var.set(name)
        self._load()

    def remove_device(self, name: str) -> None:
        self.nchan.pop(name, None)
        self.combo["values"] = list(self.nchan)
        if self.dev_var.get() == name:
            self.dev_var.set(next(iter(self.nchan), ""))
        self._load()

    def _load(self) -> None:
        name = self.dev_var.get()
        n = self.nchan.get(name, 0)
        act = set(self.active.get(name, []))
        for i, (v, b) in enumerate(zip(self.vars, self.buttons)):
            v.set(i + 1 in act)
            b.state(["!disabled"] if i < n else ["disabled"])

    def _store(self) -> None:
        name = self.dev_var.get()
        if name:
            n = self.nchan.get(name, 0)
            self.active[name] = [i + 1 for i, v in enumerate(self.vars[:n]) if v.get()]

    def _set_all(self, on: bool) -> None:
        n = self.nchan.get(self.dev_var.get(), 0)
        for v in self.vars[:n]:
            v.set(on)
        self._store()

    def set_enabled(self, enabled: bool) -> None:
        self.combo.state(["!disabled", "readonly"] if enabled else ["disabled"])
        if enabled:
            self._load()
        else:
            for b in self.buttons:
                b.state(["disabled"])
