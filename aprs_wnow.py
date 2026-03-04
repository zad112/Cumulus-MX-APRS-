import json
import time
from pathlib import Path
from collections import deque

# ---- EDIT THESE PATHS ----
GAUGES = Path(r"C:\CumulusMX\web\realtimegauges.txt")
OUT    = Path(r"C:\CumulusMX\web\wx_aprs.txt")
STATE  = Path(r"C:\CumulusMX\data\wx_state.json")  # <-- NEW: persistent state

PERIOD_SEC = 60

def zpad(n: int, width: int) -> str:
    return str(int(n)).zfill(width)

def clamp_int(x, lo, hi) -> int:
    return max(lo, min(hi, int(x)))

def inhg_to_mb(inhg: float) -> float:
    return inhg * 33.8638866667

def bfield_from_pressure(press_val: float, press_unit: str) -> str:
    u = (press_unit or "").lower()
    if u.startswith("in"):
        mb = inhg_to_mb(press_val)
    elif "hpa" in u or "mb" in u:
        mb = press_val
    else:
        mb = inhg_to_mb(press_val)
    return f"b{int(round(mb * 10)):05d}"

# --- Rain rolling store (timestamp, increment_in_inches) ---
rain_events = deque()
last_rfall = None
last_day_key = None

def prune_events(now_ts: float):
    cutoff = now_ts - 24 * 3600
    while rain_events and rain_events[0][0] < cutoff:
        rain_events.popleft()

def sum_since(now_ts: float, seconds: float) -> float:
    cutoff = now_ts - seconds
    return sum(inc for ts, inc in rain_events if ts >= cutoff)

def load_state():
    global last_rfall, last_day_key, rain_events
    if not STATE.exists():
        return
    try:
        s = json.loads(STATE.read_text(encoding="utf-8"))
        last_rfall = s.get("last_rfall", None)
        last_day_key = s.get("last_day_key", None)

        ev = s.get("rain_events", [])
        rain_events = deque((float(ts), float(inc)) for ts, inc in ev)

        prune_events(time.time())
        print(f"Loaded state: {len(rain_events)} rain events")
    except Exception as e:
        print("State load failed (starting fresh):", e)

def save_state():
    # atomic-ish write (write temp then replace)
    tmp = STATE.with_suffix(".tmp")
    payload = {
        "last_rfall": last_rfall,
        "last_day_key": last_day_key,
        "rain_events": list(rain_events),
        "saved_at": time.time(),
    }
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(STATE)

load_state()

while True:
    try:
        data = json.loads(GAUGES.read_text(encoding="utf-8"))

        temp_f   = float(data["temp"])
        hum      = float(data["hum"])
        wspeed   = float(data["wspeed"])
        wgust    = float(data["wgust"])
        bearing  = float(data["bearing"])

        press_val  = float(data["press"])
        press_unit = str(data.get("pressunit", "in"))

        # Rain since midnight (inches)
        rfall = float(data.get("rfall", "0.00"))

        solar = float(data.get("SolarRad", "0") or 0)
        uv    = float(data.get("UV", "0") or 0)

        now = time.time()

        # Day rollover handling (rfall resets at midnight)
        day_key = time.strftime("%Y-%m-%d")

        if last_rfall is None or last_day_key is None:
            last_rfall = rfall
            last_day_key = day_key

        if day_key != last_day_key:
            last_day_key = day_key
            last_rfall = rfall

        # Incremental rain since last sample
        delta = rfall - last_rfall
        if delta < 0:
            # reset/glitch - treat as fresh base
            delta = rfall

        if delta > 0:
            rain_events.append((now, delta))

        last_rfall = rfall
        prune_events(now)

        # Rolling totals
        rain_1h_in  = sum_since(now, 1 * 3600)
        rain_24h_in = sum_since(now, 24 * 3600)
        rain_day_in = rfall

        # APRS fields
        wd = clamp_int(bearing, 0, 360)
        ws = clamp_int(wspeed, 0, 999)
        wg = clamp_int(wgust, 0, 999)

        tf = clamp_int(temp_f, -99, 199)
        hh = 0 if hum >= 100 else clamp_int(hum, 0, 99)

        r_1h  = clamp_int(rain_1h_in * 100.0, 0, 999)
        p_24  = clamp_int(rain_24h_in * 100.0, 0, 999)
        P_day = clamp_int(rain_day_in * 100.0, 0, 999)

        b = bfield_from_pressure(press_val, press_unit)
        L = "L" + zpad(clamp_int(solar, 0, 999), 3)
        ufield = "u" + zpad(clamp_int(round(uv * 10), 0, 999), 3)

        wx = (
            f"{zpad(wd,3)}/{zpad(ws,3)}"
            f"g{zpad(wg,3)}"
            f"t{zpad(tf,3)}"
            f"r{zpad(r_1h,3)}"
            f"p{zpad(p_24,3)}"
            f"P{zpad(P_day,3)}"
            f"h{zpad(hh,2)}"
            f"{b}"
            f"{L}"
            f"{ufield}"
        )

        header = time.strftime("%b %d %Y %H:%M")
        OUT.write_text(header + "\n" + wx + "\n", encoding="utf-8")

        save_state()
        print("OK:", wx)

    except Exception as e:
        print("Error:", e)

    time.sleep(PERIOD_SEC)