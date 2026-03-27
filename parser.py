import csv
import re
import models
from models import Segment, Workout

DASH = r'[\u2013\-]'
TIMES = r'[x\xd7]'

# Populated at runtime by load_power_zones()
POWER_ZONES: list[tuple[int, int, str]] = []


def load_power_zones(csv_path: str):
    """Load zone definitions from power-mappings.csv."""
    POWER_ZONES.clear()
    with open(csv_path, newline='', encoding='cp1252') as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row["Session type"].strip().replace(" ", "")
            target = row["Typical target"].strip()
            # Parse "120–140 W" - handle en-dash, hyphen, and encoding artifacts
            m = re.search(r'(\d+)\s*\D+\s*(\d+)', target)
            if m:
                POWER_ZONES.append((int(m.group(1)), int(m.group(2)), name))


def watts_to_ftp(watts: int) -> float:
    return round(watts / models.FTP, 2)


def midpoint(low: int, high: int) -> float:
    return watts_to_ftp((low + high) // 2)


def zone_name(power_ftp: float) -> str:
    """Map an models.FTP fraction to the closest zone name from power-mappings.csv."""
    watts = power_ftp * models.FTP
    best_name = "Endurance"
    best_dist = float('inf')
    for low, high, name in POWER_ZONES:
        mid = (low + high) / 2
        dist = abs(watts - mid)
        if dist < best_dist:
            best_dist = dist
            best_name = name
    return best_name


def parse_power_range(text: str) -> float:
    """Parse '120-140 W' or '145' into models.FTP fraction."""
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
        r'travel',
        r'no ride',
    ]
    return any(re.search(p, t) for p in skip_patterns)


