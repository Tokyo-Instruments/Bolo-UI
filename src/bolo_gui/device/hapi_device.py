"""Real BOLO8 box via D-TACQ acq400_hapi.

The control sequence is ported from D-TACQ's bolo8_cal_cap_loop.py,
bolo_demux.py and bolo8_tester.py (see HANDOVER.md). acq400_hapi is imported
lazily, so the rest of the app (and the simulator) works without it.

Shots started outside the app (BOLO8-PANEL buttons, another script, Phoebus)
are detected by a monitor thread that watches the transient state reported by
hapi's status monitor. Whether such a shot was a capture or a calibration is
not reported directly; see _classify_external(). TODO(D-TACQ): replace the
heuristic once D-TACQ confirms how the panel buttons are exposed.
"""

from __future__ import annotations

import hashlib
import logging
import signal
import threading
import time
import urllib.request

from ..data import Shot, demux
from .base import BoloDevice, CalMode, parse_calibfit

log = logging.getLogger(__name__)

TRG = {"int": "1,1,1", "ext rising": "1,0,1", "ext falling": "1,0,0"}
IDLE = 0


def _import_hapi():
    try:
        import acq400_hapi
    except ImportError as e:
        raise RuntimeError(
            "acq400_hapi is not installed in this Python environment.\n"
            "Install the app into the hapi venv, e.g.\n"
            "  ~/PROJECTS/acq400_hapi/.venv/bin/pip install -e <path to bolo_gui>\n"
            "or use sim:NAME for a simulated box.") from e
    return acq400_hapi


