"""Command line entry point: ``bolo-gui [options] UUT [UUT ...]``.

Examples::

    bolo-gui acq2206_077                    real box
    bolo-gui acq2206_077 acq2206_078        two boxes
    bolo-gui sim:demo sim:big:32            two simulated boxes (8 and 32 channels)
    bolo-gui --selftest sim:demo            headless cal -> capture -> save, then exit
"""

from __future__ import annotations

import argparse
import logging
import sys
import tempfile
from pathlib import Path

from . import __version__
from .config import TRIGGERS, Settings, parse_channels


def get_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bolo-gui", description="BOLO8 calibrate / capture / save GUI")
    p.add_argument("uuts", nargs="*",
                   help="box hostnames, or sim:NAME[:NCHAN] for a simulator "
                        "(default: the boxes from the last session)")
    p.add_argument("--data-dir", help="where shots are saved (default ~/bolo_data)")
    p.add_argument("--post", type=int, help="POST samples per capture (10 kHz; default 100000)")
    p.add_argument("--trg", choices=TRIGGERS, help="trigger source")
    p.add_argument("--active-chan", help='active channels, ";" between boxes, e.g. "1,2,3;1-8"')
    p.add_argument("--save-scaled", action="store_true", default=None,
                   help="save physical units (float64, like bolo_demux.py --save_scaled) "
                        "instead of raw codes")
    p.add_argument("--format", choices=("dat", "hdf5", "both"), dest="save_format",
                   help="save format: D-TACQ .dat files, HDF5, or both (default dat)")
    p.add_argument("--autosave", action="store_true", default=None, help="save every capture")
    p.add_argument("--sim-nchan", type=int, default=8, help="channels of sim:NAME boxes")
    p.add_argument("--sim-speed", type=float, default=0.3,
                   help="simulator time scale (1 = real time)")
    p.add_argument("--selftest", action="store_true",
                   help="no GUI: connect, calibrate, capture, save, reload, exit")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def _settings_from_args(args) -> Settings:
    s = Settings.load(args.data_dir)
    if args.post:
        s.post = args.post
    if args.trg:
        s.trigger = args.trg
    if args.save_scaled:
        s.save_scaled = True
    if args.save_format:
        s.save_format = args.save_format
    if args.autosave:
        s.autosave = True
    if args.active_chan:
        per_box = args.active_chan.split(";")
        for i, uut in enumerate(args.uuts):
            spec = per_box[i] if i < len(per_box) else per_box[0]
            s.active[uut.split(":")[1] if uut.startswith("sim:") else uut] = parse_channels(spec)
    return s


def _setup_logging(data_dir: str, verbose: bool) -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)
    try:
        d = Path(data_dir).expanduser()
        d.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(d / "bolo_gui.log")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError as e:
        logging.warning("no log file: %s", e)
    logging.getLogger("matplotlib").setLevel(logging.WARNING)
    logging.getLogger("PIL").setLevel(logging.WARNING)


def selftest(devs, settings: Settings) -> int:
    """Headless end-to-end run; also useful on site to check a box without the GUI."""
    from . import sequencer
    from .data import load_shots, save_shots
    from .device import CalMode

    log = logging.getLogger("selftest")
    for d in devs:
        d.connect()
    sequencer.set_active(devs, settings.active)
    sequencer.calibrate(devs, CalMode.CAL_FLUSH)
    for d in devs:
        bad = [ch for ch, r in d.last_cal.items() if not r.in_range()]
        log.info("%s: %d channels calibrated, out of range: %s", d.name, len(d.last_cal), bad or "none")
    sequencer.capture(devs, settings.post, "int")
    shots = sequencer.offload(devs, "CAP", {"post": settings.post, "trigger": "int"})
    folder = save_shots(shots, settings.data_dir, note="selftest", fmt=settings.save_format,
                        scaled=settings.save_scaled)
    back = load_shots(folder)
    ok = all((a.data == b.data).all() for a, b in zip(shots, back))
    log.info("saved to %s, reload %s", folder, "OK" if ok else "MISMATCH")
    for d in devs:
        d.close()
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> None:
    args = get_parser().parse_args(argv)
    if args.selftest and not args.data_dir:
        args.data_dir = tempfile.mkdtemp(prefix="bolo_selftest_")
    settings = _settings_from_args(args)
    _setup_logging(settings.data_dir, args.verbose)

    uuts = args.uuts or ([] if args.selftest else settings.uuts)
    from .device import make_device
    devs = [make_device(u, args.sim_nchan, args.sim_speed) for u in uuts]

    if args.selftest:
        if not devs:
            devs = [make_device("sim:selftest", args.sim_nchan, 0.05)]
        sys.exit(selftest(devs, settings))

    import tkinter as tk
    from .device.hapi_device import install_sigint_guard
    from .ui.app import BoloApp

    install_sigint_guard()
    root = tk.Tk()
    app = BoloApp(root, devs, settings, args.sim_nchan, args.sim_speed)
    if not devs:
        root.after(200, app.add_device_dialog)
    root.mainloop()


if __name__ == "__main__":
    main()
