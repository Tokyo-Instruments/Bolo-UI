"""Calibration / capture / offload sequences across one or more boxes.

Ported from D-TACQ's bolo8_cal_cap_loop.py. Every step runs on all boxes
before the next step starts (arm all, then trigger all, then wait for all).
No Tk in here: the functions can be used from scripts and tests as well::

    from bolo_gui.device import make_device, CalMode
    from bolo_gui import sequencer
    devs = [make_device("acq2206_077")]
    for d in devs: d.connect()
    sequencer.calibrate(devs, CalMode.CAL_FLUSH)
    sequencer.capture(devs, post=100000, trigger="int")
    shots = sequencer.offload(devs)

To add a new operation, add a function here and a button in ui/acquire.py.
"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import ExitStack
from dataclasses import asdict
from typing import Callable

from .data import Shot
from .device import Aborted, BoloDevice, CalMode

log = logging.getLogger(__name__)

#: D-TACQ: "unfortunately this sleep seems to be necessary, else subsequent shot HANGS"
CAL_SETTLE_S = 2.0


def _gui_activity(devs: list[BoloDevice], what: str) -> ExitStack:
    stack = ExitStack()
    for d in devs:
        stack.enter_context(d.gui_activity(what))
    return stack


def check_ready(devs: list[BoloDevice], mode: CalMode | None = None) -> None:
    """Refuse to run anything but a flush after a calibration without flush."""
    if mode == CalMode.FLUSH:
        return
    pending = [d.name for d in devs if d.needs_flush]
    if pending:
        raise RuntimeError(
            f"{', '.join(pending)}: calibration was run without flush. "
            "Offload the calibration data if needed, then run 'Flush only' first.")


def set_active(devs: list[BoloDevice], active: dict[str, list[int]]) -> None:
    for d in devs:
        d.set_active(active.get(d.name, []))


def calibrate(devs: list[BoloDevice], mode: CalMode = CalMode.CAL_FLUSH,
              settle_s: float = CAL_SETTLE_S) -> dict[str, str]:
    """Run a calibration (or flush) on all boxes; returns transcripts per box.
    Remove the light source from the foils first."""
    check_ready(devs, mode)
    transcripts = {}
    with _gui_activity(devs, "CAL"):
        for d in devs:
            d.cal_prepare()
        try:
            for d in devs:
                log.info("%s: %s", d.name, mode.label)
                transcripts[d.name] = d.cal_run(mode)
            time.sleep(settle_s)
        finally:
            for d in devs:
                d.cal_restore()
    for d in devs:
        d.needs_flush = mode == CalMode.CAL_ONLY
        if mode != CalMode.FLUSH:
            d.calibfit()
    if mode == CalMode.CAL_ONLY:
        log.warning("Calibration without flush: run 'Flush only' before the next capture")
    return transcripts


def calibrate_with_response(devs: list[BoloDevice], settle_s: float = CAL_SETTLE_S
                            ) -> tuple[dict[str, str], list[Shot]]:
    """Calibrate + flush, but read the calibration data (MAG/PHI/OHI/OHV) in between.

    A normal calibration (BOLO8_CAL) flushes the filters and with them the
    calibration data. This runs the documented manual sequence instead
    (guide §4.3.1/4.3.2): calibrate without flush, offload, flush only.
    The flush runs even if reading the data fails.
    """
    transcripts = calibrate(devs, CalMode.CAL_ONLY, settle_s)
    shots: list[Shot] = []
    try:
        shots = offload(devs, "CAL", {"calibration": CalMode.CAL_ONLY.label})
    finally:
        flushed = calibrate(devs, CalMode.FLUSH, settle_s)
    return {n: transcripts[n] + flushed.get(n, "") for n in transcripts}, shots


def capture(devs: list[BoloDevice], post: int, trigger: str = "int",
            abort: threading.Event | None = None) -> None:
    """Arm all boxes, trigger (soft trigger for 'int'), wait until all stopped."""
    check_ready(devs)
    abort = abort or threading.Event()
    with _gui_activity(devs, "CAPTURE"):
        for d in devs:
            d.cap_configure(post, trigger)
        try:
            for d in devs:
                d.cap_arm()
            for d in devs:
                d.cap_wait_armed(abort)
            if trigger == "int":
                for d in devs:
                    d.cap_soft_trigger()
            else:
                log.info("armed, waiting for external trigger (%s)", trigger)
            for d in devs:
                d.cap_wait_stopped(abort)
        except Aborted:
            for d in devs:
                d.abort()
            raise
        finally:
            for d in devs:
                try:
                    d.cap_finish()
                except Exception:                        # noqa: BLE001
                    log.exception("%s: DSP reset failed", d.name)


def offload(devs: list[BoloDevice], mode: str | None = None, meta: dict | None = None,
            source: str = "gui") -> list[Shot]:
    """Read the data from all boxes. mode None = layout of the last shot on each box."""
    shots = []
    for d in devs:
        m = mode or d.buffer_mode or "CAP"
        if d.buffer_mode and m != d.buffer_mode:
            log.warning("%s: box holds %s data, reading it as %s", d.name, d.buffer_mode, m)
        log.info("%s: reading %s data ...", d.name, m)
        shot = d.offload(m)
        shot.source = source
        shot.meta.update(meta or {})
        shot.meta["box"] = d.info()
        if d.last_cal:
            shot.meta["calibfit"] = {ch: asdict(c) for ch, c in d.last_cal.items()}
        shots.append(shot)
    return shots


def run_cycles(devs: list[BoloDevice], cycles: int, *, cal_mode: CalMode | None,
               do_capture: bool, post: int, trigger: str,
               abort: threading.Event | None = None,
               on_capture: Callable[[list[Shot]], None] | None = None,
               settle_s: float = CAL_SETTLE_S) -> None:
    """bolo8_cal_cap_loop.py --shots=N: per cycle optionally calibrate, then capture.
    on_capture receives the offloaded shots of every cycle (plot / save there)."""
    abort = abort or threading.Event()
    for n in range(1, cycles + 1):
        if abort.is_set():
            raise Aborted()
        log.info("Cycle %d/%d", n, cycles)
        if cal_mode is not None:
            calibrate(devs, cal_mode, settle_s=settle_s)
        if do_capture:
            capture(devs, post, trigger, abort)
            if on_capture:
                on_capture(offload(devs, "CAP", {"post": post, "trigger": trigger,
                                                 "cycle": n, "cycles": cycles}))
