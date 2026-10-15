# BOLO8 tools

README.md is the setup and user guide; its section 8 has the hardware
sequence, data format and known limitations, section 9 the code layout.

- Run without hardware: `.venv/bin/bolo-gui sim:demo sim:big:32`
- Tests: `.venv/bin/pytest`; headless end to end: `.venv/bin/bolo-gui --selftest`
- Hardware calls only in device/hapi_device.py; keep the .dat layout identical
  to D-TACQ's bolo_demux.py.
- Not yet tested on the real box (acq2206_077); panel-button detection is a
  heuristic pending D-TACQ's answer (HapiDevice._classify_external).

@README.md
