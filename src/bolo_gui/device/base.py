"""Device interface: everything the app needs from one BOLO8 box.

The GUI and the sequencer only talk to this interface. ``HapiDevice`` drives
real hardware through acq400_hapi; ``SimDevice`` produces synthetic data so the
app can be developed and tested without a box. To support a new kind of
hardware, subclass ``BoloDevice`` and register it in ``device/__init__.py``.
"""

from __future__ import annotations

import enum
import logging
import threading
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable

from ..data import Shot

log = logging.getLogger(__name__)


class CalMode(enum.IntEnum):
    """Calibration flavours; values match bolo8_cal_cap_loop.py --cal=N."""

    CAL_FLUSH = 1      # normal: calibrate, then flush the filters (BOLO8_CAL)
    CAL_ONLY = 2       # calibrate without flush, cal data can be offloaded (BOLO8_CAL1)
    FLUSH = 3          # flush only; required after CAL_ONLY (BOLO8_CAL2)

    @property
    def label(self) -> str:
        return {1: "Calibrate + flush", 2: "Calibrate, no flush", 3: "Flush only"}[self.value]


class Aborted(Exception):
    """Raised by blocking waits when the operator pressed Abort."""


@dataclass
class ShotEvent:
    """Something happened on a box. Sent from a device thread; marshal to the UI."""

    uut: str
    kind: str              # "CAPTURE" or "CAL"
    phase: str             # "started" or "finished"
    source: str            # "gui" or "external"
    detail: dict = field(default_factory=dict)


@dataclass
class CalResult:
    """One line of calibfit.log."""

    channel: int
    sensitivity: float
    tau: float
    I0: float
    Q0: float

    def in_range(self) -> bool:
        from ..config import SENS_RANGE, TAU_RANGE
        return (SENS_RANGE[0] <= self.sensitivity <= SENS_RANGE[1]
                and TAU_RANGE[0] <= self.tau <= TAU_RANGE[1])


def parse_calibfit(text: str) -> dict[int, CalResult]:
    """Parse calibfit.log (``ch sens tau I0 Q0`` per line); also accepts the
    '>'-prefixed lines in a calibration transcript."""
    out = {}
    for line in text.splitlines():
        parts = line.lstrip(">").split()
        if len(parts) != 5:
            continue
        try:
            ch = int(parts[0])
            vals = [float(x) for x in parts[1:]]
        except ValueError:
            continue
        out[ch] = CalResult(ch, *vals)
    return out


class BoloDevice(ABC):
    """One acquisition box (UUT).

    Blocking methods are called from the worker thread, never from the Tk thread.
    Capture is split into steps so the sequencer can run each step on all boxes
    before moving on (arm all, then trigger all), as bolo8_cal_cap_loop.py does.
    """

    def __init__(self, name: str):
        self.name = name
        self.buffer_mode: str | None = None    # layout of the data held in the box
        self.needs_flush = False               # set after CalMode.CAL_ONLY
        self.last_cal: dict[int, CalResult] = {}
        self._activity: str | None = None      # what the GUI is doing right now
        self._listeners: list[Callable[[ShotEvent], None]] = []
        self._lock = threading.Lock()

    # -- connection ---------------------------------------------------------
    @abstractmethod
    def connect(self) -> None:
        """Open the connection (may take a few seconds)."""

    def close(self) -> None:
        pass

    @property
    @abstractmethod
    def nchan(self) -> int:
        """Number of bolometer channels (NCHAN / 3)."""

    @abstractmethod
    def info(self) -> dict:
        """Versions etc. for shot.json."""

    # -- configuration ------------------------------------------------------
    @abstractmethod
    def set_active(self, channels: list[int]) -> None:
        """Select channels with foils; [] = leave the box setting unchanged."""

    # -- calibration (see CalMode) -----------------------------------------
    @abstractmethod
    def cal_prepare(self) -> None:
        """Remember the trigger setting and switch to soft trigger."""

    @abstractmethod
    def cal_run(self, mode: CalMode) -> str:
        """Run the calibration service; blocks; returns the transcript."""

    @abstractmethod
    def cal_restore(self) -> None:
        """Restore the trigger setting saved by cal_prepare()."""

    @abstractmethod
    def calibfit(self) -> dict[int, CalResult]:
        """Latest calibration result from the box."""

    # -- capture -------------------------------------------------------------
    @abstractmethod
    def cap_configure(self, post: int, trigger: str) -> None:
        """Set POST samples and trigger source ('int', 'ext rising', 'ext falling')."""

    @abstractmethod
    def cap_arm(self) -> None: ...

    @abstractmethod
    def cap_wait_armed(self, abort: threading.Event) -> None: ...

    @abstractmethod
    def cap_soft_trigger(self) -> None: ...

    @abstractmethod
    def cap_wait_stopped(self, abort: threading.Event) -> None: ...

    @abstractmethod
    def cap_finish(self) -> None:
        """Post-shot housekeeping (DSP reset)."""

    @abstractmethod
    def abort(self) -> None:
        """Best effort: stop a running shot."""

    # -- data ----------------------------------------------------------------
    @abstractmethod
    def offload(self, mode: str) -> Shot:
        """Read the data held in the box. mode 'CAP' or 'CAL' selects the layout."""

    # -- events (shots started by the panel buttons or an external source) ----
    def add_listener(self, cb: Callable[[ShotEvent], None]) -> None:
        self._listeners.append(cb)

    def emit(self, kind: str, phase: str, source: str | None = None, **detail) -> None:
        ev = ShotEvent(self.name, kind, phase, source or self.event_source(), detail)
        for cb in list(self._listeners):
            try:
                cb(ev)
            except Exception:
                log.exception("listener failed")

    def event_source(self) -> str:
        return "gui" if self._activity else "external"

    @contextmanager
    def gui_activity(self, what: str):
        """Mark the box as driven by the GUI, so the monitor does not report
        the resulting state changes as external shots."""
        self._activity = what
        try:
            yield
        finally:
            self._activity = None

    @staticmethod
    def wait_for(cond: Callable[[], bool], abort: threading.Event,
                 timeout: float | None = None, poll: float = 0.1) -> None:
        """Poll cond() until true; raise Aborted on abort, TimeoutError on timeout."""
        import time
        t0 = time.monotonic()
        while not cond():
            if abort.wait(poll):
                raise Aborted()
            if timeout is not None and time.monotonic() - t0 > timeout:
                raise TimeoutError("timed out")

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.name}>"
