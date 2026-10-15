"""Simulated BOLO8 box for development and testing without hardware.

Produces data in the same layout as the real box: a strobed light source on
each foil, a first-order bolometer response (tau ~ 46 ms), calibration offsets
before the first calibration, and ohmic-heating steps during calibration.

The panel buttons (CAPTURE, CALIBRATE) and an external trigger can be
simulated with press_capture(), press_calibrate() and fire_ext_trigger().
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
import time

import numpy as np

from ..config import QUANTITIES, SAMPLE_RATE
from ..data import Shot, make_dtype
from .base import BoloDevice, CalMode, CalResult

log = logging.getLogger(__name__)

IDLE, ARM, RUN = 0, 1, 3


class SimDevice(BoloDevice):
    """Simulated box.

    time_scale shrinks all delays (1.0 = real time; a 100000 sample capture
    then takes 10 s). ext_auto_s fires an external trigger by itself that many
    (scaled) seconds after arming; None waits for fire_ext_trigger().
    """

    def __init__(self, name: str, nchan: int = 8, time_scale: float = 0.3,
                 ext_auto_s: float | None = 3.0, seed: int | None = None):
        super().__init__(name)
        self._nchan = nchan
        self.time_scale = time_scale
        self.ext_auto_s = ext_auto_s
        self.rng = np.random.default_rng(seed)
        self.state = IDLE
        self.trg = "1,1,1"
        self._saved_trg = None
        self.post = 100_000
        self.trigger = "int"
        self.active: list[int] = list(range(1, nchan + 1))
        self.calibrated = False
        self._soft = threading.Event()
        self._ext = threading.Event()
        self._abort = threading.Event()
        self._armed = threading.Event()
        self._stopped = threading.Event()
        self._shot_no = 0
        self._last_shot_seed = 0
        self._calfit: dict[int, CalResult] = {}

    def _sleep(self, secs: float) -> None:
        time.sleep(secs * self.time_scale)

    # -- connection ---------------------------------------------------------
    def connect(self) -> None:
        self._sleep(0.5)
        log.info("%s: simulated box with %d channels", self.name, self._nchan)

    @property
    def nchan(self) -> int:
        return self._nchan

    def info(self) -> dict:
        return {"model": "SIMULATOR", "software_version": "sim", "fpga_version": "sim",
                "nchan": self._nchan}

    def set_active(self, channels: list[int]) -> None:
        if channels:
            self.active = [c for c in channels if 1 <= c <= self._nchan]
        log.info("%s: BOLO_ACTIVE_CHAN=%s", self.name, ",".join(map(str, self.active)))

    # -- calibration ---------------------------------------------------------
    def cal_prepare(self) -> None:
        self._saved_trg = self.trg
        self.trg = "1,1,1"

    def cal_restore(self) -> None:
        if self._saved_trg is not None:
            self.trg = self._saved_trg

    def cal_run(self, mode: CalMode) -> str:
        if self.state != IDLE:
            raise RuntimeError(f"{self.name}: busy (state {self.state})")
        self.emit("CAL", "started", mode=int(mode))
        lines = [f"Old : {self.name}:B8:CALIBRATING     0",
                 f"New : {self.name}:B8:CALIBRATING     1"]
        if mode in (CalMode.CAL_FLUSH, CalMode.CAL_ONLY):
            chans = " ".join(map(str, self.active))
            lines += ["custom CALIBFITARGS -H 0.6",
                      "Setting GAINS to 1V2 and reset OSDACs before running.",
                      f'calibration starts for channels "{chans}"', "5 go"]
            self.state = RUN
            self._sleep(3.0)
            self.state = IDLE
            self._sleep(2.0)
            self._calfit = self._make_calfit()
            lines.append("Fitting data")
            lines += [f"{c.channel:<11} {c.sensitivity:<13.5g} {c.tau:<14.5g} "
                      f"{c.I0:<14.8g} {c.Q0:.8g}" for c in self._calfit.values()]
            self.buffer_mode = "CAL"
            self._last_shot_seed = int(self.rng.integers(1 << 31))
        if mode in (CalMode.CAL_FLUSH, CalMode.FLUSH):
            lines += [f"power_filter_design.tcl {c.channel} {c.sensitivity:.5g} {c.tau:.5g}"
                      for c in self._calfit.values()]
            lines.append("6 go")
            self._sleep(1.0)
            lines.append("restoring trg=1,1,1 PRE=0 POST=100000 OSAM=1 DEMUX=0 SOFT_TRIGGER=0")
            self.calibrated = True
        lines.append(f"New : {self.name}:B8:CALIBRATING     0")
        lines.append("END")
        self.emit("CAL", "finished", mode=int(mode))
        return "\n".join(lines) + "\n"

    def _make_calfit(self) -> dict[int, CalResult]:
        out = {}
        for ch in self.active:
            sens = 4.0 + 0.6 * self.rng.random()
            if ch == 6:                      # one bad foil, to exercise the range check
                sens = 0.4
            out[ch] = CalResult(ch, round(sens, 4), round(0.0457 + 0.001 * self.rng.random(), 6),
                                float(self.rng.normal(0, 0.02)), float(self.rng.normal(0, 0.02)))
        return out

    def calibfit(self) -> dict[int, CalResult]:
        self.last_cal = dict(self._calfit)
        return self.last_cal

    # -- capture -------------------------------------------------------------
    def cap_configure(self, post: int, trigger: str) -> None:
        self.post = int(post)
        self.trigger = trigger
        self.trg = {"int": "1,1,1", "ext rising": "1,0,1", "ext falling": "1,0,0"}[trigger]

    def cap_arm(self) -> None:
        if self.state != IDLE:
            raise RuntimeError(f"{self.name}: cannot arm, state {self.state}")
        for ev in (self._soft, self._ext, self._abort, self._armed, self._stopped):
            ev.clear()
        threading.Thread(target=self._run_shot, args=(self.trg != "1,1,1",),
                         daemon=True, name=f"sim-{self.name}").start()

    def _run_shot(self, external_trigger: bool, button: bool = False) -> None:
        self._sleep(0.3)
        self.state = ARM
        self._armed.set()
        self.emit("CAPTURE", "started")
        if not button:
            t0 = time.monotonic()
            while True:
                if self._abort.is_set():
                    self.state = IDLE
                    self._stopped.set()
                    self.emit("CAPTURE", "finished", aborted=True)
                    return
                if (external_trigger and self._ext.is_set()) or \
                        (not external_trigger and self._soft.is_set()):
                    break
                if external_trigger and self.ext_auto_s is not None and \
                        time.monotonic() - t0 > self.ext_auto_s * self.time_scale:
                    log.info("%s: (sim) external trigger", self.name)
                    break
                time.sleep(0.05)
        self.state = RUN
        end = time.monotonic() + self.post / SAMPLE_RATE * self.time_scale
        while time.monotonic() < end and not self._abort.is_set():
            time.sleep(0.05)
        self._shot_no += 1
        self._last_shot_seed = int(self.rng.integers(1 << 31))
        self.buffer_mode = "CAP"
        self.state = IDLE
        self._stopped.set()
        self.emit("CAPTURE", "finished", shot=self._shot_no)

    def cap_wait_armed(self, abort: threading.Event) -> None:
        self.wait_for(self._armed.is_set, abort)

    def cap_soft_trigger(self) -> None:
        self._soft.set()

    def cap_wait_stopped(self, abort: threading.Event) -> None:
        self.wait_for(self._stopped.is_set, abort)

    def cap_finish(self) -> None:
        self._sleep(0.1)

    def abort(self) -> None:
        self._abort.set()

    # -- simulated panel / external sources -----------------------------------
    def fire_ext_trigger(self) -> None:
        self._ext.set()

    def press_capture(self) -> None:
        """CAPTURE push-button on the BOLO8 panel."""
        if self.state != IDLE or self._activity:
            log.warning("%s: (sim) busy, button ignored", self.name)
            return
        for ev in (self._abort, self._armed, self._stopped):
            ev.clear()
        threading.Thread(target=self._run_shot, args=(False, True), daemon=True).start()

    def press_calibrate(self) -> None:
        """CALIBRATE push-button on the BOLO8 panel."""
        if self.state != IDLE or self._activity:
            log.warning("%s: (sim) busy, button ignored", self.name)
            return
        threading.Thread(target=self.cal_run, args=(CalMode.CAL_FLUSH,), daemon=True).start()

    # -- data ----------------------------------------------------------------
    def offload(self, mode: str) -> Shot:
        nchan = self._nchan
        nsam = self.post if mode == "CAP" else 50_000
        dtype = make_dtype(nchan, mode)
        self._sleep(12.0 * nsam * dtype.itemsize / 19e6)        # D-TACQ: ~12 s for 19 MB
        rng = np.random.default_rng(self._last_shot_seed)
        t = np.arange(nsam) / SAMPLE_RATE
        data = np.zeros(nsam, dtype=dtype)
        for ch in range(1, nchan + 1):
            on = ch in self.active
            cal = self._calfit.get(ch, CalResult(ch, 4.3, 0.046, 0.0, 0.0))
            if mode == "CAP":
                mag, phi, pwr = self._sim_capture(t, ch, on, cal, rng)
                data[f"{ch}_MAG"] = _codes(mag, "MAG")
                data[f"{ch}_PHI"] = _codes(phi, "PHI")
                data[f"{ch}_PWR"] = _codes(pwr, "PWR")
            else:
                mag, phi, ohi, ohv = self._sim_cal(t, ch, on, cal, rng)
                data[f"{ch}_MAG"] = _codes(mag, "MAG")
                data[f"{ch}_PHI"] = _codes(phi, "PHI")
                data[f"{ch}_OHI"] = _codes(ohi, "OHI", np.int16)
                data[f"{ch}_OHV"] = _codes(ohv, "OHV", np.int16)
        return Shot(uut=self.name, mode=mode, data=data, nchan=nchan,
                    active=list(self.active), timestamp=dt.datetime.now())

    def _sim_capture(self, t, ch, on, cal, rng):
        # strobe: bursts of short and long flashes, like the guide's LED pattern
        period = 1.6
        phase = np.mod(t + 0.05 * ch, period)
        light = ((phase < 0.2) | ((phase > 0.6) & (phase < 0.7)) | ((phase > 1.0) & (phase < 1.4)))
        p_in = light * (8.0 + 1.5 * ch) * on                    # absorbed power [mW]
        # first-order bolometer: tau dA/dt + A = sens * P   (A in mV, P in mW)
        a = _first_order(p_in * cal.sensitivity, cal.tau)
        offset = 0.0 if self.calibrated else 3.0 * np.sin(ch)
        noise = 0.05 if on else 0.02
        mag = a + offset + rng.normal(0, noise, t.size)
        phi = 0.02 * np.sin(ch) + rng.normal(0, 0.002, t.size)
        if self.calibrated:
            pwr = p_in + rng.normal(0, 0.1, t.size)
        else:
            pwr = (a + offset) / cal.sensitivity + rng.normal(0, 0.1, t.size)
        return mag, phi, pwr

    def _sim_cal(self, t, ch, on, cal, rng):
        heat = ((t > 0.5) & (t < 2.5)).astype(float) * on
        ohv = 1.2 * heat + rng.normal(0, 0.002, t.size)        # volts
        ohi = 12.0 * heat + rng.normal(0, 0.02, t.size)        # mA
        a = _first_order(ohv * ohi * cal.sensitivity, cal.tau)
        mag = a + rng.normal(0, 0.05, t.size)
        phi = 0.02 * np.sin(ch) + rng.normal(0, 0.002, t.size)
        return mag, phi, ohi, ohv


def _first_order(x: np.ndarray, tau: float) -> np.ndarray:
    """Low-pass x with time constant tau (FFT convolution, no scipy needed)."""
    n = x.size
    k = np.exp(-np.arange(min(n, int(10 * tau * SAMPLE_RATE) + 1)) / (tau * SAMPLE_RATE))
    k /= k.sum()
    nfft = 1 << (n + k.size - 2).bit_length()
    return np.fft.irfft(np.fft.rfft(x, nfft) * np.fft.rfft(k, nfft), nfft)[:n]


def _codes(values: np.ndarray, qty: str, dtype=np.int32) -> np.ndarray:
    info = np.iinfo(dtype)
    return np.clip(np.round(values / QUANTITIES[qty][0]), info.min, info.max).astype(dtype)
