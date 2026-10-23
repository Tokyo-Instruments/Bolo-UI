# BOLO8 Control: setup and user guide

BOLO8 Control (`bolo-gui`) is a desktop application for D-TACQ BOLO8 bolometer
systems. It calibrates the bolometers, captures shots, plots the data and saves
it, on one or more acquisition boxes, without Phoebus or a terminal.

It drives the hardware through D-TACQ's Python API
[acq400_hapi](https://github.com/D-TACQ/acq400_hapi), using the same sequence as
D-TACQ's own scripts (`bolo8_cal_cap_loop.py`, `bolo_demux.py`).

Source: <https://github.com/Tokyo-Instruments/Bolo-UI>

**Contents:**
1. [What you need](#1-what-you-need)
2. [Network setup](#2-network-setup)
3. [Software installation](#3-software-installation)
4. [Starting the application](#4-starting-the-application)
5. [Using the application](#5-using-the-application)
6. [Saved data](#6-saved-data)
7. [Troubleshooting](#7-troubleshooting)
8. [Technical reference](#8-technical-reference)
9. [For developers](#9-for-developers)

---

## 1. What you need

| Item | Notes |
| --- | --- |
| D-TACQ acq2206 box with BOLO8 module(s) | Example in this guide: box name `acq2206_077`, IP `192.168.0.77`. The name, MAC address and fallback IP are printed on the rear plate |
| Control PC | Ubuntu desktop (22.04 or newer, NetworkManager). macOS also works |
| Network cable | Direct Ethernet connection PC ↔ box (a switch/router also works, see 2.4) |
| Internet access on the PC | Only during installation (to download hapi and Python packages) |
| This application | Cloned from <https://github.com/Tokyo-Instruments/Bolo-UI> to `~/PROJECTS/bolo_gui` (section 3.3) |

Each BOLO8 module provides 8 bolometer channels. The application reads the
number of installed channels from the box itself.

---

## 2. Network setup

The PC talks to the box over TCP using the box's **hostname**. Two things are
needed: the PC must be in the box's subnet, and the hostname must resolve to the
box's IP address.

### 2.1 Give the PC a static IP address in the box's subnet

The box uses `192.168.0.77`, so give the PC's Ethernet port a fixed address in
`192.168.0.0/24`, for example `192.168.0.10`.

**Option A: Ubuntu Settings (graphical)**

1. *Settings ▸ Network*, click ⚙ next to the wired connection.
2. *IPv4* tab: *IPv4 Method* = **Manual**.
3. *Addresses*: Address `192.168.0.10`, Netmask `255.255.255.0`, Gateway empty.
4. Leave DNS empty. Click *Apply*, then switch the connection off and on.

**Option B: command line (NetworkManager)**

```bash
nmcli device                       # find the Ethernet interface name, e.g. enp3s0
```

```bash
sudo nmcli connection add type ethernet ifname enp3s0 con-name bolo8 ipv4.method manual ipv4.addresses 192.168.0.10/24 ipv4.never-default yes ipv6.method disabled
```

```bash
sudo nmcli connection up bolo8
```

`ipv4.never-default yes` keeps the PC's internet traffic (e.g. over Wi-Fi) away
from the box link.

### 2.2 Make the box's name resolve

Add the box to `/etc/hosts`, one line per box:

```bash
echo "192.168.0.77  acq2206_077" | sudo tee -a /etc/hosts
```

Always use the **name** (`acq2206_077`), not the IP, in the application. The
name appears in file names and on the box's own web pages.

### 2.3 Check the connection

```bash
getent hosts acq2206_077           # must print 192.168.0.77  acq2206_077
```

```bash
ping -c 3 acq2206_077
```

You can also open `http://acq2206_077/` in a browser to see the box's web page.

Phoebus/EPICS does not need these steps, because it finds the box by UDP
broadcast. That is why Phoebus can work while this application cannot connect.
Phoebus and this application can run at the same time.

### 2.4 Behind a router or switch

If the box is later connected through a router or switch with DHCP, either
reserve a fixed IP for the box in the router (and keep the `/etc/hosts` entry),
or let the router's DNS resolve the name. The `getent` check in 2.3 must pass
either way.

### 2.5 macOS

Use *System Settings ▸ Network ▸ (Ethernet adapter) ▸ Details ▸ TCP/IP*:
Configure IPv4 *Manually*, IP `192.168.0.10`, subnet mask `255.255.255.0`. Add
the same line to `/etc/hosts` with `sudo nano /etc/hosts`, and check it with
`ping acq2206_077`.

---

## 3. Software installation

The application and acq400_hapi live in one Python virtual environment
("venv"). Ubuntu 23.04 and newer refuse to install Python packages system-wide,
so a venv is required. Nothing needs to be "activated": the application is
started by its full path.

### 3.1 System packages (once)

```bash
sudo apt install git python3-venv python3-tk
```

### 3.2 acq400_hapi and its venv

```bash
mkdir -p ~/PROJECTS && cd ~/PROJECTS && git clone https://github.com/D-TACQ/acq400_hapi.git
```

```bash
cd ~/PROJECTS/acq400_hapi && python3 -m venv .venv && .venv/bin/pip install -e .
```

If acq400_hapi is already installed this way (e.g. for D-TACQ's scripts), skip
this step and reuse that venv.

### 3.3 BOLO8 Control

Clone the application into `~/PROJECTS/bolo_gui` (no GitHub account needed):

```bash
git clone https://github.com/Tokyo-Instruments/Bolo-UI.git ~/PROJECTS/bolo_gui
```

Then install it into the same venv:

```bash
~/PROJECTS/acq400_hapi/.venv/bin/pip install -e ~/PROJECTS/bolo_gui
```

This also installs numpy, matplotlib and h5py. With `-e` ("editable"), changes
to the code in `~/PROJECTS/bolo_gui` take effect at the next start, without
reinstalling.

### 3.4 Check the installation

Without hardware (simulated box):

```bash
~/PROJECTS/acq400_hapi/.venv/bin/bolo-gui --selftest
```

With the box connected (runs a real calibration and a 10 s capture, so remove
the light source from the foils first):

```bash
~/PROJECTS/acq400_hapi/.venv/bin/bolo-gui --selftest acq2206_077
```

Both runs end with `reload OK`.

### 3.5 Updating

```bash
cd ~/PROJECTS/bolo_gui && git pull
```

```bash
cd ~/PROJECTS/acq400_hapi && git pull
```

Re-run the `pip install -e` commands only if the update adds new dependencies.
Local edits to the application's files can make `git pull` stop with a
conflict; keep site-specific changes (box names, paths) in the launcher
(section 4) rather than in the code.

PCs without internet access: download the ZIP from the GitHub page
(*Code ▸ Download ZIP*) on another computer, unpack it to `~/PROJECTS/bolo_gui`,
and install as above. acq400_hapi and the Python packages then also have to be
brought over; installing on a networked PC first is much simpler.

---

## 4. Starting the application

### 4.1 From a terminal

```bash
~/PROJECTS/acq400_hapi/.venv/bin/bolo-gui acq2206_077
```

Several boxes:

```bash
~/PROJECTS/acq400_hapi/.venv/bin/bolo-gui acq2206_077 acq2206_078
```

Without box names, the boxes from the last session are used. Boxes can also be
added and removed in the window.

### 4.2 From the applications menu

1. Copy `packaging/bolo-gui.desktop` to `~/.local/share/applications/`.
2. Edit its `Exec=` line: replace `USER` with your user name and adjust the box name(s).
3. *BOLO8 Control* then appears in the applications menu and can be pinned to the dock.

### 4.3 From Phoebus

Add an *Action Button* with an *Execute Command* action:

```
/home/<user>/PROJECTS/acq400_hapi/.venv/bin/bolo-gui acq2206_077
```

### 4.4 Command line options

```
bolo-gui [options] [BOX ...]
  BOX                 box hostname, or sim:NAME[:NCHAN] for a simulated box
  --data-dir DIR      where data is saved (default ~/bolo_data)
  --post N            samples per capture at 10 kHz (default 100000 = 10 s)
  --trg {int,ext rising,ext falling}   trigger source
  --active-chan "1,2,3;1-8"            active channels per box, ';' between boxes
  --format {dat,hdf5,both}             save format (default dat)
  --save-scaled       save physical units instead of raw values (section 6.1)
  --autosave          save every capture
  --selftest          no window: calibrate, capture, save, reload, exit
  -v                  more detail in the log
```

Settings changed in the window (folder, POST, trigger, format, active channels,
…) are remembered in `<data folder>/bolo_gui_settings.json`.

---

## 5. Using the application

The window has the controls on the left and three tabs on the right: **Plot**,
**Calibration** and **Log**.

### 5.1 Typical session

1. **Check the devices.** Each box shows its channel count and `idle` when connected.
   A box that is not ticked is ignored by all actions.
2. **Select the active channels** (the channels with foils connected) for each box.
3. **Calibrate.** Remove the light source from the foils, choose *Calibrate + flush*
   and press *Calibrate*. The sensor response (magnitude and ohmic heating
   voltage) is plotted, and the *Calibration* tab lists sensitivity and cooling
   time per channel. Out-of-range values are marked red, and the tab title shows
   their number, e.g. *Calibration (1 ⚠)*.
   D-TACQ recommends calibrating at the start of every session, because room
   light and temperature drift.
4. **Capture.** Apply the light/plasma, press *Capture* (or *Capture + Save*).
   The power of all active channels is plotted when the capture has been read
   from the box (a few seconds after the end of the shot).
5. **Save** with *Save last shot*, or tick *Autosave every capture*. Each
   capture overwrites the data in the box, so save before the next capture.

### 5.2 Controls

| Control | What it does |
| --- | --- |
| Devices | Tick the boxes an action applies to. ↻ reconnects, ✕ removes, *Add device…* adds one |
| Active channels | Channels with foils connected, per box (up to 32 shown; channels the box does not have are greyed out) |
| Calibration mode | *Calibrate + flush*: normal calibration. *Calibrate, no flush*: keeps the calibration data in the box for reading; captures stay blocked until *Flush only* has been run. *Flush only*: completes a calibration without flush |
| Show sensor response | With *Calibrate + flush*: read and plot the calibration data before the flush deletes it (on by default) |
| Trigger | *int*: the application triggers the capture. *ext rising / ext falling*: the box waits for an external trigger signal; *Abort* cancels the wait |
| POST samples | Capture length at 10 kHz (100000 = 10 s) |
| Capture / Capture + Save | Arm all ticked boxes, trigger, read and plot the data (and save) |
| Cycles, calibrate each, Run | Repeat (calibration and) capture N times. Turn on autosave, otherwise only the last cycle is kept |
| Read data from box | Read whatever the box currently holds (e.g. calibration data after *Calibrate, no flush*) |
| Abort | Stop a running capture or cycle loop |
| Folder, Note | Where data is saved; the note is stored with every saved shot |
| Format, scaled | *D-TACQ .dat*, *HDF5*, or both. *scaled*: save values in mW, mV, rad… instead of raw values (see section 6) |
| Autosave every capture | Save automatically after each capture |
| Watch for panel-button / external shots | When a capture is started from the BOLO8 panel's push-buttons (or by any other program), read and plot it, and save it if autosave is on. After a panel calibration, update the calibration table |
| Plot tab | Choose quantities (PWR, MAG, PHI; OHI, OHV for calibration data) and channels (`all`, `1-4`, `1,3,5`). Zoom with the toolbar; detail is redrawn when zooming in |
| File ▸ Open shot… / Open HDF5 file… | Reopen saved data, including folders saved by the older `bolo_shot.sh` script |
| Simulator menu | Only for simulated boxes (`sim:NAME`): simulate the panel buttons and an external trigger |

### 5.3 Trying it without hardware

```bash
~/PROJECTS/acq400_hapi/.venv/bin/bolo-gui sim:demo sim:big:32
```

This opens two simulated boxes with 8 and 32 channels, producing data of the
same layout as a real box. Channel 6 is simulated as a faulty foil.

---

## 6. Saved data

Each save creates one folder per acquisition in the data folder
(default `~/bolo_data`). The *Format* setting decides what goes in it:

```
~/bolo_data/
  20261015_101500/                         one folder per saved shot
    acq2206_077.01_PWR.CAP.dat             D-TACQ .dat format: one file per channel and quantity,
    acq2206_077.01_MAG.CAP.dat             identical to D-TACQ's bolo_demux.py --save (--save_scaled)
    ...
    shot.json                              metadata for the .dat files
    shot.h5                                HDF5 format: everything in one file
  cal_logs/<time>_<box>_<mode>.log         transcript of every calibration
  calibration_history.csv                  every calibration result (one line per channel)
  bolo_gui.log                             application log
  bolo_gui_settings.json                   remembered settings
```

Calibration data (from *Show sensor response* or *Read data from box*) is saved
the same way, with `CAL` instead of `CAP` in the file names.

### 6.1 Raw or scaled values

By default both formats store the **raw integer values** from the box, as
`bolo_demux.py --save` does. Multiply by the scale factor to get physical units.
With the *scaled* option (or `--save-scaled`) they store the values already in
physical units, as 64-bit floats, exactly like `bolo_demux.py --save_scaled`:
the raw value times the factor below. Scaled files are about twice (.dat) to
three times (HDF5) as large. Raw data loses nothing: it can always be scaled
later, including with different factors.

| Quantity | Meaning | Scale | Unit | Type |
| --- | --- | --- | --- | --- |
| PWR | Absorbed power (real-time, calibrated) | 4.550e-3 | mW | int32 |
| MAG | Bridge voltage amplitude | 7.109e-5 | mV | int32 |
| PHI | Bridge voltage phase | 1.8626e-9 | rad | int32 |
| OHI | Ohmic heating current (calibration only) | 2.6041666e-3 | mA | int16 |
| OHV | Ohmic heating voltage (calibration only) | 3.81469725e-5 | V | int16 |

The type column is for raw values; scaled values are always float64. These are
the defaults of D-TACQ's `bolo_demux.py`. The PWR and MAG factors assume the
1V25 input gain setting. They are also stored with the data (`shot.json` →
`scales` and `scaled: true/false`; HDF5 → see 6.3).

The sample rate is 10 kHz. The first ~20 samples contain a filter start-up spike
and should be skipped.

### 6.2 D-TACQ .dat format

File name `<box>.<channel>_<quantity>.<CAP|CAL>.dat` with a two-digit channel
number (`01`, `02`, …), values without header. All channels the box has are
saved, not only the active ones.

```python
import numpy as np
pwr_mW = np.fromfile("acq2206_077.01_PWR.CAP.dat", dtype=np.int32) * 4.550e-3   # raw
pwr_mW = np.fromfile("acq2206_077.01_PWR.CAP.dat", dtype=np.float64)            # scaled
```

The file name does not say whether a file is raw or scaled (as with D-TACQ's
script); `scaled` in `shot.json` does. Data saved before October 2026 uses
one-digit channel numbers (`1_PWR`), as did the older `bolo_shot.sh`; the
application opens both, and folders from `bolo_demux.py` (raw or scaled) as well.

`shot.json` holds the timestamp, note, POST, trigger, active channels, whether
the shot was started from the panel (`source: external`), box firmware versions,
the latest calibration table and the scale factors.

### 6.3 HDF5 format

`shot.h5` contains the same data and metadata, compressed to about half the
size. One group per box, one dataset per quantity with one row per channel:

```
/                    attrs: timestamp, note, sample_rate, skip_samples, scaled
/acq2206_077/        attrs: mode (CAP/CAL), nchan, nsam, active_channels, source, post, trigger, meta_json
/acq2206_077/PWR     [nchan, nsam], row 0 = channel 1; attrs: scale, unit, scaled, raw_scale
/acq2206_077/MAG, PHI (and OHI, OHV for calibration data)
/acq2206_077/calibfit   table: channel, sensitivity, tau, I0, Q0
```

```python
import h5py
with h5py.File("shot.h5") as f:
    pwr = f["acq2206_077/PWR"]
    ch1_mW = pwr[0] * pwr.attrs["scale"]
```

This works for raw and scaled files alike: for scaled data the values are
float64 in physical units and `scale` is 1.0 (`raw_scale` keeps the factor).

HDF5 files can also be opened with HDFView, MATLAB (`h5read`) or any other HDF5 tool.

### 6.4 Reading data with the application's own code

Works for both formats, from the venv's Python:

```python
from bolo_gui.data import load_shots
shot = load_shots("~/bolo_data/20261015_101500")[0]
t, p = shot.time(), shot.values(1, "PWR")      # seconds, mW
```

---

## 7. Troubleshooting

All messages also appear in the *Log* tab and in `~/bolo_data/bolo_gui.log`.

| Problem | Cause and fix |
| --- | --- |
| Device shows `error: no connection` | Name does not resolve or box not reachable: run the checks in 2.3. Then press ↻ |
| `nodename nor servname provided` / `Name or service not known` in the log | `/etc/hosts` entry missing or misspelled (2.2) |
| Phoebus works but the application cannot connect | Phoebus does not need the hosts entry or the static IP; this application does (section 2) |
| `acq400_hapi is not installed` | The application was started with the wrong Python. Start it via `~/PROJECTS/acq400_hapi/.venv/bin/bolo-gui` (3.3) |
| `No module named tkinter` | `sudo apt install python3-tk` |
| Capture never finishes | With an external trigger, the box waits for the trigger signal; press *Abort*. Check the trigger setting |
| Capture is refused: "calibration was run without flush" | Run *Flush only* (after reading the calibration data, if wanted) |
| Many channels out of range after calibration | The light source was not removed, or no foil is connected to those channels. Untick channels without foils |
| Channels are offset from each other | The system is not calibrated yet; run a calibration |
| A second capture overwrote unsaved data | The box holds only one shot. Turn on *Autosave* |
| Panel-button shot is not picked up | *Watch for panel-button / external shots* must be on. See 8.4 |

---

## 8. Technical reference

### 8.1 Hardware sequence

The application uses the same acq400_hapi calls as D-TACQ's scripts
(`device/hapi_device.py`, `sequencer.py`):

```python
uut = acq400_hapi.factory("acq2206_077")
uut.s14.BOLO_ACTIVE_CHAN = "1,2,3"                 # active channels

# calibration
old_trg = uut.s1.trg.split(' ')[0].split('=')[1]   # remember trigger setting
uut.s1.trg = "1,1,1"                               # soft trigger
txt = uut.run_service(acq400_hapi.AcqPorts.BOLO8_CAL, eof="END")
time.sleep(2)                                      # D-TACQ: otherwise the next shot can hang
uut.s1.trg = old_trg
# results: http://<box>/tmp/calibfit.log   (channel sensitivity cooling I0 Q0)

# capture
uut.s0.transient = "POST=100000 SOFT_TRIGGER=0 DEMUX=0"
uut.s1.trg = "1,1,1"          # int; ext rising "1,0,1", ext falling "1,0,0"
uut.s0.set_arm = '1'
uut.statmon.wait_armed()
uut.s0.soft_trigger = '1'     # int only
uut.statmon.wait_stopped()
uut.s14.DSP_RESET = 1; time.sleep(0.1); uut.s14.DSP_RESET = 0

# offload: NCHAN/3 bolometer channels, per channel MAG, PHI, PWR (int32)
# or, after a calibration, MAG, PHI (int32), OHI, OHV (int16)
data = uut.read_channels()
```

Calibration ports: `BOLO8_CAL` = calibrate and flush, `BOLO8_CAL1` = calibrate
without flush, `BOLO8_CAL2` = flush only (`--cal=1/2/3` in
`bolo8_cal_cap_loop.py`). The flush deletes the calibration data, so *Show sensor
response* runs CAL1, reads the data, then CAL2. Reading a 10 s capture takes
several seconds (D-TACQ: about 12 s for 19 MB).

### 8.2 Several boxes

Each step runs on all ticked boxes before the next one starts (arm all, trigger
all, wait for all, read all), as in `bolo8_cal_cap_loop.py`. The data of one
acquisition from all boxes is saved into one folder.

### 8.3 Channel count

The number of bolometer channels is read from the box (`NCHAN / 3`; 8 per BOLO8
module). Whether a foil is actually connected cannot be read from the box. Set
the active channels accordingly; channels without a foil fail the calibration
range check.

### 8.4 Shots started from the BOLO8 panel buttons

The application watches the box's acquisition state and notices shots it did
not start. To tell a panel calibration from a panel capture it uses a
`…CALIBRATING` setting of the box if one exists (the candidates found are
written to the log at connect), otherwise it checks whether `calibfit.log`
changed within 20 s after the shot. How the panel buttons are exposed is still
to be confirmed with D-TACQ; the logic is in `HapiDevice._classify_external`.

### 8.5 Known limitations

- After a calibration the box holds calibration-layout data; *Read data from box*
  reads it as such. Each capture overwrites the box buffer.
- acq400_hapi's status monitor can send an interrupt to the program on an
  unexpected state change (a shot that skips the ARM state). The application
  logs it and restarts the monitor instead of closing. Pressing Ctrl-C twice in
  the terminal still quits.
- Phoebus's Data Browser plots against wall-clock time and cannot export data,
  which is why shot data is handled by this application.
- The older stopgap (`bolo_shot.sh` with a Phoebus panel) is kept in
  `reference/` for comparison.

---

## 9. For developers

### 9.1 Development setup

Any machine, no hardware needed:

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
```

```bash
.venv/bin/pytest
```

```bash
.venv/bin/bolo-gui sim:demo sim:big:32
```

Add acq400_hapi to the development venv with `.venv/bin/pip install -e '.[dev,hw]'`.

### 9.2 Code layout

```
src/bolo_gui/
  cli.py                 command line, --selftest
  config.py              constants (scales, rates, ranges) and remembered settings
  data.py                Shot; .dat / HDF5 save and load; calibration history
  sequencer.py           calibrate / capture / read / cycles over several boxes (no GUI code)
  worker.py              background thread; results back to the window via a queue
  device/base.py         BoloDevice: the interface every box type implements
  device/hapi_device.py  real box via acq400_hapi (all hardware calls are here)
  device/sim_device.py   simulated box
  ui/app.py              main window; one method per action
  ui/acquire.py, devices.py, plot.py, calib.py, log.py    panels
tests/                   pytest, runs against the simulator
packaging/               desktop menu entry
reference/               previous bolo_shot.sh + Phoebus panel
```

### 9.3 Extending

- **New action:** write a function in `sequencer.py` (plain Python, testable with
  the simulator), add a button in `ui/acquire.py`, and add a method in
  `ui/app.py` that runs it in the worker. The docstring at the top of
  `ui/app.py` shows the pattern.
- **New hardware variant:** subclass `BoloDevice` and return it from
  `make_device()` in `device/__init__.py`.
- **Scripting without the window:** see the example at the top of `sequencer.py`.
- Keep the `.dat` layout identical to D-TACQ's `bolo_demux.py`, so existing
  analysis code keeps working.
