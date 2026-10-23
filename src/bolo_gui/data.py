"""Shot data: layout, scaling, saving and loading.

Each acquisition goes into one folder, in one or both formats (SAVE_FORMATS)::

    <data_dir>/<YYYYmmdd_HHMMSS>/<uut>.<CH>_<QTY>.<CAP|CAL>.dat  "dat": as D-TACQ's bolo_demux.py
    <data_dir>/<YYYYmmdd_HHMMSS>/shot.json                         (--save or --save_scaled) + metadata
    <data_dir>/<YYYYmmdd_HHMMSS>/shot.h5                           "hdf5": everything in one file

<CH> is zero-padded (01, 02, ...) as in bolo_demux.py since acq400_hapi 62e69a8;
older folders with 1, 2, ... still load.

By default values are the raw codes from the box (int32; OHI/OHV int16). With
scaled=True they are physical units as float64 (raw * scale, like
bolo_demux.py --save_scaled); shot.json then has "scaled": true.

HDF5 layout (value = data * attrs["scale"] in attrs["unit"]; scale is 1.0 for scaled data)::

    /                       attrs: timestamp, note, sample_rate, skip_samples, format, scaled, ...
    /<uut>/                 attrs: mode, nchan, nsam, active_channels, source, post, trigger,
                                   meta_json (all metadata as JSON)
    /<uut>/<QTY>            [nchan, nsam], row i = channel i+1; int32/int16 raw codes, or
                            float64 if scaled; attrs scale, unit, raw_scale, scaled
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


def dat_name(uut: str, ch: int, qty: str, mode: str) -> str:
    """File name as written by bolo_demux.py: acq2206_077.01_PWR.CAP.dat"""
    return f"{uut}.{ch:02d}_{qty}.{mode}.dat"


def scaled_values(shot: Shot, ch: int, qty: str) -> np.ndarray:
    """raw * scale as float64, exactly as bolo_demux.py --save_scaled writes it."""
    return shot.raw(ch, qty) * QUANTITIES[qty][0]


def save_shots(shots: list[Shot], data_dir: str | Path, note: str = "",
               extra: dict | None = None, fmt: str = "dat", scaled: bool = False) -> Path:
    """Write all shots of one acquisition into one timestamped folder; return it.
    fmt: "dat" (D-TACQ layout + shot.json), "hdf5" (shot.h5) or "both".
    scaled: store physical units (float64) instead of raw codes."""
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
        "scaled": scaled,
        "scales": {q: {"scale": s, "unit": u} for q, (s, u, _) in QUANTITIES.items()},
        "uuts": {shot.uut: _uut_meta(shot) for shot in shots},
        **(extra or {}),
    }
    if fmt in ("dat", "both"):
        for shot in shots:
            for ch in range(1, shot.nchan + 1):
                for qty in shot.quantities:
                    values = scaled_values(shot, ch, qty) if scaled else shot.raw(ch, qty)
                    values.tofile(folder / dat_name(shot.uut, ch, qty, shot.mode))
        (folder / "shot.json").write_text(json.dumps(meta, indent=2, default=str))
    if fmt in ("hdf5", "both"):
        _write_h5(folder / H5_NAME, shots, meta, scaled)
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


def _write_h5(path: Path, shots: list[Shot], meta: dict, scaled: bool = False) -> None:
    h5py = _import_h5py()
    with h5py.File(path, "w") as f:
        f.attrs["format"] = H5_FORMAT
        for k in ("timestamp", "note", "sample_rate", "skip_samples", "scaled"):
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
                chans = range(1, shot.nchan + 1)
                if scaled:
                    arr = np.stack([scaled_values(shot, ch, qty) for ch in chans])
                else:
                    arr = np.stack([shot.raw(ch, qty) for ch in chans]).astype(t, copy=False)
                ds = g.create_dataset(qty, data=arr,
                                      compression="gzip", compression_opts=4, shuffle=True,
                                      chunks=(1, min(shot.nsam, 65536)) if shot.nsam else None)
                scale, unit, title = QUANTITIES[qty]
                # value = data * scale in either case, so readers need no special casing
                ds.attrs.update(scale=1.0 if scaled else scale, raw_scale=scale, scaled=scaled,
                                unit=unit, title=title, first_channel=1)
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
            for qty, t in LAYOUT[mode]:
                ds = g[qty]
                arr = ds[()]
                if ds.attrs.get("scaled", False):
                    arr = _unscale(arr, qty, t)
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


def _unscale(values: np.ndarray, qty: str, raw_type) -> np.ndarray:
    """Physical units back to raw codes (exact: raw * scale is stored as float64)."""
    return np.rint(values / QUANTITIES[qty][0]).astype(raw_type)


def _looks_scaled(path: Path) -> bool:
    """Guess whether a .dat file without shot.json was written by --save_scaled.

    Scaled files are float64 in physical units (|x| below ~1e6). Raw int32 codes
    read as float64 give denormal (< 1e-300), NaN or huge numbers instead."""
    if path.stat().st_size % 8:
        return False
    x = np.fromfile(path, dtype=np.float64, count=4096)
    if x.size == 0 or not np.isfinite(x).all():
        return False
    a = np.abs(x[x != 0])
    return a.size == 0 or bool(((a > 1e-200) & (a < 1e12)).all())


def _read_dat(folder: Path) -> list[Shot]:
    meta = {}
    if (folder / "shot.json").exists():
        meta = json.loads((folder / "shot.json").read_text())

    # (uut, mode) -> {(ch, qty): path}; accepts 01_PWR (current) and 1_PWR (older) names
    found: dict[tuple[str, str], dict[tuple[int, str], Path]] = {}
    for p in folder.glob("*.dat"):
        m = _DAT_RE.match(p.name)
        if m:
            found.setdefault((m["uut"], m["mode"]), {})[(int(m["ch"]), m["qty"])] = p

    ts = dt.datetime.fromtimestamp(folder.stat().st_mtime)
    if "timestamp" in meta:
        ts = dt.datetime.fromisoformat(meta["timestamp"])

    shots = []
    for (uut, mode), files in sorted(found.items()):
        umeta = dict(meta.get("uuts", {}).get(uut, {}))
        nchan = int(umeta.pop("nchan", max(ch for ch, _ in files)))
        if "scaled" in meta:
            scaled = bool(meta["scaled"])
        else:
            scaled = _looks_scaled(files[min(files)])
        dtype = make_dtype(nchan, mode)
        cols = {}
        for (ch, qty), p in files.items():
            raw_type = dtype.fields.get(f"{ch}_{qty}", (None,))[0]
            if raw_type is None:
                continue
            if scaled:
                cols[f"{ch}_{qty}"] = _unscale(np.fromfile(p, dtype=np.float64), qty, raw_type)
            else:
                cols[f"{ch}_{qty}"] = np.fromfile(p, dtype=raw_type)
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
