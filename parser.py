import csv
import re
import models
from models import Segment, Workout

DASH = r'[\u2013\-]'
TIMES = r'[x\xd7]'

# Populated at runtime by load_power_zones()
# Each entry: (low_ftp_fraction, high_ftp_fraction, zone_name)
POWER_ZONES: list[tuple[float, float, str]] = []


def load_power_zones(csv_path: str):
    """Load zone definitions from power-mappings.csv.

    Supports both formats:
      - '60-70% FTP' (preferred)
      - '120-140 W' (legacy)
    """
    POWER_ZONES.clear()
    with open(csv_path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row["Session type"].strip().replace(" ", "")
            target = row["Typical target"].strip()
            # Try % FTP format first: "60-70% FTP"
            m = re.search(r'(\d+)\s*-\s*(\d+)\s*%', target)
            if m:
                POWER_ZONES.append((int(m.group(1)) / 100, int(m.group(2)) / 100, name))
                continue
            # Legacy watts format: "120-140 W"
            m = re.search(r'(\d+)\s*\D+\s*(\d+)', target)
            if m:
                low_w, high_w = int(m.group(1)), int(m.group(2))
                POWER_ZONES.append((low_w / models.FTP, high_w / models.FTP, name))


def _zone_power(name: str) -> float:
    """Look up a zone name and return its midpoint FTP fraction."""
    for low, high, zname in POWER_ZONES:
        if zname == name:
            return round((low + high) / 2, 2)
    return models.Z2_POWER


def watts_to_ftp(watts: int) -> float:
    return round(watts / models.FTP, 2)


def midpoint(low: int, high: int) -> float:
    return watts_to_ftp((low + high) // 2)


def zone_name(power_ftp: float) -> str:
    """Map an FTP fraction to the closest zone name from power-mappings.csv."""
    best_name = "Endurance"
    best_dist = float('inf')
    for low, high, name in POWER_ZONES:
        mid = (low + high) / 2
        dist = abs(power_ftp - mid)
        if dist < best_dist:
            best_dist = dist
            best_name = name
    return best_name


def parse_power_range(text: str) -> float:
    """Parse '120-140 W' or '145' into FTP fraction."""
    m = re.search(rf'(\d+)\s*{DASH}\s*(\d+)\s*W?', text)
    if m:
        return midpoint(int(m.group(1)), int(m.group(2)))
    m = re.search(r'(\d+)\s*W?', text)
    if m:
        return watts_to_ftp(int(m.group(1)))
    return models.Z2_POWER


def parse_duration(text: str) -> int:
    """Parse duration string to seconds. Handles '2.5 h', '90 min', '75-90 min', '1.25 h'."""
    # Duration range - use higher value
    m = re.search(rf'(\d+(?:\.\d+)?)\s*{DASH}\s*(\d+(?:\.\d+)?)\s*(h|min)', text)
    if m:
        val = float(m.group(2))
        unit = m.group(3)
        return int(val * 3600) if unit == 'h' else int(val * 60)
    # Single duration
    m = re.search(r'(\d+(?:\.\d+)?)\s*(h|min)', text)
    if m:
        val = float(m.group(1))
        unit = m.group(2)
        return int(val * 3600) if unit == 'h' else int(val * 60)
    return 0


def is_skip(text: str) -> bool:
    """Check if this day should be skipped (rest, travel, etc)."""
    t = text.strip().lower()
    if not t:
        return True
    skip_patterns = [
        r'^rest',
        r'^travel',
        r'no ride',
    ]
    return any(re.search(p, t) for p in skip_patterns)


def strip_prefix(text: str) -> str:
    """Remove non-workout prefixes like 'PUBLIC HOLIDAY:', 'Mallorca'."""
    text = re.sub(r'^PUBLIC HOLIDAY:\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'^Mallorca\s*', '', text, flags=re.IGNORECASE)
    return text.strip()


def _expand_zone_names(text: str) -> str:
    """Replace zone names with their watt ranges for pattern matching.

    e.g. 'Endurance (2.5 h)' -> 'Endurance 120-140 W (2.5 h)'
         '3×10 Threshold (1.5 h)' -> '3×10 Threshold 200-210 W (1.5 h)'
    """
    for low_ftp, high_ftp, name in POWER_ZONES:
        low_w = round(low_ftp * models.FTP)
        high_w = round(high_ftp * models.FTP)
        watts = f"{low_w}-{high_w} W"
        # Insert watt range after zone name, only if not already followed by a digit
        text = re.sub(rf'\b{name}\b(?!\s+\d)', f'{name} {watts}', text)
    return text


def _make_interval_workout(total_sec: int, repeat: int, on_min: int,
                           on_power: float, name: str, display_name: str,
                           desc: str, source: str) -> Workout:
    """Build a workout with Z2 filler + interval block, placing intervals per the 20-min rule."""
    on_dur = on_min * 60
    off_dur = on_dur // 2
    interval_block_sec = repeat * (on_dur + off_dur) - off_dur  # no rest after last interval

    overhead = models.WARMUP_DURATION + models.COOLDOWN_DURATION
    main_sec = max(total_sec - overhead, interval_block_sec)

    # Z2 filler before intervals: at most models.INTERVAL_PLACEMENT_OFFSET (10 min)
    filler_before = min(models.INTERVAL_PLACEMENT_OFFSET, main_sec - interval_block_sec)
    filler_before = max(filler_before, 0)
    filler_after = max(main_sec - filler_before - interval_block_sec, 0)

    segments = []
    if filler_before > 0:
        segments.append(Segment(kind="steady", duration_sec=filler_before, power=models.Z2_POWER))
    segments.append(Segment(
        kind="intervals", repeat=repeat,
        on_duration=on_dur, off_duration=off_dur,
        on_power=on_power, off_power=models.REST_POWER,
    ))
    if filler_after > 0:
        segments.append(Segment(kind="steady", duration_sec=filler_after, power=models.Z2_POWER))

    return Workout(name=name, display_name=display_name, description=desc, segments=segments, source_text=source)


def parse_description(text: str) -> Workout | None:
    """Parse a free-text workout description into a Workout, or None if it should be skipped."""
    text = text.strip()
    if is_skip(text):
        return None
    source = text
    text = strip_prefix(text)
    text = _expand_zone_names(text)

    # Pattern B: "{dur_range} min {zone} {pow} ({total})" - sustained block in endurance ride
    # e.g. "60-90 min Tempo 150-160 W (4 h)"
    m = re.search(
        rf'(\d+)\s*{DASH}\s*(\d+)\s*min\s+\w+\s+(\d+)\s*{DASH}\s*(\d+)\s*W\s*\(([^)]+)\)',
        text
    )
    if m:
        tempo_sec = int(m.group(2)) * 60  # use higher duration
        tempo_power = midpoint(int(m.group(3)), int(m.group(4)))
        total_sec = parse_duration(m.group(5))
        overhead = models.WARMUP_DURATION + models.COOLDOWN_DURATION
        main_sec = max(total_sec - overhead, tempo_sec)
        z2_before = min(models.INTERVAL_PLACEMENT_OFFSET, main_sec - tempo_sec)
        z2_before = max(z2_before, 0)
        z2_after = max(main_sec - z2_before - tempo_sec, 0)

        segments = []
        if z2_before > 0:
            segments.append(Segment(kind="steady", duration_sec=z2_before, power=models.Z2_POWER))
        segments.append(Segment(kind="steady", duration_sec=tempo_sec, power=tempo_power))
        if z2_after > 0:
            segments.append(Segment(kind="steady", duration_sec=z2_after, power=models.Z2_POWER))

        zn = zone_name(tempo_power)
        block_min = int(m.group(2))
        name = f"{zn}_{block_min}min_{_format_dur(total_sec)}"
        display = f"{zn} {block_min}min {_format_dur(total_sec)}"
        return Workout(name=name, display_name=display,
                       description=f"Endurance with sustained {zn} block",
                       segments=segments, source_text=source)

    # Pattern D: "{N}x{M} [min] {zone} {pow} ({dur})" - intervals with total duration
    # e.g. "3×10 Threshold 200-210 W (1.5 h)", "3×3 min SweetSpot 180-190 W (60 min)"
    m = re.search(
        rf'(\d+)\s*{TIMES}\s*(\d+)\s*(?:min\s+)?(?:or\s+\d+{TIMES}\d+\s+)?\w+\s+(\d+)\s*{DASH}\s*(\d+)\s*W\s*\(([^)]+)\)',
        text
    )
    if m:
        repeat = int(m.group(1))
        on_min = int(m.group(2))
        on_power = midpoint(int(m.group(3)), int(m.group(4)))
        total_sec = parse_duration(m.group(5))
        zn = zone_name(on_power)
        name = f"{zn}_{repeat}x{on_min}_{_format_dur(total_sec)}"
        display = f"{zn} {repeat}x{on_min} {_format_dur(total_sec)}"
        desc = f"{repeat}x{on_min} min {zn} intervals"
        return _make_interval_workout(total_sec, repeat, on_min, on_power, name,
                                      display, desc, source)

    # Pattern F: "{Zone} {pow} ({dur})" - simple steady state (anchored to start)
    # e.g. "Endurance 120-140 W (2.5 h)", "Tempo 150-160 W (2 h)", "Recovery 100-110 W (1 h)"
    m = re.match(
        rf'(?:Recovery|Endurance|Tempo|SweetSpot|Threshold|Z[12])\s+(\d+)\s*{DASH}\s*(\d+)\s*W\s*\(([^)]+)\)',
        text
    )
    if m:
        power = midpoint(int(m.group(1)), int(m.group(2)))
        total_sec = parse_duration(m.group(3))
        overhead = models.WARMUP_DURATION + models.COOLDOWN_DURATION
        main_sec = max(total_sec - overhead, 60)

        segments = [Segment(kind="steady", duration_sec=main_sec, power=power)]
        zn = zone_name(power)
        name = f"{zn}_{_format_dur(total_sec)}"
        display = f"{zn} {_format_dur(total_sec)}"
        return Workout(name=name, display_name=display, description=f"{zn} session",
                       segments=segments, source_text=source)

    # Pattern H: "{dist} km {zone} {pow}" - distance based
    # e.g. "130 km Tempo 150-160 W", "340 km Endurance 120-140 W"
    m = re.search(rf'(\d+)\s*km\s+\w+\s+(\d+)\s*{DASH}\s*(\d+)\s*W', text)
    if m:
        dist_km = int(m.group(1))
        total_sec = int(dist_km / 30 * 3600)  # assume 30 km/h
        power = midpoint(int(m.group(2)), int(m.group(3)))
        overhead = models.WARMUP_DURATION + models.COOLDOWN_DURATION
        main_sec = max(total_sec - overhead, 60)

        segments = [Segment(kind="steady", duration_sec=main_sec, power=power)]
        zn = zone_name(power)
        name = f"{zn}_{dist_km}km"
        display = f"{zn} {dist_km}km"
        return Workout(name=name, display_name=display, description=f"{dist_km}km {zn} ride",
                       segments=segments, source_text=source)

    # Pattern I: duration with optional qualifier (fallback)
    # e.g. "Z2 4.5 h", "2 h easy", "1 h recovery spin"
    m = re.search(
        rf'(?:long\s+)?(?:(?:Z[12]|Endurance|Recovery)\s+)?(\d+(?:\.\d+)?)\s*(?:{DASH}\s*(\d+(?:\.\d+)?)\s*)?(h|min)',
        text
    )
    if m:
        if m.group(2):
            val = float(m.group(2))  # use higher value
        else:
            val = float(m.group(1))
        unit = m.group(3)
        total_sec = int(val * 3600) if unit == 'h' else int(val * 60)

        # Determine power from context
        power = models.RECOVERY_POWER if re.search(r'(?:recovery|easy)', text, re.IGNORECASE) else models.Z2_POWER

        overhead = models.WARMUP_DURATION + models.COOLDOWN_DURATION
        main_sec = max(total_sec - overhead, 60)

        segments = [Segment(kind="steady", duration_sec=main_sec, power=power)]
        label = "Recovery" if power == models.RECOVERY_POWER else "Endurance"
        name = f"{label}_{_format_dur(total_sec)}"
        display = f"{label} {_format_dur(total_sec)}"
        return Workout(name=name, display_name=display, description=f"{label} ride",
                       segments=segments, source_text=source)

    # Fallback: couldn't parse, skip with a warning
    print(f"WARNING: Could not parse session: '{source}'")
    return None


def _format_dur(seconds: int) -> str:
    """Format seconds as e.g. '2h30m', '1h', '45m'."""
    h = seconds // 3600
    m = (seconds % 3600) // 60
    if h and m:
        return f"{h}h{m:02d}m"
    elif h:
        return f"{h}h"
    else:
        return f"{m}m"
