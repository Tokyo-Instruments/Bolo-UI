"""Constants and user settings.

Scale factors and ranges come from D-TACQ's bolo_demux.py / bolo8_tester.py
(see README.md, section 6.1).
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

log = logging.getLogger(__name__)

SAMPLE_RATE = 10_000          # DSP output rate [Hz], not the ADC clock
SKIP_SAMPLES = 20             # filter start-up spike at the start of every shot
MAX_CHANNELS = 32             # UI grid size; a box reports its real count

# quantity -> (scale, unit, title). Raw codes * scale = value in unit.
QUANTITIES = {
    "PWR": (4.550e-3, "mW", "Power"),
    "MAG": (7.109e-5, "mV", "Magnitude"),
    "PHI": (1.8626e-9, "rad", "Phase"),
    "OHI": (2.6041666e-3, "mA", "Ohmic heating current"),
    "OHV": (3.81469725e-5, "V", "Ohmic heating voltage"),
}

# Per-channel quantities in the order the box sends them (BOLO guide §2.2)
LAYOUT = {
    "CAP": (("MAG", "i4"), ("PHI", "i4"), ("PWR", "i4")),
    "CAL": (("MAG", "i4"), ("PHI", "i4"), ("OHI", "i2"), ("OHV", "i2")),
}

# calibfit.log plausibility ranges (bolo8_tester.py)
SENS_RANGE = (1.0, 15.0)
TAU_RANGE = (0.0, 1.0)

TRIGGERS = ("int", "ext rising", "ext falling")

DEFAULT_DATA_DIR = Path.home() / "bolo_data"
SETTINGS_FILE = "bolo_gui_settings.json"


@dataclass
class Settings:
    """Operator settings; persisted as JSON in the data directory."""

    data_dir: str = str(DEFAULT_DATA_DIR)
    post: int = 100_000                    # samples after trigger (10 s at 10 kHz)
    trigger: str = "int"                   # one of TRIGGERS
    autosave: bool = False
    watch: bool = True                     # react to shots started outside the GUI
    cycles: int = 1
    save_format: str = "dat"                # "dat", "hdf5" or "both" (data.SAVE_FORMATS)
    save_scaled: bool = False              # physical units (float64) instead of raw codes
    cal_show_response: bool = True         # read calibration data before the flush
    plot_quantity: str = "PWR"
    active: dict = field(default_factory=dict)   # uut -> [channels]
    uuts: list = field(default_factory=list)     # device specs of the last session

    @property
    def path(self) -> Path:
        return Path(self.data_dir).expanduser() / SETTINGS_FILE

    @classmethod
    def load(cls, data_dir: str | Path | None = None) -> "Settings":
        s = cls()
        if data_dir:
            s.data_dir = str(data_dir)
        try:
            raw = json.loads(s.path.read_text())
            known = {f.name for f in fields(cls)}
            for k, v in raw.items():
                if k in known and k != "data_dir":
                    setattr(s, k, v)
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            log.warning("could not read settings %s: %s", s.path, e)
        return s

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(asdict(self), indent=2))
        except OSError as e:
            log.warning("could not save settings %s: %s", self.path, e)


def parse_channels(text: str) -> list[int]:
    """'1,2,5-8' -> [1, 2, 5, 6, 7, 8]. Empty or 'all' -> []."""
    text = text.strip()
    if not text or text.lower() == "all":
        return []
    out: list[int] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = (int(x) for x in part.split("-", 1))
            out.extend(range(a, b + 1))
        else:
            out.append(int(part))
    return sorted(set(out))


def format_channels(chans: list[int]) -> str:
    return ",".join(str(c) for c in sorted(chans))
