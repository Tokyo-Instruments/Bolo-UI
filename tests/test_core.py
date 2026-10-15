import threading

import numpy as np
import pytest

from bolo_gui import sequencer
from bolo_gui.config import parse_channels
from bolo_gui.data import demux, load_shots, make_dtype, save_shots
from bolo_gui.device import Aborted, CalMode, SimDevice, parse_calibfit


@pytest.fixture
def devs():
    ds = [SimDevice("a", nchan=8, time_scale=0.01, ext_auto_s=1.0, seed=1),
          SimDevice("b", nchan=32, time_scale=0.01, ext_auto_s=1.0, seed=2)]
    for d in ds:
        d.connect()
    return ds


def test_dtype_matches_bolo_demux():
    # CAP: 3 int32 per channel; CAL: 2 int32 + 2 int16 = same 12 bytes (24 LW per 8 ch)
    assert make_dtype(8, "CAP").itemsize == 8 * 12
    assert make_dtype(8, "CAL").itemsize == 8 * 12
    assert make_dtype(1, "CAL").names == ("1_MAG", "1_PHI", "1_OHI", "1_OHV")


def test_demux_of_hapi_layout():
    nchan, nsam = 2, 5
    # hapi read_channels() returns NCHAN x nsam int32, logical channels MAG,PHI,PWR per bolo channel
    raw = np.arange(nchan * 3 * nsam, dtype=np.int32).reshape(nchan * 3, nsam)
    data = demux(raw, nchan, "CAP")
    assert (data["1_MAG"] == raw[0]).all()
    assert (data["2_PWR"] == raw[5]).all()


def test_parse_calibfit():
    txt = ">1          4.1196        0.045691       0.0051923348 -0.0044416948\n>run_calibfit1 99\nEND\n"
    r = parse_calibfit(txt)
    assert list(r) == [1] and r[1].sensitivity == pytest.approx(4.1196) and r[1].in_range()


def test_parse_channels():
    assert parse_channels("1,2,5-7") == [1, 2, 5, 6, 7]
    assert parse_channels("all") == []


def test_cal_capture_save_roundtrip(devs, tmp_path):
    sequencer.calibrate(devs, CalMode.CAL_FLUSH, settle_s=0)
    assert not devs[0].last_cal[6].in_range()            # the sim's deliberately bad foil
    sequencer.capture(devs, post=20_000, trigger="int")
    shots = sequencer.offload(devs, "CAP", {"post": 20_000})
    assert [s.nsam for s in shots] == [20_000, 20_000]
    folder = save_shots(shots, tmp_path, note="t")
    # same files bolo_demux.py writes
    expected = shots[0].data["3_PWR"]
    assert (np.fromfile(folder / "a.3_PWR.CAP.dat", dtype=np.int32) == expected).all()
    back = load_shots(folder)
    assert [b.uut for b in back] == ["a", "b"]
    assert all((x.data == y.data).all() for x, y in zip(shots, back))
    assert back[0].meta["post"] == 20_000 and "calibfit" in back[0].meta


def test_load_folder_without_shot_json(devs, tmp_path):
    sequencer.capture(devs[:1], post=1000, trigger="int")
    shot = sequencer.offload(devs[:1], "CAP")[0]
    for name in shot.data.dtype.names:                     # what bolo_shot.sh produced
        shot.data[name].tofile(tmp_path / f"a.{name}.CAP.dat")
    back = load_shots(tmp_path)[0]
    assert back.nchan == 8 and (back.data == shot.data).all()


def test_flush_required_after_cal_only(devs):
    sequencer.calibrate(devs, CalMode.CAL_ONLY, settle_s=0)
    with pytest.raises(RuntimeError, match="Flush"):
        sequencer.capture(devs, 1000)
    assert sequencer.offload(devs, None)[0].mode == "CAL"
    sequencer.calibrate(devs, CalMode.FLUSH, settle_s=0)
    sequencer.capture(devs, 1000)


def test_external_trigger_and_abort(devs):
    sequencer.capture(devs, 1000, "ext rising")             # sim fires the trigger itself
    for d in devs:
        d.ext_auto_s = None
    abort = threading.Event()
    threading.Timer(0.3, abort.set).start()
    with pytest.raises(Aborted):
        sequencer.capture(devs, 1000, "ext falling", abort)


def test_cycles_and_events(devs):
    events, got = [], []
    for d in devs:
        d.add_listener(events.append)
    sequencer.run_cycles(devs, 2, cal_mode=CalMode.CAL_FLUSH, do_capture=True, post=1000,
                         trigger="int", on_capture=got.append, settle_s=0)
    assert len(got) == 2 and got[1][0].meta["cycle"] == 2
    assert all(e.source == "gui" for e in events)


def test_panel_button_is_external(devs):
    done = threading.Event()
    seen = []

    def cb(ev):
        seen.append(ev)
        if ev.phase == "finished":
            done.set()

    devs[0].add_listener(cb)
    devs[0].press_capture()
    assert done.wait(5)
    assert seen[-1].source == "external" and seen[-1].kind == "CAPTURE"
    assert devs[0].buffer_mode == "CAP"
