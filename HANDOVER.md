# BOLO8 tools: handover

Status as of 2026-10-15. Owner: Morten (Tokyo Instruments), for a customer site.

## Goal of the next step

Build a small **Python GUI app** for a D-TACQ BOLO8 bolometer system that lets an
operator calibrate, capture, see and save shots without Phoebus or a terminal.
It replaces the current stopgap: a Phoebus panel (`phoebus/bolo_shot.bob`) whose
buttons call a shell script (`bolo_shot.sh`) that runs D-TACQ's hapi scripts.
That stopgap works on site; the GUI should reproduce its behaviour first, then
improve on it.

Do not start from zero: the hardware control sequence and the data layout below
are taken from D-TACQ's own scripts and are confirmed working with the real box.

## System

| Item | Detail |
| --- | --- |
| Hardware | D-TACQ acq2206 carrier with BOLO8 (BOLO8-BLF) bolometer modules. 3 data channels per bolometer input: MAG, PHI, PWR |
| Box name | `acq2206_077`, fallback IP `192.168.0.77` (name, MAC and fallback IP also on the rear plate) |
| Control PC | Ubuntu (desktop, NetworkManager). Morten also tests from macOS |
| Network | Direct Ethernet cable. PC on a static IP in the box's subnet (`192.168.0.10/24`, set in the Ubuntu network settings), `192.168.0.77  acq2206_077` in `/etc/hosts`. Later possibly behind a router |
| Phoebus | D-TACQ's [ACQ400CSSP](https://github.com/D-TACQ/ACQ400CSSP) screens on Phoebus 5.0.2, started with `./ACQ400CSSP_launcher.py acq2206_077` |
| Python API | D-TACQ [acq400_hapi](https://github.com/D-TACQ/acq400_hapi), cloned to `~/PROJECTS/acq400_hapi`, installed editable into `~/PROJECTS/acq400_hapi/.venv` |
| Reference manual | D-TACQ *BOLO Production User Guide* Rev 2 (2026-08-09), not in this repo |

Customer-facing setup instructions (hosts file, venv, folders, Phoebus button)
are in a separate doc, "BOLO8 Quick Setup Guide". Keep the GUI consistent with it.

### Python environment

hapi must not be installed system-wide (Ubuntu 23.04+ blocks it, PEP 668). The
venv is `~/PROJECTS/acq400_hapi/.venv`, created with:

```bash
sudo apt install git python3-venv python3-tk
cd ~/PROJECTS/acq400_hapi
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/pip install numpy matplotlib requests
```

The GUI can live in that same venv (simplest), or in its own venv that also
installs `-e ~/PROJECTS/acq400_hapi`. Either way, launch it with the venv's
Python by absolute path; nothing should rely on an activated shell.

### Name resolution

hapi opens TCP connections to the hostname, so the hostname must resolve
(`getent hosts acq2206_077`). Phoebus does not need this: EPICS finds the box
by UDP broadcast. Keep using the hostname, not the IP: it appears in PV names,
in file names, and `bolo_shot.sh` builds an environment variable from it.

## Files in this repo

| File | Purpose |
| --- | --- |
| `bolo_shot.sh` | `bolo_shot.sh cal\|cap\|save\|capsave UUT`. Runs hapi scripts with the venv Python, saves to `~/bolo_data/<YYYYmmdd_HHMMSS>/`, logs to `~/bolo_data/bolo_shot.log`. Confirmed working |
| `phoebus/bolo_shot.bob` | Phoebus panel: four Action Buttons (Calibrate, Capture, Save last capture, Capture + Save) running `$(SCRIPT) <cmd> $(UUT)`. Installed on site as `~/bolo_panel/bolo_shot.bob` |
| (on site only) `ACQ400CSSP/src/acq400_launcher.bob` | Morten added an Action Button with an *Open Display* action that opens the panel. The edit lives in D-TACQ's checkout, not in this repo |

## What the current flow does

`bolo_shot.sh` wraps three D-TACQ scripts:

| Command | Runs | Notes |
| --- | --- | --- |
| `cal` | `user_apps/special/bolo8_cal_cap_loop.py --cal=1 --cap=0 --shots=1 UUT` | Light source must be removed first. Writes `cal_1.log` to `$<uut>_path` |
| `cap` | `user_apps/special/bolo8_cal_cap_loop.py --cal=0 --cap=1 --shots=1 UUT` | 100 000 samples, soft trigger |
| `save` | `user_apps/analysis/bolo_demux.py --mode=CAP --save --chans=1-8 --root <dir> UUT` | Pulls the capture from the box, writes one file per channel and quantity, then shows a matplotlib window (blocks until closed; `MPLBACKEND=Agg` when `PLOT=0`) |

`bolo8_cal_cap_loop.py` saves no data despite its docstring (MDSplus code was
removed). The data stays in the box until the next capture overwrites it.

## Hardware control sequence (port this into the GUI)

All calls are hapi; `uut = acq400_hapi.factory("acq2206_077")`. Taken from
`bolo8_cal_cap_loop.py`, `bolo8_tester.py` and `bolo_demux.py` (links below).

**Active channels** (optional; only channels with foils connected):

```python
uut.s14.BOLO_ACTIVE_CHAN = "1,2,3"
```

**Calibration:**

```python
old_trg = uut.s1.trg.split(' ')[0].split('=')[1]   # remember trigger setting
uut.s1.trg = "1,1,1"                               # soft trigger for calibration
txt = uut.run_service(acq400_hapi.AcqPorts.BOLO8_CAL, eof="END")   # blocks until done, returns the log text
time.sleep(2)                                      # D-TACQ: without it the next shot can hang
uut.s1.trg = old_trg
```

Ports: `BOLO8_CAL` = calibrate and flush (normal). `BOLO8_CAL1` = calibrate
without flush (calibration data can then be offloaded with
`bolo_demux.py --mode=CAL`); must be followed by `BOLO8_CAL2` = flush only,
before any further capture. The manual's section 4.3.1 text swaps the CLI
numbers; the code (`--cal=2` = CAL1, `--cal=3` = CAL2) is correct.

Calibration results are served by the box at `http://<uut>/tmp/calibfit.log`,
one line per channel: `channel sensitivity cooling I0 Q0`
(`bolo8_tester.py` treats sensitivity 1..15 and cooling 0..1 as in range).

**Capture:**

```python
uut.s0.transient = "POST=100000 SOFT_TRIGGER=0 DEMUX=0"
uut.s1.trg = "1,0,1"            # only for external trigger: rising; "1,0,0" falling
uut.s0.set_arm = '1'
uut.statmon.wait_armed()
uut.s0.soft_trigger = '1'       # internal trigger only
uut.statmon.wait_stopped()
uut.s14.DSP_RESET = 1; time.sleep(0.1); uut.s14.DSP_RESET = 0
```

**Offload** (from `bolo_demux.py`, `mode = CAP` or `CAL`):

```python
nchan = int(uut.s0.NCHAN) // 3          # bolometer channels
fields = []
for ch in range(1, nchan + 1):
    fields += [(f'{ch}_MAG', np.int32), (f'{ch}_PHI', np.int32)]
    if mode == 'CAL':
        fields += [(f'{ch}_OHI', np.int16), (f'{ch}_OHV', np.int16)]
    else:
        fields += [(f'{ch}_PWR', np.int32)]
data = uut.read_channels()
data = np.ascontiguousarray(data.T).view(np.dtype(fields)).reshape(-1)
data['1_PWR']   # raw codes for channel 1 power
```

Setting `uut.save_data = "<dir>"` before `read_channels()` makes hapi also
write raw per-channel files. Offload is slow (D-TACQ's example: about 12 s for
19 MB), so run it off the GUI thread.

## Data format and scaling

- Sample rate 10 kHz (DSP output rate, not the 1 MHz ADC clock). POST=100000 = 10 s.
- Skip the first ~20 samples (filter start-up spike).
- Saved files (current flow): `<uut>.<ch>_<QTY>.<CAP|CAL>.dat`, raw int32 (OHI/OHV int16), no header.

| Quantity | Scale | Unit |
| --- | --- | --- |
| PWR | 4.550e-3 | mW |
| MAG | 7.109e-5 | mV |
| PHI | 1.8626e-9 | rad |
| OHI (CAL only) | 2.6041666e-3 | mA |
| OHV (CAL only) | 3.81469725e-5 | V |

Keep writing the same `.dat` files so existing analysis keeps working; add
metadata alongside (see below).

## Known pitfalls

- Save only after a capture: after a calibration the box holds CAL-layout data.
- Each capture overwrites the box buffer: save before the next capture.
- `bolo8_tester.py --save=1` writes nothing (`active_types` is never filled)
  and keeps only the last cycle. It is D-TACQ's production test tool, not for users.
- Phoebus's Data Browser plots against wall-clock time and is not usable for
  shot data; Phoebus cannot export data. Hence this project.
- Phoebus may be open at the same time as the GUI; the current flow already
  drives the box from scripts while Phoebus displays it.

## Suggested scope for the GUI (to confirm with Morten)

1. One window: box name field, buttons Calibrate / Capture / Save / Capture + Save,
   active-channel selection, status line and a log pane.
2. Show calibration results per channel after each calibration (from `calibfit.log`), flag out-of-range values.
3. Plot PWR (scaled, in seconds) of the last capture inline, all active channels; MAG/PHI on request.
4. Save to `~/bolo_data/<timestamp>/` in the current `.dat` format plus a
   `shot.json`: timestamp, uut, active channels, POST, trigger mode, latest
   calibration table, `uut.s0.software_version`, `uut.s0.fpga_version`, optional operator note.
5. All hardware calls in a worker thread; buttons disabled while busy; errors shown, not swallowed.
6. Optional later: auto-save after every capture, repeated cal/cap cycles, external trigger, configurable POST.

Toolkit not decided. `python3-tk` is already installed (tkinter + embedded
matplotlib needs no new system packages); PySide6 + pyqtgraph gives faster
plotting. Ask before choosing.

## Testing

There is no box off site. Put the hapi calls behind a small interface so the GUI
can run against a fake that returns synthetic data of the right layout; test on
the real box on site. A launcher (`.desktop` file or a Phoebus button running the
GUI with the venv Python) is the last step.

## References

- [acq400_hapi](https://github.com/D-TACQ/acq400_hapi)
- [bolo8_cal_cap_loop.py](https://github.com/D-TACQ/acq400_hapi/blob/master/user_apps/special/bolo8_cal_cap_loop.py)
- [bolo_demux.py](https://github.com/D-TACQ/acq400_hapi/blob/master/user_apps/analysis/bolo_demux.py)
- [bolo8_tester.py](https://github.com/D-TACQ/acq400_hapi/blob/master/test_apps/bolo8_tester.py)
- [acq400_upload.py](https://github.com/D-TACQ/acq400_hapi/blob/master/user_apps/acq400/acq400_upload.py)
- [ACQ400CSSP](https://github.com/D-TACQ/ACQ400CSSP) (Phoebus screens and launcher)
- [Phoebus documentation](https://control-system-studio.readthedocs.io/en/stable/)
