"""Log pane: shows everything sent to the Python logging system."""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

MAX_LINES = 5000


class LogPanel(ttk.Frame):
    def __init__(self, master, call_in_ui):
        super().__init__(master)
        self.text = ScrolledText(self, height=10, wrap="none", state="disabled",
                                 font=("TkFixedFont", 10))
        self.text.pack(fill="both", expand=True)
        self.text.tag_configure("WARNING", foreground="#b36b00")
        self.text.tag_configure("ERROR", foreground="#c00000")
        self.text.tag_configure("CRITICAL", foreground="#c00000")
        bar = ttk.Frame(self)
        bar.pack(fill="x")
        ttk.Button(bar, text="Clear", command=self.clear).pack(side="right", padx=4, pady=2)
        self.handler = _TkHandler(self, call_in_ui)
        self.handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))

    def append(self, msg: str, level: str) -> None:
        self.text.configure(state="normal")
        self.text.insert("end", msg + "\n", level)
        lines = int(self.text.index("end-1c").split(".")[0])
        if lines > MAX_LINES:
            self.text.delete("1.0", f"{lines - MAX_LINES}.0")
        self.text.see("end")
        self.text.configure(state="disabled")

    def clear(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")


class _TkHandler(logging.Handler):
    def __init__(self, panel: LogPanel, call_in_ui):
        super().__init__(logging.INFO)
        self.panel = panel
        self.call_in_ui = call_in_ui

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.call_in_ui(self.panel.append, self.format(record), record.levelname)
        except Exception:                                # noqa: BLE001
            self.handleError(record)