def strip_prefix(text: str) -> str:
    """Remove non-workout prefixes like 'PUBLIC HOLIDAY:', 'Mallorca'."""
    text = re.sub(r'^PUBLIC HOLIDAY:\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'^Mallorca\s*', '', text, flags=re.IGNORECASE)
    return text.strip()


def _make_interval_workout(total_sec: int, repeat: int, on_min: int,
                           on_power: float, name: str, desc: str, source: str) -> Workout:
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

    return Workout(name=name, description=desc, segments=segments, source_text=source)


def parse_description(text: str) -> Workout | None:
    """Parse a free-text workout description into a Workout, or None if it should be skipped."""
    text = text.strip()
    if is_skip(text):
        return None
    source = text
    text = strip_prefix(text)

    # Pattern A: "{dur} incl. {N}x{M} @ {pow}" - endurance with embedded intervals
    m = re.search(
        rf'(\d+(?:\.\d+)?\s*(?:h|min))\s+.*?incl\.\s*(\d+)\s*{TIMES}\s*(\d+)\s*(?:min\s*)?@\s*(\d+)\s*{DASH}\s*(\d+)\s*W',
        text
    )
    if m:
        total_sec = parse_duration(m.group(1))
        repeat = int(m.group(2))
        on_min = int(m.group(3))
        on_power = midpoint(int(m.group(4)), int(m.group(5)))
        zn = zone_name(on_power)
        name = f"Endurance_{_format_dur(total_sec)}_with_{repeat}x{on_min}min_{zn}"
        return _make_interval_workout(total_sec, repeat, on_min, on_power, name,
                                      f"Endurance ride with {repeat}x{on_min} min {zn} intervals", source)

    # Pattern B: "{dur} Z2 incl. {dur_range} @ {pow}" - endurance with sustained tempo block
    m = re.search(
        rf'(\d+(?:\.\d+)?\s*(?:h|min)).*?incl\.\s*(\d+)\s*{DASH}\s*(\d+)\s*min\s*@\s*(\d+)\s*{DASH}\s*(\d+)\s*W',
        text
    )
    if m:
        total_sec = parse_duration(m.group(1))
        tempo_sec = int(m.group(3)) * 60  # use higher duration
        tempo_power = midpoint(int(m.group(4)), int(m.group(5)))
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
        name = f"Endurance_{_format_dur(total_sec)}_with_{zn}"
        return Workout(name=name, description=f"Endurance with sustained {zn} block",
                       segments=segments, source_text=source)

    # Pattern C: "{dur} + {N}x{M} @ {pow}" - long ride plus intervals at end
    m = re.search(
        rf'(\d+(?:\.\d+)?\s*h)\s*\+\s*(\d+)\s*{TIMES}\s*(\d+)\s*@\s*(\d+)\s*{DASH}\s*(\d+)\s*W',
        text
    )
    if m:
        ride_sec = parse_duration(m.group(1))
        repeat = int(m.group(2))
        on_min = int(m.group(3))
        on_power = midpoint(int(m.group(4)), int(m.group(5)))
        on_dur = on_min * 60
        off_dur = on_dur // 2
        # Z2 portion = stated ride time minus warmup/cooldown
        z2_sec = max(ride_sec - models.WARMUP_DURATION - models.COOLDOWN_DURATION, 60)

        segments = [
            Segment(kind="steady", duration_sec=z2_sec, power=models.Z2_POWER),
            Segment(kind="intervals", repeat=repeat,
                    on_duration=on_dur, off_duration=off_dur,
                    on_power=on_power, off_power=models.REST_POWER),
        ]
        zn = zone_name(on_power)
        name = f"Endurance_{_format_dur(ride_sec)}_plus_{repeat}x{on_min}min_{zn}"
        return Workout(name=name,
                       description=f"Long endurance ride then {repeat}x{on_min} min {zn} intervals",
                       segments=segments, source_text=source)

    # Pattern D: "{N}x{M} @ {pow} ({dur})" - pure intervals with total duration
    m = re.search(
        rf'(\d+)\s*{TIMES}\s*(\d+)\s*(?:or\s+\d+{TIMES}\d+\s*)?@\s*(\d+)\s*{DASH}\s*(\d+)\s*W\s*\(([^)]+)\)',
        text
    )
    if m:
        repeat = int(m.group(1))
        on_min = int(m.group(2))
        on_power = midpoint(int(m.group(3)), int(m.group(4)))
        total_sec = parse_duration(m.group(5))
        zn = zone_name(on_power)
        name = f"{repeat}x{on_min}min_{zn}_{_format_dur(total_sec)}"
        return _make_interval_workout(total_sec, repeat, on_min, on_power, name,
                                      f"{repeat}x{on_min} min {zn} intervals", source)

    # Pattern E: "{dur} steady; final {dur} @ {pow}" - compound with finishing block
    m = re.search(
        rf'(\d+(?:\.\d+)?\s*h)\s+steady;?\s*final\s+(\d+)\s*min\s*@\s*(\d+)\s*{DASH}\s*(\d+)\s*W',
        text
    )
    if m:
        total_sec = parse_duration(m.group(1))
        finish_sec = int(m.group(2)) * 60
        finish_power = midpoint(int(m.group(3)), int(m.group(4)))
        overhead = models.WARMUP_DURATION + models.COOLDOWN_DURATION
        z2_sec = max(total_sec - overhead - finish_sec, 0)

        segments = []
        if z2_sec > 0:
            segments.append(Segment(kind="steady", duration_sec=z2_sec, power=models.Z2_POWER))
        segments.append(Segment(kind="steady", duration_sec=finish_sec, power=finish_power))

        zn = zone_name(finish_power)
        name = f"Endurance_{_format_dur(total_sec)}_finish_{zn}"
        return Workout(name=name, description=f"Endurance ride with final {m.group(2)} min {zn} finish",
                       segments=segments, source_text=source)

    # Pattern F: "{Zone} {pow} ({dur})" - zone with power and duration
    m = re.search(
        rf'(?:Z[12]|Endurance|Tempo)\s+(\d+)\s*{DASH}\s*(\d+)\s*W\s*\(([^)]+)\)',
        text
    )
    if m:
        power = midpoint(int(m.group(1)), int(m.group(2)))
        total_sec = parse_duration(m.group(3))
        overhead = models.WARMUP_DURATION + models.COOLDOWN_DURATION
        main_sec = max(total_sec - overhead, 60)
        zone = "Tempo" if "empo" in text else "Z2"

        segments = [Segment(kind="steady", duration_sec=main_sec, power=power)]
        zn = zone_name(power)
        name = f"{zn}_{_format_dur(total_sec)}"
        return Workout(name=name, description=f"{zn} session",
                       segments=segments, source_text=source)

    # Pattern G: "{dur} {Zone} {pow}" - duration then zone and power
    m = re.search(
        rf'(\d+(?:\.\d+)?\s*(?:h|min))\s+(?:Z[12]|easy)\s+(\d+)\s*{DASH}\s*(\d+)\s*W',
        text
    )
    if m:
        total_sec = parse_duration(m.group(1))
        power = midpoint(int(m.group(2)), int(m.group(3)))
        overhead = models.WARMUP_DURATION + models.COOLDOWN_DURATION
        main_sec = max(total_sec - overhead, 60)

        segments = [Segment(kind="steady", duration_sec=main_sec, power=power)]
        zn = zone_name(power)
        name = f"{zn}_{_format_dur(total_sec)}"
        return Workout(name=name, description=f"{zn} ride",
                       segments=segments, source_text=source)

    # Pattern H: "{dist}km @ {pow}" - distance based
    m = re.search(rf'(\d+)\s*km\s*@\s*(\d+)(?:\s*{DASH}\s*(\d+))?\s*W?', text)
    if m:
        dist_km = int(m.group(1))
        total_sec = int(dist_km / 30 * 3600)  # assume 30 km/h
        if m.group(3):
            power = midpoint(int(m.group(2)), int(m.group(3)))
        else:
            power = watts_to_ftp(int(m.group(2)))
        overhead = models.WARMUP_DURATION + models.COOLDOWN_DURATION
        main_sec = max(total_sec - overhead, 60)

        segments = [Segment(kind="steady", duration_sec=main_sec, power=power)]
        zn = zone_name(power)
        name = f"Ride_{dist_km}km_{zn}"
        return Workout(name=name, description=f"{dist_km}km {zn} ride",
                       segments=segments, source_text=source)

    # Pattern I: duration with zone qualifier (no explicit power) - "Z2 4-5h", "long Z2 4 h"
    m = re.search(
        rf'(?:long\s+)?(?:Z[12]\s+)?(\d+(?:\.\d+)?)\s*(?:{DASH}\s*(\d+(?:\.\d+)?)\s*)?(h|min)',
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
        label = "Recovery" if power == models.RECOVERY_POWER else "Z2"
        name = f"{label}_{_format_dur(total_sec)}"
        return Workout(name=name, description=f"{label} ride",
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
