# BOLO8 tools

Read HANDOVER.md for the system context, the confirmed hardware control
sequence and the data format. README.md describes the GUI (src/bolo_gui).

- Run without hardware: `.venv/bin/bolo-gui sim:demo sim:big:32`
- Tests: `.venv/bin/pytest`; headless end to end: `.venv/bin/bolo-gui --selftest`
- Hardware calls only in device/hapi_device.py; keep the .dat layout identical
  to D-TACQ's bolo_demux.py.

@HANDOVER.md
