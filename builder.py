import xml.etree.ElementTree as ET
import xml.dom.minidom
from metrics import format_metrics
from models import (
    Workout, Segment,
    WARMUP_DURATION, WARMUP_LOW, WARMUP_HIGH,
    COOLDOWN_DURATION, COOLDOWN_LOW, COOLDOWN_HIGH,
    OVER_UNDER_BLOCK, OVER_UNDER_THRESHOLD, OVER_UNDER_PLUS, OVER_UNDER_MINUS,
    FTP,
)

WARMUP_MESSAGES = (
    "Light hands, loose shoulders, and let the flywheel come to you",
    "Build pressure through the whole pedal stroke, like smoothing wet paint",
)

COOLDOWN_MESSAGE = "Exhale, soften the grip, and let the legs come back under you"

RECOVERY_MESSAGES = (
    "Back off, drop the shoulders, and make every breath long and quiet",
    "Easy speed now. Let the heart rate fall while the legs keep turning cleanly",
    "Reset the posture: tall spine, soft elbows, relaxed jaw",
)

ENDURANCE_MESSAGES = {
    "start": (
        "Ride like you could do this all day: quiet upper body, steady chain",
        "Think diesel engine: calm breathing, even pressure, no wasted motion",
    ),
    "mid": (
        "Keep the hips still and draw smooth circles through the bottom of the stroke",
        "Imagine a long valley road: patient pace, relaxed hands, rhythm first",
    ),
}

TEMPO_MESSAGES = {
    "start": (
        "Tempo is controlled pressure: tall torso, firm core, and no stomping",
        "Sit proud over the bottom bracket and let the cadence stay honest",
    ),
    "mid": (
        "Stay just under the red line. Smooth face, heavy legs, calm breathing",
        "Picture a steady climb you can see for miles: committed, never rushed",
    ),
}

THRESHOLD_MESSAGES = {
    "first": (
        "First rep: lock into the effort early and keep the torso quiet",
        "Ride the line, not above it. Strong core, smooth breathing, eyes up",
    ),
    "mid": (
        "Hold form under load: elbows soft, chin still, power driven from the hips",
        "This is the hard, honest work. Keep the pressure even and the mind narrow",
    ),
    "last": (
        "Final rep: stay tall, keep the pedals turning over, and squeeze every clean watt",
        "Think of cresting the last rise without fading: brave legs, calm head",
    ),
}

SWEETSPOT_MESSAGES = {
    "first": (
        "Sweet spot should feel firm, not frantic. Stay seated and keep the cadence round",
        "Settle into sustainable pressure and let the effort come to you",
    ),
    "mid": (
        "Heavy but controlled. Breathe into the belly and keep the upper body quiet",
        "Imagine a long false flat with no surges, just steady intent",
    ),
    "last": (
        "Final block: same patience, same posture, no heroics",
        "Stay smooth to the end and make the last minutes look the most composed",
    ),
}


