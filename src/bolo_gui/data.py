"""Shot data: layout, scaling, saving and loading.

Each acquisition goes into one folder, in one or both formats (SAVE_FORMATS)::

    <data_dir>/<YYYYmmdd_HHMMSS>/<uut>.<ch>_<QTY>.<CAP|CAL>.dat   "dat": as D-TACQ's bolo_demux.py,
    <data_dir>/<YYYYmmdd_HHMMSS>/shot.json                          raw codes, no header + metadata
    <data_dir>/<YYYYmmdd_HHMMSS>/shot.h5                            "hdf5": everything in one file

HDF5 layout (raw codes; value = raw * attrs["scale"] in attrs["unit"])::

    /                       attrs: timestamp, note, sample_rate, skip_samples, format, ...
    /<uut>/                 attrs: mode, nchan, nsam, active_channels, source, post, trigger,
                                   meta_json (all metadata as JSON)
    /<uut>/<QTY>            int32/int16 [nchan, nsam], row i = channel i+1; attrs scale, unit
    /<uut>/calibfit         table channel, sensitivity, tau, I0, Q0 (if calibrated)

    import h5py
    with h5py.File("shot.h5") as f:
        pwr = f["acq2206_077/PWR"]
        mw = pwr[0] * pwr.attrs["scale"]            # channel 1 in mW
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


SAVE_FORMATS = {"dat": "D-TACQ .dat", "hdf5": "HDF5", "both": ".dat + HDF5"}
H5_NAME = "shot.h5"
H5_FORMAT = "bolo_gui/1"


def save_shots(shots: list[Shot], data_dir: str | Path, note: str = "",
               extra: dict | None = None, fmt: str = "dat") -> Path:
    """Write all shots of one acquisition into one timestamped folder; return it.
    fmt: "dat" (D-TACQ layout + shot.json), "hdf5" (shot.h5) or "both"."""
    if not shots:
        raise ValueError("nothing to save")
    if fmt not in SAVE_FORMATS:
        raise ValueError(f"unknown save format {fmt!r}")
    if fmt in ("hdf5", "both"):
        _import_h5py()                           # fail before creating the folder
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
        "uuts": {shot.uut: _uut_meta(shot) for shot in shots},
        **(extra or {}),
    }
    if fmt in ("dat", "both"):
        for shot in shots:
            for name in shot.data.dtype.names:
                shot.data[name].tofile(folder / f"{shot.uut}.{name}.{shot.mode}.dat")
        (folder / "shot.json").write_text(json.dumps(meta, indent=2, default=str))
    if fmt in ("hdf5", "both"):
        _write_h5(folder / H5_NAME, shots, meta)
    return folder


def _uut_meta(shot: Shot) -> dict:
    return {"mode": shot.mode, "nchan": shot.nchan, "nsam": shot.nsam,
            "active_channels": shot.active, "source": shot.source, **shot.meta}


def _import_h5py():
    try:
        import h5py
    except ImportError as e:
        raise RuntimeError("HDF5 saving needs h5py: <venv>/bin/pip install h5py") from e
    return h5py


def _write_h5(path: Path, shots: list[Shot], meta: dict) -> None:
    h5py = _import_h5py()
    with h5py.File(path, "w") as f:
        f.attrs["format"] = H5_FORMAT
        for k in ("timestamp", "note", "sample_rate", "skip_samples"):
            f.attrs[k] = meta[k]
        for k, v in meta.items():
            if k not in ("uuts", "scales") and k not in f.attrs and isinstance(v, (str, int, float)):
                f.attrs[k] = v
        for shot in shots:
            g = f.create_group(shot.uut)
            umeta = meta["uuts"][shot.uut]
            for k in ("mode", "nchan", "nsam", "source", "post", "trigger"):
                if k in umeta:
                    g.attrs[k] = umeta[k]
            g.attrs["active_channels"] = np.asarray(shot.active, dtype=np.int32)
            g.attrs["meta_json"] = json.dumps(umeta, default=str)
            for qty, t in LAYOUT[shot.mode]:
                arr = np.stack([shot.raw(ch, qty) for ch in range(1, shot.nchan + 1)])
                ds = g.create_dataset(qty, data=arr.astype(t, copy=False),
                                      compression="gzip", compression_opts=4, shuffle=True,
                                      chunks=(1, min(shot.nsam, 65536)) if shot.nsam else None)
                scale, unit, title = QUANTITIES[qty]
                ds.attrs.update(scale=scale, unit=unit, title=title, first_channel=1)
            cal = shot.meta.get("calibfit")
            if cal:
                rows = [(int(ch), c["sensitivity"], c["tau"], c["I0"], c["Q0"])
                        for ch, c in sorted(cal.items(), key=lambda kv: int(kv[0]))]
                g.create_dataset("calibfit", data=np.array(rows, dtype=[
                    ("channel", "i4"), ("sensitivity", "f8"), ("tau", "f8"),
                    ("I0", "f8"), ("Q0", "f8")]))


def _read_h5(path: Path) -> list[Shot]:
    h5py = _import_h5py()
    shots = []
    with h5py.File(path, "r") as f:
        ts = dt.datetime.fromisoformat(f.attrs["timestamp"])
        for uut, g in f.items():
            umeta = json.loads(g.attrs.get("meta_json", "{}"))
            mode, nchan = str(g.attrs["mode"]), int(g.attrs["nchan"])
            dtype = make_dtype(nchan, mode)
            nsam = g[LAYOUT[mode][0][0]].shape[1]
            data = np.zeros(nsam, dtype=dtype)
            for qty, _ in LAYOUT[mode]:
                arr = g[qty][()]
                for ch in range(1, nchan + 1):
                    data[f"{ch}_{qty}"] = arr[ch - 1]
            for k in ("mode", "nchan", "nsam"):
                umeta.pop(k, None)
            shots.append(Shot(uut=uut, mode=mode, data=data, nchan=nchan,
                              active=[int(c) for c in umeta.pop("active_channels", [])],
                              timestamp=ts, source=umeta.pop("source", "file"), meta=umeta))
    return shots


_DAT_RE = re.compile(r"^(?P<uut>.+)\.(?P<ch>\d+)_(?P<qty>[A-Z]+)\.(?P<mode>CAP|CAL)\.dat$")


def load_shots(path: str | Path) -> list[Shot]:
    """Read a saved shot: a folder (written by save_shots(), bolo_shot.sh or
    bolo_demux.py) or an HDF5 file. A folder with both formats is read from the .dat files."""
    path = Path(path).expanduser()
    if path.is_file():
        return _read_h5(path)
    if not any(path.glob("*.dat")) and (path / H5_NAME).exists():
        return _read_h5(path / H5_NAME)
    return _read_dat(path)


def _read_dat(folder: Path) -> list[Shot]:
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
        raise FileNotFoundError(f"no BOLO .dat or {H5_NAME} files in {folder}")
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
