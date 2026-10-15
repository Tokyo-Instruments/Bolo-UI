"""Device backends. make_device() turns a command-line spec into a device.

Spec formats:
    acq2206_077          real box via acq400_hapi (hostname must resolve)
    sim:acq2206_077      simulated box, 8 channels
    sim:test:32          simulated box with 32 channels
"""

from __future__ import annotations

from .base import Aborted, BoloDevice, CalMode, CalResult, ShotEvent, parse_calibfit
from .hapi_device import HapiDevice
from .sim_device import SimDevice

__all__ = ["Aborted", "BoloDevice", "CalMode", "CalResult", "ShotEvent", "parse_calibfit",
           "HapiDevice", "SimDevice", "make_device"]


def make_device(spec: str, sim_nchan: int = 8, sim_time_scale: float = 0.3) -> BoloDevice:
    dev: BoloDevice
    if spec.startswith("sim:"):
        parts = spec.split(":")
        name = parts[1] or "sim"
        nchan = int(parts[2]) if len(parts) > 2 and parts[2] else sim_nchan
        dev = SimDevice(name, nchan=nchan, time_scale=sim_time_scale)
    else:
        dev = HapiDevice(spec)
    dev.spec = spec
    return dev
