#!/bin/bash
# bolo_shot.sh -- calibrate / capture / save a BOLO8 shot via acq400_hapi
#
# usage: bolo_shot.sh cal|cap|save|capsave UUT
#   cal      run a calibration (bolo8_cal_cap_loop.py --cal=1 --cap=0)
#   cap      run a capture     (bolo8_cal_cap_loop.py --cal=0 --cap=1)
#   save     offload the LAST CAPTURE from the box into a timestamped folder
#   capsave  capture, then save
#
# Only "save" after a CAPTURE: right after a calibration the box holds
# calibration data in a different layout.
#
# Saved files: <DATA>/<YYYYmmdd_HHMMSS>/<uut>.<ch>_{MAG,PHI,PWR}.CAP.dat
# raw int32, 10 kHz, no header. Scale: PWR*4.550e-3 mW, MAG*7.109e-5 mV,
# PHI*1.8626e-9 rad.

# ---- edit these for the local install -------------------------------------
HAPI="$HOME/PROJECTS/acq400_hapi"   # acq400_hapi checkout
PY="$HAPI/.venv/bin/python"         # python from the venv hapi is installed in
DATA="$HOME/bolo_data"              # where shots are saved
CHANS="1-8"                         # channels to PLOT (all 8 are always saved)
PLOT=1                              # 1: show plot after save, 0: no plot window
# ---------------------------------------------------------------------------

cmd="$1"
uut="$2"
if [ -z "$cmd" ] || [ -z "$uut" ]; then
    echo "usage: $0 cal|cap|save|capsave UUT"
    exit 1
fi

mkdir -p "$DATA"
cd "$HAPI" || { echo "acq400_hapi not found at $HAPI"; exit 1; }
[ -x "$PY" ] || { echo "python not found at $PY (check PY in $0)"; exit 1; }

# bolo8_cal_cap_loop.py writes cal_N.log to $<uut>_path
export "${uut}_path"="$DATA"
[ "$PLOT" = 1 ] || export MPLBACKEND=Agg

cal() { "$PY" ./user_apps/special/bolo8_cal_cap_loop.py --cal=1 --cap=0 --shots=1 "$uut"; }
cap() { "$PY" ./user_apps/special/bolo8_cal_cap_loop.py --cal=0 --cap=1 --shots=1 "$uut"; }
save() {
    d="$DATA/$(date +%Y%m%d_%H%M%S)"
    "$PY" ./user_apps/analysis/bolo_demux.py --mode=CAP --save --chans="$CHANS" --root "$d" "$uut" \
        && echo "SAVED to $d"
}

{
    echo "=== $(date '+%F %T') $cmd $uut"
    case "$cmd" in
        cal)     cal ;;
        cap)     cap ;;
        save)    save ;;
        capsave) cap && save ;;
        *)       echo "unknown command: $cmd" ;;
    esac
} 2>&1 | tee -a "$DATA/bolo_shot.log"
