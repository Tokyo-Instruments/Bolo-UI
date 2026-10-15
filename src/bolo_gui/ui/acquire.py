"""Acquisition and saving controls.

To add a function: add a button here that calls a new method on the app
(ui/app.py), which runs a sequencer function in the worker.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, ttk

from ..config import SAMPLE_RATE, TRIGGERS, Settings
from ..data import SAVE_FORMATS
from ..device import CalMode


class AcquirePanel(ttk.Frame):
    def __init__(self, master, settings: Settings, actions: dict):
        """actions: name -> callable for calibrate, capture, capture_save, cycles,
        read_box, abort, save."""
        super().__init__(master)
        self.actions = actions
        self._action_buttons: list[ttk.Button] = []

        # -- calibration ------------------------------------------------------
        cal = ttk.LabelFrame(self, text="Calibration")
        cal.pack(fill="x", pady=(0, 4))
        self.cal_mode = tk.StringVar(value=CalMode.CAL_FLUSH.label)
        combo = ttk.Combobox(cal, textvariable=self.cal_mode, state="readonly", width=20,
                             values=[m.label for m in CalMode])
        combo.grid(row=0, column=0, padx=4, pady=4)
        self._button(cal, "Calibrate", "calibrate").grid(row=0, column=1, padx=4, pady=4)
        self.cal_response = tk.BooleanVar(value=settings.cal_show_response)
        self.cal_response_cb = ttk.Checkbutton(
            cal, text="Show sensor response (read data before flush)", variable=self.cal_response)
        self.cal_response_cb.grid(row=1, column=0, columnspan=2, sticky="w", padx=4)
        combo.bind("<<ComboboxSelected>>", lambda _e: self._update_cal_response())
        ttk.Label(cal, text="Remove the light source first.", foreground="#666").grid(
            row=2, column=0, columnspan=2, sticky="w", padx=4)

        # -- capture ------------------------------------------------------------
        cap = ttk.LabelFrame(self, text="Capture")
        cap.pack(fill="x", pady=4)
        ttk.Label(cap, text="Trigger:").grid(row=0, column=0, sticky="w", padx=4)
        self.trigger = tk.StringVar(value=settings.trigger)
        tf = ttk.Frame(cap)
        tf.grid(row=0, column=1, columnspan=3, sticky="w")
        for t in TRIGGERS:
            ttk.Radiobutton(tf, text=t, value=t, variable=self.trigger).pack(side="left", padx=2)

        ttk.Label(cap, text="POST samples:").grid(row=1, column=0, sticky="w", padx=4)
        self.post = tk.IntVar(value=settings.post)
        sp = ttk.Spinbox(cap, from_=1000, to=10_000_000, increment=10_000,
                         textvariable=self.post, width=10)
        sp.grid(row=1, column=1, sticky="w")
        self.post_secs = ttk.Label(cap, text="")
        self.post_secs.grid(row=1, column=2, sticky="w", padx=4)
        self.post.trace_add("write", lambda *_: self._update_secs())
        self._update_secs()

        bf = ttk.Frame(cap)
        bf.grid(row=2, column=0, columnspan=4, sticky="w", pady=4)
        self._button(bf, "Capture", "capture").pack(side="left", padx=4)
        self._button(bf, "Capture + Save", "capture_save").pack(side="left", padx=4)

        cf = ttk.Frame(cap)
        cf.grid(row=3, column=0, columnspan=4, sticky="w", pady=(0, 4))
        ttk.Label(cf, text="Cycles:").pack(side="left", padx=4)
        self.cycles = tk.IntVar(value=settings.cycles)
        ttk.Spinbox(cf, from_=1, to=10_000, textvariable=self.cycles, width=5).pack(side="left")
        self.cal_each = tk.BooleanVar(value=False)
        ttk.Checkbutton(cf, text="calibrate each", variable=self.cal_each).pack(side="left", padx=4)
        self._button(cf, "Run", "cycles").pack(side="left", padx=4)

        # -- box data / abort ---------------------------------------------------------
        misc = ttk.Frame(self)
        misc.pack(fill="x", pady=4)
        self._button(misc, "Read data from box", "read_box").pack(side="left", padx=4)
        self.abort_btn = ttk.Button(misc, text="Abort", command=actions["abort"], state="disabled")
        self.abort_btn.pack(side="right", padx=4)

        # -- saving -------------------------------------------------------------
        sv = ttk.LabelFrame(self, text="Saving")
        sv.pack(fill="x", pady=4)
        ttk.Label(sv, text="Folder:").grid(row=0, column=0, sticky="w", padx=4)
        self.data_dir = tk.StringVar(value=settings.data_dir)
        ttk.Entry(sv, textvariable=self.data_dir, width=24).grid(row=0, column=1, sticky="we")
        ttk.Button(sv, text="…", width=2, command=self._browse).grid(row=0, column=2, padx=2)
        ttk.Label(sv, text="Note:").grid(row=1, column=0, sticky="w", padx=4)
        self.note = tk.StringVar()
        ttk.Entry(sv, textvariable=self.note, width=24).grid(row=1, column=1, columnspan=2, sticky="we")
        ttk.Label(sv, text="Format:").grid(row=2, column=0, sticky="w", padx=4)
        self.save_format = tk.StringVar(value=SAVE_FORMATS.get(settings.save_format, SAVE_FORMATS["dat"]))
        ttk.Combobox(sv, textvariable=self.save_format, state="readonly", width=14,
                     values=list(SAVE_FORMATS.values())).grid(row=2, column=1, sticky="w")
        self.autosave = tk.BooleanVar(value=settings.autosave)
        ttk.Checkbutton(sv, text="Autosave every capture", variable=self.autosave).grid(
            row=3, column=0, columnspan=3, sticky="w", padx=4)
        self.watch = tk.BooleanVar(value=settings.watch)
        ttk.Checkbutton(sv, text="Watch for panel-button / external shots",
                        variable=self.watch).grid(row=4, column=0, columnspan=3, sticky="w", padx=4)
        self._button(sv, "Save last shot", "save").grid(row=5, column=0, columnspan=3,
                                                         sticky="w", padx=4, pady=4)
        sv.columnconfigure(1, weight=1)

    def _button(self, parent, text: str, action: str) -> ttk.Button:
        b = ttk.Button(parent, text=text, command=self.actions[action])
        self._action_buttons.append(b)
        return b

    def _update_secs(self) -> None:
        try:
            self.post_secs.configure(text=f"= {self.post.get() / SAMPLE_RATE:g} s")
        except (tk.TclError, ValueError):
            self.post_secs.configure(text="")

    def _browse(self) -> None:
        d = filedialog.askdirectory(initialdir=self.data_dir.get(), mustexist=False)
        if d:
            self.data_dir.set(d)

    def _update_cal_response(self) -> None:
        """The option only applies to 'Calibrate + flush'."""
        on = self.selected_cal_mode() == CalMode.CAL_FLUSH
        self.cal_response_cb.state(["!disabled"] if on else ["disabled"])

    def show_cal_response(self) -> bool:
        return self.selected_cal_mode() == CalMode.CAL_FLUSH and self.cal_response.get()

    def selected_format(self) -> str:
        """Key of data.SAVE_FORMATS for the format chosen in the combobox."""
        return next((k for k, v in SAVE_FORMATS.items() if v == self.save_format.get()), "dat")

    def selected_cal_mode(self) -> CalMode:
        return next(m for m in CalMode if m.label == self.cal_mode.get())

    def set_busy(self, busy: bool) -> None:
        for b in self._action_buttons:
            b.state(["disabled"] if busy else ["!disabled"])
        self.abort_btn.state(["!disabled"] if busy else ["disabled"])

    def store(self, s: Settings) -> None:
        s.data_dir = self.data_dir.get()
        s.trigger = self.trigger.get()
        s.autosave = self.autosave.get()
        s.save_format = self.selected_format()
        s.watch = self.watch.get()
        s.cal_show_response = self.cal_response.get()
        try:
            s.post = int(self.post.get())
            s.cycles = int(self.cycles.get())
        except (tk.TclError, ValueError):
            pass
