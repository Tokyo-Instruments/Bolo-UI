# BOLO8 Control (bolo-gui)

A small operator GUI for D-TACQ BOLO8 bolometer systems: calibrate, capture,
plot and save shots on one or more boxes, without Phoebus or a terminal. It
replaces the `bolo_shot.sh` + Phoebus-button stopgap (kept in `reference/`) and
uses the same hardware sequence as D-TACQ's `bolo8_cal_cap_loop.py`.

## Install (on site, Ubuntu)

Install the app into the venv that already has acq400_hapi (see HANDOVER.md):

```bash
~/PROJECTS/acq400_hapi/.venv/bin/pip install -e ~/PROJECTS/bolo_gui
~/PROJECTS/acq400_hapi/.venv/bin/bolo-gui acq2206_077
```

Calling `bolo-gui` by its absolute path needs no activated venv. For a menu
entry, copy `packaging/bolo-gui.desktop` to `~/.local/share/applications/` and
fix the path in `Exec=`. A Phoebus action button can run the same command.

Development (own venv, hapi optional):

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'     # add ,hw for acq400_hapi
.venv/bin/bolo-gui sim:acq2206_077 sim:big:32                  # two simulated boxes
.venv/bin/pytest
```

## Usage

```
bolo-gui [options] UUT [UUT ...]
  UUT                 hostname (must resolve, e.g. via /etc/hosts), or sim:NAME[:NCHAN]
  --data-dir DIR      default ~/bolo_data
  --post N            samples per capture at 10 kHz (default 100000 = 10 s)
  --trg {int,ext rising,ext falling}
  --active-chan "1,2,3;1-8"   per box, ';' separated
  --autosave          save every capture
  --selftest          no GUI: calibrate, capture, save, reload, exit (works on hardware too)
```

Without UUT arguments the boxes from the last session are used. Settings
(folder, POST, trigger, active channels, …) are remembered in
`<data-dir>/bolo_gui_settings.json`.

| Control | What it does |
| --- | --- |
| Devices | Tick the boxes an action applies to; ↻ reconnects, ✕ removes, *Add device…* adds one |
| Active channels | Foils connected per box (`BOLO_ACTIVE_CHAN`), up to 32 |
| Calibrate | *Calibrate + flush* (normal), *Calibrate, no flush* (calibration data can then be read with *Read data from box*; capture stays blocked until *Flush only*), *Flush only* |
| Capture | Arm all boxes, soft trigger (int) or wait for the external trigger, read the data, plot |
| Cycles | N × (optional calibration, capture), like `--shots=N` |
| Read data from box | Read whatever the box holds now (capture or calibration layout) |
| Watch for panel-button / external shots | When a shot is started from the BOLO8-PANEL buttons or elsewhere, read and plot it (and save it if autosave is on); after a panel calibration, refresh the calibration table |
| File ▸ Open shot… | Reopen a saved folder (also old `bolo_shot.sh` folders) |
| Simulator menu | For sim boxes: press the panel CAPTURE / CALIBRATE buttons, send an external trigger |

## Saved data

```
~/bolo_data/<YYYYmmdd_HHMMSS>/<uut>.<ch>_<QTY>.<CAP|CAL>.dat   raw codes, no header (as bolo_demux.py)
~/bolo_data/<YYYYmmdd_HHMMSS>/shot.json                          timestamp, note, POST, trigger, source,
                                                                 active channels, box versions, calibfit, scales
~/bolo_data/cal_logs/<ts>_<uut>_<mode>.log                       calibration transcripts
~/bolo_data/calibration_history.csv                              every calibration result
~/bolo_data/bolo_gui.log                                         application log
```

Samples are at 10 kHz; skip the first ~20. Scales (raw × scale): PWR 4.550e-3 mW,
MAG 7.109e-5 mV, PHI 1.8626e-9 rad, OHI 2.6041666e-3 mA, OHV 3.81469725e-5 V.

```python
from bolo_gui.data import load_shots
shot = load_shots("~/bolo_data/20261015_101500")[0]
t, p = shot.time(), shot.values(1, "PWR")      # seconds, mW
```

## Code layout and how to extend it

```
src/bolo_gui/
  cli.py              command line, --selftest
  config.py           constants (scales, rates, ranges) and persisted Settings
  data.py             Shot, .dat/.json save + load, calibration history
  sequencer.py        calibrate / capture / offload / cycles over several boxes (no GUI code)
  worker.py           background thread; results back to Tk via a queue
  device/base.py      BoloDevice interface every backend implements
  device/hapi_device.py   real box via acq400_hapi
  device/sim_device.py    simulator
  ui/app.py           main window; one method per action
  ui/acquire.py, devices.py, plot.py, calib.py, log.py   panels
```

- **New action:** write a function in `sequencer.py` (plain Python, testable),
  add a button in `ui/acquire.py`, and a method in `ui/app.py` that submits it to
  the worker (see the docstring at the top of `ui/app.py`).
- **New hardware variant:** subclass `BoloDevice` and return it from
  `device/__init__.py:make_device`.
- **Scripting without the GUI:** see the example at the top of `sequencer.py`.

## Open items

- **Panel buttons (TODO with D-TACQ):** a shot started from the BOLO8-PANEL is
  detected via hapi's status monitor (transient state changes). Whether it was a
  calibration or a capture is decided by a `*CALIBRATING` site knob if the box
  has one (found knobs are logged at connect), otherwise by `calibfit.log`
  changing within 20 s. Replace `HapiDevice._classify_external` once D-TACQ
  confirms how the buttons are exposed.
- Not yet tested against the real box. On site: run `bolo-gui --selftest
  acq2206_077`, then the GUI, and compare saved `.dat` files with `bolo_shot.sh save`.
- hapi's status monitor may SIGINT the process on a state jump that skips ARM;
  the app logs it and restarts the monitor instead of exiting.