class HapiDevice(BoloDevice):
    #: seconds to wait for calibfit.log to change after an external shot before
    #: deciding it was a capture (only used if no CALIBRATING knob was found)
    cal_detect_window = 20.0

    def __init__(self, name: str):
        super().__init__(name)
        self.uut = None
        self.hapi = None
        self._nchan = 0
        self._info: dict = {}
        self._old_trg: str | None = None
        self._cal_knob: tuple[str, str] | None = None
        self._calfit_hash: str | None = None
        self._last_transcript = ""
        self._gui_end = 0.0
        self._stop = threading.Event()
        self._monitor: threading.Thread | None = None

    # -- connection ---------------------------------------------------------
    def connect(self) -> None:
        self.hapi = _import_hapi()
        self.uut = self.hapi.factory(self.name)
        self._nchan = int(self.uut.s0.NCHAN) // 3
        info = {}
        for key in ("MODEL", "SERIAL", "software_version", "fpga_version"):
            try:
                info[key.lower()] = getattr(self.uut.s0, key)
            except Exception as e:                       # noqa: BLE001 - informational only
                info[key.lower()] = f"? ({e})"
        info["nchan"] = self._nchan
        self._info = info
        self._find_cal_knob()
        try:
            self._calfit_hash = _hash(self._fetch_calibfit_text())
        except OSError:
            pass
        self._stop.clear()
        self._monitor = threading.Thread(target=self._monitor_loop, daemon=True,
                                         name=f"monitor-{self.name}")
        self._monitor.start()
        log.info("%s: connected, %d channels, %s", self.name, self._nchan, info.get("model"))

    def close(self) -> None:
        self._stop.set()
        if self.uut is not None:
            try:
                self.uut.close()
            except Exception:                           # noqa: BLE001
                log.debug("close failed", exc_info=True)

    @property
    def nchan(self) -> int:
        return self._nchan

    def info(self) -> dict:
        return dict(self._info)

    def set_active(self, channels: list[int]) -> None:
        if channels:
            value = ",".join(str(c) for c in channels)
            self.uut.s14.BOLO_ACTIVE_CHAN = value
            log.info("%s: BOLO_ACTIVE_CHAN=%s", self.name, value)

    # -- calibration ---------------------------------------------------------
    def cal_prepare(self) -> None:
        # trg=1,1,1 external d1 RISING  ->  "1,1,1"
        self._old_trg = self.uut.s1.trg.split(" ")[0].split("=")[1]
        self.uut.s1.trg = "1,1,1"               # soft trigger for calibration

    def cal_restore(self) -> None:
        if self._old_trg:
            self.uut.s1.trg = self._old_trg

    def cal_run(self, mode: CalMode) -> str:
        ports = {CalMode.CAL_FLUSH: self.hapi.AcqPorts.BOLO8_CAL,
                 CalMode.CAL_ONLY: self.hapi.AcqPorts.BOLO8_CAL1,
                 CalMode.FLUSH: self.hapi.AcqPorts.BOLO8_CAL2}
        self.emit("CAL", "started", mode=int(mode))
        txt = self._run_service(ports[mode], eof="END")
        self._last_transcript = txt
        if mode != CalMode.FLUSH:
            self.buffer_mode = "CAL"
        self._gui_end = time.monotonic()
        self.emit("CAL", "finished", mode=int(mode))
        return txt

    def _run_service(self, port: int, eof: str) -> str:
        """uut.run_service(), but each line goes to the log as it arrives."""
        import socket
        txt = []
        with self.hapi.Netclient(self.uut.uut, port) as nc:
            while True:
                rx = nc.receive_message(self.uut.NL, 256)
                txt.append(rx)
                log.info("%s> %s", self.name, rx.rstrip())
                if rx.startswith(eof):
                    break
            nc.sock.shutdown(socket.SHUT_RDWR)
            nc.sock.close()
        return "\n".join(txt) + "\n"

    def _fetch_calibfit_text(self) -> str:
        url = f"http://{self.name}/tmp/calibfit.log"
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.read().decode("utf-8", "replace")

    def calibfit(self) -> dict:
        try:
            text = self._fetch_calibfit_text()
            self._calfit_hash = _hash(text)
            self.last_cal = parse_calibfit(text)
        except OSError as e:
            log.warning("%s: calibfit.log not available (%s), using transcript", self.name, e)
            self.last_cal = parse_calibfit(self._last_transcript)
        return self.last_cal

    # -- capture -------------------------------------------------------------
    def cap_configure(self, post: int, trigger: str) -> None:
        self.uut.s0.transient = f"POST={int(post)} SOFT_TRIGGER=0 DEMUX=0"
        self.uut.s1.trg = TRG[trigger]

    def cap_arm(self) -> None:
        sm = self.uut.statmon
        sm.armed.clear()
        sm.stopped.clear()
        sm.break_requested = False
        self.uut.s0.set_arm = "1"

    def cap_wait_armed(self, abort: threading.Event) -> None:
        self.wait_for(self.uut.statmon.armed.is_set, abort)
        self.emit("CAPTURE", "started")

    def cap_soft_trigger(self) -> None:
        self.uut.s0.soft_trigger = "1"

    def cap_wait_stopped(self, abort: threading.Event) -> None:
        self.wait_for(self.uut.statmon.stopped.is_set, abort)
        self.buffer_mode = "CAP"

    def cap_finish(self) -> None:
        self.uut.s14.DSP_RESET = 1
        time.sleep(0.1)
        self.uut.s14.DSP_RESET = 0
        self._gui_end = time.monotonic()
        self.emit("CAPTURE", "finished")

    def abort(self) -> None:
        if self.uut is None:
            return
        try:
            self.uut.statmon.break_requested = True
            self.uut.s0.set_abort = "1"
        except Exception:                               # noqa: BLE001
            log.warning("%s: abort failed", self.name, exc_info=True)

    # -- data ----------------------------------------------------------------
    def offload(self, mode: str) -> Shot:
        t0 = time.monotonic()
        raw = self.uut.read_channels()
        data = demux(raw, self._nchan, mode)
        log.info("%s: read %d bytes in %.1f s", self.name, data.nbytes, time.monotonic() - t0)
        return Shot(uut=self.name, mode=mode, data=data, nchan=self._nchan)

    # -- external shots --------------------------------------------------------
    def _find_cal_knob(self) -> None:
        """Look for a knob that says 'calibration running' (B8:CALIBRATING PV).
        TODO(D-TACQ): confirm the knob name; the found candidates are logged."""
        for site in ("s14", "s0", "s1"):
            try:
                knobs = getattr(self.uut, site).help(".*CALIB.*")
            except Exception:                           # noqa: BLE001
                continue
            if knobs:
                log.info("%s: %s calibration knobs: %s", self.name, site, ", ".join(knobs))
            for k in knobs:
                if k.upper().endswith("CALIBRATING"):
                    self._cal_knob = (site, k)
                    log.info("%s: using %s.%s to detect panel calibrations", self.name, site, k)
                    return

    def _calibrating(self) -> bool | None:
        if not self._cal_knob:
            return None
        site, knob = self._cal_knob
        try:
            val = getattr(self.uut, site).get_knob(knob)
            return int(val.split()[-1]) != 0
        except Exception:                               # noqa: BLE001
            return None

    def _monitor_loop(self) -> None:
        prev = IDLE
        saw_cal = False
        while not self._stop.wait(0.05):
            sm = self.uut.statmon
            if not sm.st_thread.is_alive() and not self._stop.is_set():
                self._restart_statmon()
                continue
            state = sm.status[0] if sm.status else IDLE
            gui = self._activity is not None or time.monotonic() - self._gui_end < 1.0
            if prev == IDLE and state != IDLE:
                saw_cal = False
                if not gui:
                    self.emit("SHOT", "started", source="external")
            if state != IDLE and not gui and self._calibrating():
                saw_cal = True
            if prev != IDLE and state == IDLE and not gui:
                kind = self._classify_external(saw_cal)
                if kind == "CAP":
                    self.buffer_mode = "CAP"
                    self.emit("CAPTURE", "finished", source="external")
                else:
                    self.buffer_mode = "CAL"
                    self.emit("CAL", "finished", source="external")
            prev = state

    def _classify_external(self, saw_cal: bool) -> str:
        """Was the shot that just ended a capture or a calibration?"""
        if saw_cal:
            return "CAL"
        cal_now = self._calibrating()
        if cal_now is not None:
            # knob available: wait until the calibration (fit + filter load) ends
            if cal_now:
                while self._calibrating() and not self._stop.wait(0.5):
                    pass
                return "CAL"
            return "CAP"
        # no knob: a calibration rewrites calibfit.log shortly after its transient
        t_end = time.monotonic() + self.cal_detect_window
        while time.monotonic() < t_end and not self._stop.wait(1.0):
            try:
                h = _hash(self._fetch_calibfit_text())
            except OSError:
                break
            if h != self._calfit_hash:
                self._calfit_hash = h
                return "CAL"
        return "CAP"

    def _restart_statmon(self) -> None:
        """hapi's status monitor exits (and SIGINTs us) if it sees a state jump
        that skips ARM, e.g. a very short shot started from the panel."""
        log.warning("%s: hapi status monitor stopped, restarting it", self.name)
        try:
            from acq400_hapi.acq400 import Statusmonitor
            self.uut.statmon = Statusmonitor(self.uut.uut, list(self.uut.statmon.status))
        except Exception:                               # noqa: BLE001
            log.exception("%s: could not restart status monitor", self.name)
            self._stop.wait(5.0)


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()


_guard_installed = False


def install_sigint_guard() -> None:
    """hapi's Statusmonitor calls os.kill(SIGINT) on itself on some state
    errors. Log that instead of letting it kill the GUI; a real Ctrl-C twice
    within 2 s still quits."""
    global _guard_installed
    if _guard_installed or threading.current_thread() is not threading.main_thread():
        return
    last = [0.0]

    def handler(signum, frame):
        now = time.monotonic()
        if now - last[0] < 2.0:
            raise KeyboardInterrupt
        last[0] = now
        log.warning("SIGINT received (hapi state error or Ctrl-C); press Ctrl-C again to quit")

    signal.signal(signal.SIGINT, handler)
    _guard_installed = True
