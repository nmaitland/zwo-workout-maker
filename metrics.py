from collections import deque

import models
from models import (
    Segment,
    Workout,
    WorkoutMetrics,
    WARMUP_DURATION,
    WARMUP_LOW,
    WARMUP_HIGH,
    COOLDOWN_DURATION,
    COOLDOWN_LOW,
    COOLDOWN_HIGH,
    OVER_UNDER_BLOCK,
    OVER_UNDER_THRESHOLD,
    OVER_UNDER_PLUS,
    OVER_UNDER_MINUS,
)


def compute_workout_metrics(workout: Workout) -> WorkoutMetrics:
    """Compute AP, NP, IF, VI, and TSS from the generated workout structure."""
    profile = expand_workout_profile(workout)
    duration_sec = len(profile)
    if duration_sec == 0:
        return WorkoutMetrics(
            duration_sec=0,
            average_power=0.0,
            normalized_power=0.0,
            intensity_factor=0.0,
            variability_index=0.0,
            training_stress_score=0,
        )

    average_power = sum(profile) / duration_sec
    normalized_power = _normalized_power(profile)
    intensity_factor = normalized_power / models.FTP if models.FTP else 0.0
    variability_index = normalized_power / average_power if average_power else 0.0
    duration_hours = duration_sec / 3600
    training_stress_score = round(duration_hours * (intensity_factor ** 2) * 100)

    return WorkoutMetrics(
        duration_sec=duration_sec,
        average_power=average_power,
        normalized_power=normalized_power,
        intensity_factor=intensity_factor,
        variability_index=variability_index,
        training_stress_score=training_stress_score,
    )


def expand_workout_profile(workout: Workout) -> list[float]:
    """Expand a workout into one power sample per second."""
    profile: list[float] = []
    _append_ramp(profile, WARMUP_DURATION, WARMUP_LOW, WARMUP_HIGH)
    for segment in workout.segments:
        _append_segment(profile, segment)
    _append_ramp(profile, COOLDOWN_DURATION, COOLDOWN_LOW, COOLDOWN_HIGH)
    return profile


def format_metrics(metrics: WorkoutMetrics) -> str:
    return (
        f"IF {metrics.intensity_factor:.2f} | "
        f"VI {metrics.variability_index:.2f} | "
        f"TSS {metrics.training_stress_score}"
    )


def _append_segment(profile: list[float], segment: Segment):
    if segment.kind == "steady":
        _append_steady(profile, segment.duration_sec, segment.power)
        return
    if segment.kind == "intervals":
        _append_intervals(profile, segment)
        return
    if segment.kind == "ramp":
        _append_ramp(profile, segment.duration_sec, segment.power, segment.power_high)


def _append_steady(profile: list[float], duration_sec: int, power_frac: float):
    if duration_sec <= OVER_UNDER_THRESHOLD:
        _append_flat(profile, duration_sec, power_frac)
        return

    over_power = round(power_frac * (1 + OVER_UNDER_PLUS), 2)
    under_power = round(power_frac * (1 - OVER_UNDER_MINUS), 2)
    cycle = OVER_UNDER_BLOCK * 2
    full_cycles = duration_sec // cycle
    remainder = duration_sec % cycle

    for _ in range(full_cycles):
        _append_flat(profile, OVER_UNDER_BLOCK, over_power)
        _append_flat(profile, OVER_UNDER_BLOCK, under_power)

    if remainder > 0:
        _append_flat(profile, remainder, power_frac)


def _append_intervals(profile: list[float], segment: Segment):
    if segment.on_duration <= OVER_UNDER_THRESHOLD:
        for index in range(segment.repeat):
            _append_flat(profile, segment.on_duration, segment.on_power)
            if index < segment.repeat - 1:
                _append_flat(profile, segment.off_duration, segment.off_power)
        return

    over_power = round(segment.on_power * (1 + OVER_UNDER_PLUS), 2)
    under_power = round(segment.on_power * (1 - OVER_UNDER_MINUS), 2)
    cycle = OVER_UNDER_BLOCK * 2
    full_cycles = segment.on_duration // cycle
    remainder = segment.on_duration % cycle

    for index in range(segment.repeat):
        for _ in range(full_cycles):
            _append_flat(profile, OVER_UNDER_BLOCK, over_power)
            _append_flat(profile, OVER_UNDER_BLOCK, under_power)
        if remainder > 0:
            _append_flat(profile, remainder, segment.on_power)
        if index < segment.repeat - 1:
            _append_flat(profile, segment.off_duration, segment.off_power)


def _append_ramp(profile: list[float], duration_sec: int, start_power_frac: float, end_power_frac: float):
    if duration_sec <= 0:
        return
    if end_power_frac is None:
        end_power_frac = start_power_frac
    if duration_sec == 1:
        profile.append(start_power_frac * models.FTP)
        return
    step = (end_power_frac - start_power_frac) / (duration_sec - 1)
    for second in range(duration_sec):
        profile.append((start_power_frac + step * second) * models.FTP)


def _append_flat(profile: list[float], duration_sec: int, power_frac: float):
    if duration_sec <= 0:
        return
    watts = power_frac * models.FTP
    profile.extend([watts] * duration_sec)


def _normalized_power(profile: list[float]) -> float:
    if not profile:
        return 0.0

    window = deque()
    rolling_sum = 0.0
    rolling_averages: list[float] = []

    for sample in profile:
        window.append(sample)
        rolling_sum += sample
        if len(window) > 30:
            rolling_sum -= window.popleft()
        rolling_averages.append(rolling_sum / len(window))

    mean_fourth = sum(avg ** 4 for avg in rolling_averages) / len(rolling_averages)
    return mean_fourth ** 0.25
