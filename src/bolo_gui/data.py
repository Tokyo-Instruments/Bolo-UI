"""Shot data: layout, scaling, saving and loading.

Saved layout matches D-TACQ's bolo_demux.py so existing analysis keeps working::

    <data_dir>/<YYYYmmdd_HHMMSS>/<uut>.<ch>_<QTY>.<CAP|CAL>.dat   raw codes, no header
    <data_dir>/<YYYYmmdd_HHMMSS>/shot.json                          metadata (added by this app)
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .config import LAYOUT, QUANTITIES, SAMPLE_RATE, SKIP_SAMPLES

TIMESTAMP_FMT = "%Y%m%d_%H%M%S"


def make_dtype(nchan: int, mode: str) -> np.dtype:
    """Structured dtype for one sample of all channels (bolo_demux.py)."""
    fields = []
    for ch in range(1, nchan + 1):
        for qty, t in LAYOUT[mode]:
            fields.append((f"{ch}_{qty}", t))
    return np.dtype(fields)


def demux(raw: np.ndarray, nchan: int, mode: str) -> np.ndarray:
    """Turn hapi read_channels() output (NCHAN x nsam int32) into a structured array."""
    return np.ascontiguousarray(raw.T).view(make_dtype(nchan, mode)).reshape(-1)


@dataclass
class Shot:
    """Data offloaded from one UUT after one capture or calibration."""

    uut: str
    mode: str                         # "CAP" or "CAL"
    data: np.ndarray                  # structured array, see make_dtype
    nchan: int
    active: list[int] = field(default_factory=list)   # [] means all
    timestamp: dt.datetime = field(default_factory=dt.datetime.now)
    source: str = "gui"               # "gui" or "external" (panel button / ext start)
    meta: dict = field(default_factory=dict)          # post, trigger, versions, calibfit ...

    @property
    def quantities(self) -> list[str]:
        return [q for q, _ in LAYOUT[self.mode]]

    @property
    def channels(self) -> list[int]:
        return self.active or list(range(1, self.nchan + 1))

    @property
    def nsam(self) -> int:
        return len(self.data)

    def raw(self, ch: int, qty: str) -> np.ndarray:
        return self.data[f"{ch}_{qty}"]

    def values(self, ch: int, qty: str) -> np.ndarray:
        """Channel data in physical units (see config.QUANTITIES)."""
        return self.raw(ch, qty) * QUANTITIES[qty][0]

    def time(self) -> np.ndarray:
        return np.arange(self.nsam) / SAMPLE_RATE


def save_shots(shots: list[Shot], data_dir: str | Path, note: str = "",
               extra: dict | None = None) -> Path:
    """Write all shots of one acquisition into one timestamped folder; return it."""
    if not shots:
        raise ValueError("nothing to save")
    ts = shots[0].timestamp
    root = Path(data_dir).expanduser()
    folder = root / ts.strftime(TIMESTAMP_FMT)
    n = 1
    while folder.exists():                       # two shots in the same second
        folder = root / f"{ts.strftime(TIMESTAMP_FMT)}_{n}"
        n += 1
    folder.mkdir(parents=True)

    meta = {
        "timestamp": ts.isoformat(timespec="seconds"),
        "note": note,
        "sample_rate": SAMPLE_RATE,
        "skip_samples": SKIP_SAMPLES,
        "scales": {q: {"scale": s, "unit": u} for q, (s, u, _) in QUANTITIES.items()},
        "uuts": {},
        **(extra or {}),
    }
    for shot in shots:
        for name in shot.data.dtype.names:
            shot.data[name].tofile(folder / f"{shot.uut}.{name}.{shot.mode}.dat")
        meta["uuts"][shot.uut] = {
            "mode": shot.mode,
            "nchan": shot.nchan,
            "nsam": shot.nsam,
            "active_channels": shot.active,
            "source": shot.source,
            **shot.meta,
        }
    (folder / "shot.json").write_text(json.dumps(meta, indent=2, default=str))
    return folder


_DAT_RE = re.compile(r"^(?P<uut>.+)\.(?P<ch>\d+)_(?P<qty>[A-Z]+)\.(?P<mode>CAP|CAL)\.dat$")


def load_shots(folder: str | Path) -> list[Shot]:
    """Read a folder written by save_shots() or by bolo_shot.sh / bolo_demux.py."""
    folder = Path(folder)
    meta = {}
    if (folder / "shot.json").exists():
        meta = json.loads((folder / "shot.json").read_text())

    found: dict[tuple[str, str], int] = {}       # (uut, mode) -> nchan
    for p in folder.glob("*.dat"):
        m = _DAT_RE.match(p.name)
        if m:
            key = (m["uut"], m["mode"])
            found[key] = max(found.get(key, 0), int(m["ch"]))

    ts = dt.datetime.fromtimestamp(folder.stat().st_mtime)
    if "timestamp" in meta:
        ts = dt.datetime.fromisoformat(meta["timestamp"])

    shots = []
    for (uut, mode), nchan_files in sorted(found.items()):
        umeta = dict(meta.get("uuts", {}).get(uut, {}))
        nchan = int(umeta.pop("nchan", nchan_files))
        dtype = make_dtype(nchan, mode)
        cols = {}
        for name in dtype.names:
            p = folder / f"{uut}.{name}.{mode}.dat"
            if p.exists():
                cols[name] = np.fromfile(p, dtype=dtype.fields[name][0])
        nsam = min(len(c) for c in cols.values())
        data = np.zeros(nsam, dtype=dtype)
        for name, col in cols.items():
            data[name] = col[:nsam]
        umeta.pop("mode", None)
        umeta.pop("nsam", None)
        shots.append(Shot(
            uut=uut, mode=mode, data=data, nchan=nchan,
            active=umeta.pop("active_channels", []),
            timestamp=ts, source=umeta.pop("source", "file"), meta=umeta,
        ))
    if not shots:
        raise FileNotFoundError(f"no BOLO .dat files in {folder}")
    return shots


def save_text(text: str, data_dir: str | Path, name: str) -> Path:
    p = Path(data_dir).expanduser() / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def append_cal_history(data_dir: str | Path, uut: str, results: dict, source: str = "gui",
                       when: dt.datetime | None = None) -> Path:
    """Append one calibration (dict ch -> CalResult) to calibration_history.csv."""
    import csv
    p = Path(data_dir).expanduser() / "calibration_history.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    new = not p.exists()
    when = when or dt.datetime.now()
    with p.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["timestamp", "uut", "channel", "sensitivity", "tau", "I0", "Q0",
                        "status", "source"])
        for ch, r in sorted(results.items()):
            w.writerow([when.isoformat(timespec="seconds"), uut, ch, r.sensitivity, r.tau,
                        r.I0, r.Q0, "Pass" if r.in_range() else "Fail", source])
    return p