def build_zwo(workout: Workout) -> str:
    """Convert a Workout into a ZWO XML string."""
    root = ET.Element("workout_file")
    ET.SubElement(root, "author").text = "Training Plan Generator"
    ET.SubElement(root, "name").text = workout.display_name
    description = workout.description
    if workout.metrics is not None:
        description = f"{description} | {format_metrics(workout.metrics)}"
    ET.SubElement(root, "description").text = description
    ET.SubElement(root, "sportType").text = "bike"

    wo = ET.SubElement(root, "workout")

    # Warmup
    warmup = ET.SubElement(wo, "Warmup",
                           Duration=str(WARMUP_DURATION),
                           PowerLow=f"{WARMUP_LOW:.2f}",
                           PowerHigh=f"{WARMUP_HIGH:.2f}")
    _add_text(warmup, 0, WARMUP_MESSAGES[0])
    _add_text(warmup, WARMUP_DURATION // 2, WARMUP_MESSAGES[1])

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
    _add_text(cooldown, 0, COOLDOWN_MESSAGE)

    return _prettify(root)


def _emit_steady(wo: ET.Element, seg: Segment, time_offset: int) -> int:
    """Emit a steady segment, converting to over-unders if longer than threshold."""
    start_msg, mid_msg = _steady_messages(seg.power)

    if seg.duration_sec <= OVER_UNDER_THRESHOLD:
        el = ET.SubElement(wo, "SteadyState",
                           Duration=str(seg.duration_sec),
                           Power=f"{seg.power:.2f}")
        _add_text(el, 0, start_msg)
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
        _add_text(el, 0, start_msg)
        mid_time = (full_cycles // 2) * cycle
        _add_text(el, mid_time, mid_msg)
        time_offset += full_cycles * cycle

    if remainder > 0:
        ET.SubElement(wo, "SteadyState",
                      Duration=str(remainder),
                      Power=f"{seg.power:.2f}")
        time_offset += remainder

    return time_offset


def _emit_intervals(wo: ET.Element, seg: Segment, time_offset: int) -> int:
    """Emit an interval block. If ON duration > 5 min, apply over-unders within each interval."""
    first_msg, mid_msg, last_msg = _interval_messages(seg.on_power)

    if seg.on_duration <= OVER_UNDER_THRESHOLD:
        # Short intervals - use simple IntervalsT
        el = ET.SubElement(wo, "IntervalsT",
                           Repeat=str(seg.repeat),
                           OnDuration=str(seg.on_duration),
                           OffDuration=str(seg.off_duration),
                           OnPower=f"{seg.on_power:.2f}",
                           OffPower=f"{seg.off_power:.2f}")

        _add_text(el, 0, first_msg)
        if seg.repeat > 2:
            mid_interval = (seg.repeat // 2) * (seg.on_duration + seg.off_duration)
            _add_text(el, mid_interval, mid_msg)
        if seg.repeat > 1:
            last_interval = (seg.repeat - 1) * (seg.on_duration + seg.off_duration)
            _add_text(el, last_interval, last_msg)

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
                msg = first_msg
            elif i == seg.repeat - 1:
                msg = last_msg
            else:
                msg = mid_msg

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
                _add_text(rest_el, 0, _recovery_message(i))
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


def _steady_messages(power: float) -> tuple[str, str]:
    if power <= 0.58:
        return (
            "This is genuine recovery. Feather the pedals and let the body freshen up",
            "Keep the cadence light and imagine washing the fatigue out of the legs",
        )
    if power <= 0.72:
        return ENDURANCE_MESSAGES["start"][0], ENDURANCE_MESSAGES["mid"][0]
    if power <= 0.88:
        return TEMPO_MESSAGES["start"][0], TEMPO_MESSAGES["mid"][0]
    return SWEETSPOT_MESSAGES["start"][0], SWEETSPOT_MESSAGES["mid"][0]


def _interval_messages(power: float) -> tuple[str, str, str]:
    if power >= 0.98:
        return (
            THRESHOLD_MESSAGES["first"][0],
            THRESHOLD_MESSAGES["mid"][0],
            THRESHOLD_MESSAGES["last"][0],
        )
    if power >= 0.88:
        return (
            SWEETSPOT_MESSAGES["first"][0],
            SWEETSPOT_MESSAGES["mid"][0],
            SWEETSPOT_MESSAGES["last"][0],
        )
    return (
        TEMPO_MESSAGES["start"][1],
        TEMPO_MESSAGES["mid"][1],
        "Last block: stay patient and keep the pressure tidy all the way through",
    )


def _recovery_message(index: int) -> str:
    return RECOVERY_MESSAGES[index % len(RECOVERY_MESSAGES)]


def _prettify(root: ET.Element) -> str:
    """Pretty-print XML."""
    rough = ET.tostring(root, encoding="unicode")
    parsed = xml.dom.minidom.parseString(rough)
    lines = parsed.toprettyxml(indent="  ", encoding=None).split("\n")
    # Remove the XML declaration line
    return "\n".join(line for line in lines if line.strip() and not line.startswith("<?xml"))
