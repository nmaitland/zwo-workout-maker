import xml.etree.ElementTree as ET
import xml.dom.minidom
from models import (
    Workout, Segment,
    WARMUP_DURATION, WARMUP_LOW, WARMUP_HIGH,
    COOLDOWN_DURATION, COOLDOWN_LOW, COOLDOWN_HIGH,
    OVER_UNDER_BLOCK, OVER_UNDER_THRESHOLD, OVER_UNDER_PLUS, OVER_UNDER_MINUS,
    FTP,
)

COACHING_MESSAGES = {
    "warmup_start": "Easy spin to get the legs going",
    "warmup_mid": "Gradually building into the session",
    "interval_first": "First interval - find your rhythm and settle in",
    "interval_mid": "Halfway through the hard efforts! Stay strong",
    "interval_last": "Final interval - make it count!",
    "steady_start": "Settle in, keep it smooth and consistent",
    "steady_mid": "Halfway through this block - you are doing great",
    "near_end": "Nearly there, stay focused to the finish",
    "cooldown": "Great work! Spin the legs out easy",
}


def build_zwo(workout: Workout) -> str:
    """Convert a Workout into a ZWO XML string."""
    root = ET.Element("workout_file")
    ET.SubElement(root, "author").text = "Training Plan Generator"
    ET.SubElement(root, "name").text = workout.display_name
    ET.SubElement(root, "description").text = workout.description
    ET.SubElement(root, "sportType").text = "bike"

    wo = ET.SubElement(root, "workout")

    # Warmup
    warmup = ET.SubElement(wo, "Warmup",
                           Duration=str(WARMUP_DURATION),
                           PowerLow=f"{WARMUP_LOW:.2f}",
                           PowerHigh=f"{WARMUP_HIGH:.2f}")
    _add_text(warmup, 0, COACHING_MESSAGES["warmup_start"])
    _add_text(warmup, WARMUP_DURATION // 2, COACHING_MESSAGES["warmup_mid"])

    # Main segments
    time_offset = WARMUP_DURATION
    for seg in workout.segments:
        if seg.kind == "steady":
            time_offset = _emit_steady(wo, seg, time_offset)
        elif seg.kind == "intervals":
            time_offset = _emit_intervals(wo, seg, time_offset)
        elif seg.kind == "ramp":
            _emit_ramp(wo, seg)
            time_offset += seg.duration_sec

    # Cooldown
    cooldown = ET.SubElement(wo, "Cooldown",
                             Duration=str(COOLDOWN_DURATION),
                             PowerLow=f"{COOLDOWN_LOW:.2f}",
                             PowerHigh=f"{COOLDOWN_HIGH:.2f}")
    _add_text(cooldown, 0, COACHING_MESSAGES["cooldown"])

    return _prettify(root)


def _emit_steady(wo: ET.Element, seg: Segment, time_offset: int) -> int:
    """Emit a steady segment, converting to over-unders if longer than threshold."""
    if seg.duration_sec <= OVER_UNDER_THRESHOLD:
        el = ET.SubElement(wo, "SteadyState",
                           Duration=str(seg.duration_sec),
                           Power=f"{seg.power:.2f}")
        _add_text(el, 0, COACHING_MESSAGES["steady_start"])
        return time_offset + seg.duration_sec

    # Convert to over-unders
    over_power = round(seg.power * (1 + OVER_UNDER_PLUS), 2)
    under_power = round(seg.power * (1 - OVER_UNDER_MINUS), 2)
    cycle = OVER_UNDER_BLOCK * 2  # one over + one under
    full_cycles = seg.duration_sec // cycle
    remainder = seg.duration_sec % cycle

    if full_cycles > 0:
        el = ET.SubElement(wo, "IntervalsT",
                           Repeat=str(full_cycles),
                           OnDuration=str(OVER_UNDER_BLOCK),
                           OffDuration=str(OVER_UNDER_BLOCK),
                           OnPower=f"{over_power:.2f}",
                           OffPower=f"{under_power:.2f}")
        _add_text(el, 0, COACHING_MESSAGES["steady_start"])
        mid_time = (full_cycles // 2) * cycle
        _add_text(el, mid_time, COACHING_MESSAGES["steady_mid"])
        time_offset += full_cycles * cycle

    if remainder > 0:
        ET.SubElement(wo, "SteadyState",
                      Duration=str(remainder),
                      Power=f"{seg.power:.2f}")
        time_offset += remainder

    return time_offset


def _emit_intervals(wo: ET.Element, seg: Segment, time_offset: int) -> int:
    """Emit an interval block. If ON duration > 5 min, apply over-unders within each interval."""
    if seg.on_duration <= OVER_UNDER_THRESHOLD:
        # Short intervals - use simple IntervalsT
        el = ET.SubElement(wo, "IntervalsT",
                           Repeat=str(seg.repeat),
                           OnDuration=str(seg.on_duration),
                           OffDuration=str(seg.off_duration),
                           OnPower=f"{seg.on_power:.2f}",
                           OffPower=f"{seg.off_power:.2f}")

        _add_text(el, 0, COACHING_MESSAGES["interval_first"])
        if seg.repeat > 2:
            mid_interval = (seg.repeat // 2) * (seg.on_duration + seg.off_duration)
            _add_text(el, mid_interval, COACHING_MESSAGES["interval_mid"])
        if seg.repeat > 1:
            last_interval = (seg.repeat - 1) * (seg.on_duration + seg.off_duration)
            _add_text(el, last_interval, COACHING_MESSAGES["interval_last"])

        total = seg.repeat * (seg.on_duration + seg.off_duration) - seg.off_duration
        return time_offset + total
    else:
        # Long intervals - each ON period becomes over-unders, with rest between
        over_power = round(seg.on_power * (1 + OVER_UNDER_PLUS), 2)
        under_power = round(seg.on_power * (1 - OVER_UNDER_MINUS), 2)
        cycle = OVER_UNDER_BLOCK * 2
        full_cycles = seg.on_duration // cycle
        remainder = seg.on_duration % cycle

        for i in range(seg.repeat):
            if i == 0:
                msg = COACHING_MESSAGES["interval_first"]
            elif i == seg.repeat - 1:
                msg = COACHING_MESSAGES["interval_last"]
            else:
                msg = COACHING_MESSAGES["interval_mid"]

            # Over-under block for this interval
            if full_cycles > 0:
                el = ET.SubElement(wo, "IntervalsT",
                                   Repeat=str(full_cycles),
                                   OnDuration=str(OVER_UNDER_BLOCK),
                                   OffDuration=str(OVER_UNDER_BLOCK),
                                   OnPower=f"{over_power:.2f}",
                                   OffPower=f"{under_power:.2f}")
                _add_text(el, 0, msg)
                time_offset += full_cycles * cycle

            if remainder > 0:
                ET.SubElement(wo, "SteadyState",
                              Duration=str(remainder),
                              Power=f"{seg.on_power:.2f}")
                time_offset += remainder

            # Rest between intervals (not after last)
            if i < seg.repeat - 1:
                rest_el = ET.SubElement(wo, "SteadyState",
                                        Duration=str(seg.off_duration),
                                        Power=f"{seg.off_power:.2f}")
                _add_text(rest_el, 0, "Recovery - easy spinning")
                time_offset += seg.off_duration

        return time_offset


def _emit_ramp(wo: ET.Element, seg: Segment):
    """Emit a ramp segment."""
    ET.SubElement(wo, "Ramp",
                  Duration=str(seg.duration_sec),
                  PowerLow=f"{seg.power:.2f}",
                  PowerHigh=f"{seg.power_high:.2f}")


def _add_text(parent: ET.Element, timeoffset: int, message: str):
    """Add a coaching text event."""
    ET.SubElement(parent, "textevent",
                  timeoffset=str(timeoffset),
                  message=message)


def _prettify(root: ET.Element) -> str:
    """Pretty-print XML."""
    rough = ET.tostring(root, encoding="unicode")
    parsed = xml.dom.minidom.parseString(rough)
    lines = parsed.toprettyxml(indent="  ", encoding=None).split("\n")
    # Remove the XML declaration line
    return "\n".join(line for line in lines if line.strip() and not line.startswith("<?xml"))
