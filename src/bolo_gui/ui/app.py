"""Main window: wires the panels to the devices, the sequencer and the worker.

Every hardware action follows the same pattern::

    def do_something(self):
        devs = self._devs()                       # boxes ticked in the device list
        if not devs: return
        def job():                                # runs in the worker thread
            return sequencer.something(devs, ...)
        self.worker.submit("Something", job, on_done=self._something_done)

on_done runs on the Tk thread and may touch widgets; job must not.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from .. import __version__, sequencer
from ..config import Settings
from ..data import Shot, append_cal_history, load_shots, save_shots, save_text
from ..device import BoloDevice, CalMode, ShotEvent, SimDevice, make_device
from ..worker import Worker
from .acquire import AcquirePanel
from .calib import CalibPanel
from .devices import ChannelsPanel, DevicesPanel
from .log import LogPanel
from .plot import PlotPanel

log = logging.getLogger(__name__)


class BoloApp:
    def __init__(self, root: tk.Tk, devices: list[BoloDevice], settings: Settings,
                 sim_nchan: int = 8, sim_time_scale: float = 0.3):
        self.root = root
        self.settings = settings
        self.sim_nchan = sim_nchan
        self.sim_time_scale = sim_time_scale
        self.worker = Worker()
        self.worker.on_busy_changed = self._on_busy
        self.devices: dict[str, BoloDevice] = {}
        self.connected: set[str] = set()
        self.last_shots: list[Shot] = []
        self.last_saved: Path | None = None

        root.title(f"BOLO8 Control {__version__}")
        root.geometry("1400x860")
        root.minsize(1000, 640)
        self._build()
        logging.getLogger().addHandler(self.log_panel.handler)

        for d in devices:
            self.add_device(d)
        root.after(50, self._poll)
        root.protocol("WM_DELETE_WINDOW", self.quit)

    # ------------------------------------------------------------------ layout
    def _build(self) -> None:
        menubar = tk.Menu(self.root)
        fm = tk.Menu(menubar, tearoff=False)
        fm.add_command(label="Open shot…", command=self.open_shot, accelerator="Ctrl+O")
        fm.add_command(label="Open HDF5 file…", command=self.open_h5)
        fm.add_command(label="Open data folder", command=self.open_data_folder)
        fm.add_separator()
        fm.add_command(label="Quit", command=self.quit, accelerator="Ctrl+Q")
        menubar.add_cascade(label="File", menu=fm)
        self.sim_menu = tk.Menu(menubar, tearoff=False, postcommand=self._fill_sim_menu)
        menubar.add_cascade(label="Simulator", menu=self.sim_menu)
        self.root.config(menu=menubar)
        self.root.bind_all("<Control-o>", lambda _e: self.open_shot())
        self.root.bind_all("<Control-q>", lambda _e: self.quit())

        paned = ttk.PanedWindow(self.root, orient="horizontal")
        paned.pack(fill="both", expand=True)

        left = ttk.Frame(paned, padding=6)
        paned.add(left, weight=0)
        self.devices_panel = DevicesPanel(left, self.add_device_dialog, self.remove_device,
                                          self.connect)
        self.devices_panel.pack(fill="x")
        self.channels = ChannelsPanel(left)
        self.channels.pack(fill="x", pady=6)
        self.acquire = AcquirePanel(left, self.settings, {
            "calibrate": self.calibrate,
            "capture": lambda: self.capture(save=False),
            "capture_save": lambda: self.capture(save=True),
            "cycles": self.run_cycles,
            "read_box": self.read_box,
            "abort": self.abort,
            "save": self.save_last,
        })
        self.acquire.pack(fill="x")

        self.notebook = ttk.Notebook(paned)
        paned.add(self.notebook, weight=1)
        self.plot = PlotPanel(self.notebook, self.settings.plot_quantity)
        self.calib = CalibPanel(self.notebook)
        self.log_panel = LogPanel(self.notebook, self.worker.call_in_ui)
        self.notebook.add(self.plot, text="Plot")
        self.notebook.add(self.calib, text="Calibration")
        self.notebook.add(self.log_panel, text="Log")

        self.status = ttk.Label(self.root, text="Ready", anchor="w", relief="sunken", padding=(6, 2))
        self.status.pack(fill="x", side="bottom")

    def _poll(self) -> None:
        self.worker.poll()
        self.root.after(50, self._poll)

    def _on_busy(self, busy: bool, name: str) -> None:
        self.acquire.set_busy(busy)
        self.channels.set_enabled(not busy)
        self.status.configure(text=f"Running: {name} …" if busy else "Ready")

    @property
    def data_dir(self) -> str:
        return self.acquire.data_dir.get()

    # ----------------------------------------------------------------- devices
    def add_device(self, dev: BoloDevice) -> None:
        if dev.name in self.devices:
            messagebox.showwarning("Add device", f"{dev.name} is already in the list")
            return
        self.devices[dev.name] = dev
        dev.add_listener(lambda ev: self.worker.call_in_ui(self._on_event, ev))
        self.devices_panel.add(dev.name)
        self.connect(dev.name)

    def add_device_dialog(self) -> None:
        spec = simpledialog.askstring(
            "Add device", "Box hostname (e.g. acq2206_077),\nor sim:NAME[:NCHAN] for a simulator:",
            parent=self.root)
        if spec and spec.strip():
            self.add_device(make_device(spec.strip(), self.sim_nchan, self.sim_time_scale))

    def connect(self, name: str) -> None:
        """Connect in a thread of its own so a dead box does not block the worker."""
        dev = self.devices[name]
        self.devices_panel.set_status(name, "connecting")
        self.connected.discard(name)

        def run():
            try:
                dev.connect()
            except Exception as e:                       # noqa: BLE001
                log.error("%s: connection failed: %s", name, e)
                self.worker.call_in_ui(self.devices_panel.set_status, name, "error: no connection")
            else:
                self.worker.call_in_ui(self._connected, name)

        threading.Thread(target=run, daemon=True, name=f"connect-{name}").start()

    def _connected(self, name: str) -> None:
        dev = self.devices.get(name)
        if dev is None:
            return
        self.connected.add(name)
        self.devices_panel.set_nchan(name, dev.nchan)
        self.devices_panel.set_status(name, "idle")
        self.channels.set_device(name, dev.nchan, self.settings.active.get(name))

    def remove_device(self, name: str) -> None:
        if self.worker.busy:
            messagebox.showwarning("Remove device", "Wait until the current operation has finished.")
            return
        dev = self.devices.pop(name, None)
        if dev:
            threading.Thread(target=dev.close, daemon=True).start()
        self.connected.discard(name)
        self.devices_panel.remove(name)
        self.channels.remove_device(name)

    def _devs(self) -> list[BoloDevice] | None:
        names = [n for n in self.devices_panel.used() if n in self.connected]
        if not names:
            messagebox.showerror("No device", "No connected device is selected (tick it in Devices).")
            return None
        return [self.devices[n] for n in names]

    def _check_flush(self, devs: list[BoloDevice]) -> bool:
        try:
            sequencer.check_ready(devs)
            return True
        except RuntimeError as e:
            messagebox.showerror("Flush needed", str(e))
            return False

    # ---------------------------------------------------------------- actions
    def calibrate(self) -> None:
        devs = self._devs()
        if not devs:
            return
        mode = self.acquire.selected_cal_mode()
        if mode != CalMode.FLUSH:
            if not self._check_flush(devs):
                return
            if not messagebox.askokcancel(
                    "Calibrate", "Remove the light source from the bolometer foils, then press OK."):
                return
        active = dict(self.channels.active)

        if self.acquire.show_cal_response():
            def job_resp():
                sequencer.set_active(devs, active)
                return sequencer.calibrate_with_response(devs)

            def done_resp(result):
                transcripts, shots = result
                self._calibrated(devs, mode, transcripts)
                if shots:
                    self._new_shots(shots, False)        # plot; saved if autosave is on
            self.worker.submit("Calibrate + flush (with response)", job_resp, on_done=done_resp)
            return

        def job():
            sequencer.set_active(devs, active)
            return sequencer.calibrate(devs, mode)

        self.worker.submit(mode.label, job, on_done=lambda tr: self._calibrated(devs, mode, tr))

    def _calibrated(self, devs: list[BoloDevice], mode: CalMode, transcripts: dict) -> None:
        stamp = dt.datetime.now()
        for d in devs:
            try:
                save_text(transcripts.get(d.name, ""), self.data_dir,
                          f"cal_logs/{stamp:%Y%m%d_%H%M%S}_{d.name}_{mode.name}.log")
                if mode != CalMode.FLUSH and d.last_cal:
                    append_cal_history(self.data_dir, d.name, d.last_cal, "gui", stamp)
            except OSError as e:
                log.error("could not write calibration log: %s", e)
            if mode != CalMode.FLUSH:
                self.calib.update_results(d.name, d.last_cal)
            self.devices_panel.set_status(d.name, "needs flush" if d.needs_flush else "idle")
        if mode != CalMode.FLUSH:
            self.notebook.select(self.calib)
            self._update_calib_tab()
        if mode == CalMode.CAL_ONLY:
            if messagebox.askyesno("Calibration data",
                                   "Read the calibration data (MAG/PHI/OHI/OHV) from the box now?\n"
                                   "Run 'Flush only' afterwards, before the next capture."):
                self.read_box()

    def _update_calib_tab(self) -> None:
        nbad = self.calib.bad_count()
        self.notebook.tab(self.calib, text=f"Calibration ({nbad} ⚠)" if nbad else "Calibration")

    def _capture_meta(self) -> dict:
        return {"post": int(self.acquire.post.get()), "trigger": self.acquire.trigger.get()}

    def capture(self, save: bool = False) -> None:
        devs = self._devs()
        if not devs or not self._check_flush(devs):
            return
        meta = self._capture_meta()
        active = dict(self.channels.active)
        abort = self.worker.abort

        def job():
            sequencer.set_active(devs, active)
            sequencer.capture(devs, meta["post"], meta["trigger"], abort)
            return sequencer.offload(devs, "CAP", meta)

        self.worker.submit("Capture", job, on_done=lambda shots: self._new_shots(shots, save))

    def run_cycles(self) -> None:
        devs = self._devs()
        if not devs or not self._check_flush(devs):
            return
        n = int(self.acquire.cycles.get())
        if n > 1 and not self.acquire.autosave.get() and messagebox.askyesno(
                "Cycles", "Autosave is off: every capture overwrites the previous one and "
                          "only the last cycle is kept.\n\nTurn autosave on?"):
            self.acquire.autosave.set(True)
        cal_mode = CalMode.CAL_FLUSH if self.acquire.cal_each.get() else None
        meta = self._capture_meta()
        active = dict(self.channels.active)
        abort = self.worker.abort

        def job():
            sequencer.set_active(devs, active)
            sequencer.run_cycles(
                devs, n, cal_mode=cal_mode, do_capture=True, post=meta["post"],
                trigger=meta["trigger"], abort=abort,
                on_capture=lambda shots: self.worker.call_in_ui(self._new_shots, shots, False))
            return devs

        def done(_):
            for d in devs:
                if d.last_cal:
                    self.calib.update_results(d.name, d.last_cal)

        self.worker.submit(f"{n} cycle(s)", job, on_done=done)

    def read_box(self) -> None:
        """Offload whatever the boxes hold (capture, or calibration data after 'no flush')."""
        devs = self._devs()
        if not devs:
            return
        self.worker.submit("Read data", sequencer.offload, devs, None, {},
                           on_done=lambda shots: self._new_shots(shots, False, autosave=False))

    def abort(self) -> None:
        self.worker.abort.set()
        for d in self.devices.values():
            if d.name in self.connected:
                d.abort()
        self.status.configure(text="Aborting …")

    # ----------------------------------------------------------- data/display
    def _new_shots(self, shots: list[Shot], save: bool, autosave: bool = True) -> None:
        self.last_shots = shots
        self.last_saved = None
        self.plot.show(shots)
        self.notebook.select(self.plot)
        if save or (autosave and self.acquire.autosave.get()):
            self._save_async(shots)

    def save_last(self) -> None:
        if not self.last_shots:
            messagebox.showinfo("Save", "Nothing to save yet. Capture first.")
            return
        if self.last_saved:
            messagebox.showinfo("Save", f"Already saved to\n{self.last_saved}")
            return
        self._save_async(self.last_shots)

    def _save_async(self, shots: list[Shot]) -> None:
        """Save in a thread of its own, so a running cycle loop is not held up."""
        data_dir, note, fmt = self.data_dir, self.acquire.note.get(), self.acquire.selected_format()
        scaled = self.acquire.save_scaled.get()

        def run():
            try:
                folder = save_shots(shots, data_dir, note=note, extra={"app_version": __version__},
                                    fmt=fmt, scaled=scaled)
            except Exception as e:                       # noqa: BLE001
                log.error("saving failed: %s", e)
                self.worker.call_in_ui(messagebox.showerror, "Save failed", str(e))
                return
            log.info("SAVED to %s", folder)
            self.worker.call_in_ui(self._saved, shots, folder)

        threading.Thread(target=run, daemon=True, name="save").start()

    def _saved(self, shots: list[Shot], folder: Path) -> None:
        if shots is self.last_shots:
            self.last_saved = folder
        self.status.configure(text=f"Saved to {folder}")

    def open_shot(self) -> None:
        folder = filedialog.askdirectory(initialdir=self.data_dir, mustexist=True,
                                         title="Open a saved shot folder")
        if folder:
            self._open(folder)

    def open_h5(self) -> None:
        path = filedialog.askopenfilename(initialdir=self.data_dir, title="Open an HDF5 shot",
                                          filetypes=[("HDF5", "*.h5 *.hdf5"), ("All files", "*")])
        if path:
            self._open(path)

    def _open(self, path: str) -> None:
        try:
            shots = load_shots(path)
        except Exception as e:                           # noqa: BLE001
            messagebox.showerror("Open shot", str(e))
            return
        self.last_shots = shots
        self.last_saved = Path(path)
        self.plot.show(shots)
        self.notebook.select(self.plot)
        log.info("opened %s", path)

    def open_data_folder(self) -> None:
        path = Path(self.data_dir).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        opener = "open" if sys.platform == "darwin" else ("explorer" if os.name == "nt" else "xdg-open")
        subprocess.Popen([opener, str(path)])

    # --------------------------------------------- shots started outside the app
    def _on_event(self, ev: ShotEvent) -> None:
        if ev.uut not in self.devices:
            return
        dev = self.devices[ev.uut]
        if ev.phase == "started":
            what = {"CAL": "calibrating", "CAPTURE": "capturing"}.get(ev.kind, "shot running")
            prefix = "external" if ev.source == "external" else "busy"
            self.devices_panel.set_status(ev.uut, f"{prefix}: {what}")
            if ev.source == "external":
                log.info("%s: shot started outside the app (panel button / external)", ev.uut)
            return
        # finished
        self.devices_panel.set_status(ev.uut, "needs flush" if dev.needs_flush else "idle")
        if ev.source != "external":
            return
        if not self.acquire.watch.get():
            log.info("%s: external %s finished (watch is off, ignored)", ev.uut, ev.kind)
            return
        if ev.kind == "CAPTURE":
            log.info("%s: external capture finished, reading data", ev.uut)
            self.worker.submit(f"Read external capture ({ev.uut})", sequencer.offload, [dev], "CAP",
                               {"trigger": "external / panel button"}, "external",
                               on_done=lambda shots: self._new_shots(shots, False))
        elif ev.kind == "CAL":
            log.info("%s: external calibration finished, reading calibfit.log", ev.uut)

            def done(results, name=ev.uut):
                self.calib.update_results(name, results, "external")
                self._update_calib_tab()
                try:
                    append_cal_history(self.data_dir, name, results, "external")
                except OSError as e:
                    log.error("could not write calibration history: %s", e)
                self.notebook.select(self.calib)

            self.worker.submit(f"Read calibration ({ev.uut})", dev.calibfit, on_done=done)

    def _fill_sim_menu(self) -> None:
        m = self.sim_menu
        m.delete(0, "end")
        sims = [d for d in self.devices.values() if isinstance(d, SimDevice)]
        if not sims:
            m.add_command(label="(no simulated devices; add sim:NAME)", state="disabled")
            return
        for d in sims:
            m.add_command(label=f"{d.name}: press CAPTURE button", command=d.press_capture)
            m.add_command(label=f"{d.name}: press CALIBRATE button", command=d.press_calibrate)
            m.add_command(label=f"{d.name}: send external trigger", command=d.fire_ext_trigger)
            m.add_separator()

    # ------------------------------------------------------------------- quit
    def quit(self) -> None:
        if self.worker.busy and not messagebox.askyesno(
                "Quit", f"'{self.worker.current}' is still running. Quit anyway?"):
            return
        self.acquire.store(self.settings)
        self.settings.active = dict(self.channels.active)
        self.settings.uuts = [getattr(d, "spec", d.name) for d in self.devices.values()]
        self.settings.plot_quantity = self.plot.capture_quantity()
        self.settings.save()
        for d in self.devices.values():
            try:
                d.close()
            except Exception:                            # noqa: BLE001
                pass
        self.root.destroy()
