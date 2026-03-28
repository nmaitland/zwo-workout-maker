import argparse
import csv
import os
import re
from metrics import compute_workout_metrics
from parser import parse_description, load_power_zones
from builder import build_zwo
import models
from models import Workout

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
OUTPUT_DIR = "output"
CTL_TIME_CONSTANT_DAYS = 42


def dedup_key(workout: Workout) -> str:
    """Generate a hashable key from workout structure for deduplication."""
    parts = []
    for seg in workout.segments:
        if seg.kind == "steady":
            parts.append(f"S:{seg.duration_sec}:{seg.power}")
        elif seg.kind == "intervals":
            parts.append(f"I:{seg.repeat}:{seg.on_duration}:{seg.on_power}")
        elif seg.kind == "ramp":
            parts.append(f"R:{seg.duration_sec}:{seg.power}:{seg.power_high}")
    return "|".join(parts)


def sanitize_filename(name: str) -> str:
    """Clean up a workout name for use as a filename."""
    name = re.sub(r'[^\w\-]', '_', name)
    name = re.sub(r'_+', '_', name)
    return name.strip('_')


def parse_args():
    parser = argparse.ArgumentParser(description="Generate Zwift .zwo workout files from a training plan CSV")
    parser.add_argument("--ftp", type=int, default=200, help="Functional Threshold Power in watts (default: 200)")
    parser.add_argument("--starting-ctl", type=float, default=0.0, help="Starting CTL value for weekly ramp calculation (default: 0)")
    parser.add_argument("--prefix", type=str, default="", help="Prefix for output filenames (e.g. 'MyPlan_')")
    parser.add_argument("--plan", type=str, default=None, help="Path to training plan CSV (default: training-plan.csv)")
    parser.add_argument("--mappings", type=str, default=None, help="Path to power mappings CSV (default: power-mappings.csv)")
    parser.add_argument("--clean-output", action="store_true", help="Delete existing .zwo files from the output directory before generating")
    return parser.parse_args()


def split_phase_and_tss(value: str) -> str:
    return value.split("|", 1)[0].strip()


def compute_weekly_ctl_ramp(daily_tss_values: list[list[int]], starting_ctl: float) -> list[float]:
    ctl = starting_ctl
    weekly_ramps: list[float] = []
    alpha = 1 / CTL_TIME_CONSTANT_DAYS

    for week_daily_tss in daily_tss_values:
        week_start_ctl = ctl
        for daily_tss in week_daily_tss:
            ctl += alpha * (daily_tss - ctl)
        weekly_ramps.append(round(ctl - week_start_ctl, 1))

    return weekly_ramps


def rewrite_training_plan(
    csv_path: str,
    rows: list[dict[str, str]],
    weekly_totals: list[int],
    weekly_ctl_ramps: list[float],
):
    fieldnames = ["Week commencing", "Phase", "Weekly TSS", "Weekly CTL Ramp", "Total hours", *DAYS]
    normalized_rows = []

    for row, weekly_total, weekly_ctl_ramp in zip(rows, weekly_totals, weekly_ctl_ramps):
        normalized = {
            "Week commencing": row.get("Week commencing", ""),
            "Phase": split_phase_and_tss(row.get("Phase / weekly TSS", row.get("Phase", ""))),
            "Weekly TSS": str(weekly_total),
            "Weekly CTL Ramp": f"{weekly_ctl_ramp:.1f}",
            "Total hours": row.get("Total hours", ""),
        }
        for day in DAYS:
            normalized[day] = row.get(day, "")
        normalized_rows.append(normalized)

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(normalized_rows)


def main():
    args = parse_args()
    script_dir = os.path.dirname(os.path.abspath(__file__))
    csv_path = args.plan or os.path.join(script_dir, "training-plan.csv")
    mappings_path = args.mappings or os.path.join(script_dir, "power-mappings.csv")
    out_dir = os.path.join(script_dir, OUTPUT_DIR)
    os.makedirs(out_dir, exist_ok=True)

    if args.clean_output:
        removed = 0
        for name in os.listdir(out_dir):
            if not name.lower().endswith(".zwo"):
                continue
            os.remove(os.path.join(out_dir, name))
            removed += 1
        print(f"Removed {removed} existing ZWO files from {OUTPUT_DIR}/")

    models.FTP = args.ftp
    load_power_zones(mappings_path)

    workouts: dict[str, tuple[Workout, str]] = {}  # dedup_key -> (workout, filename)
    skipped = []
    parse_failures = []

    with open(csv_path, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    weekly_totals: list[int] = []
    daily_tss_values: list[list[int]] = []
    for row in rows:
        weekly_total = 0
        week_daily_tss: list[int] = []
        for day in DAYS:
            cell = row.get(day, "").strip()
            if not cell:
                week_daily_tss.append(0)
                continue

            workout = parse_description(cell)
            if workout is None:
                skipped.append(f"  {row.get('Week commencing', '').strip()} {day}: {cell}")
                week_daily_tss.append(0)
                continue

            workout.metrics = compute_workout_metrics(workout)
            weekly_total += workout.metrics.training_stress_score
            week_daily_tss.append(workout.metrics.training_stress_score)

            key = dedup_key(workout)
            if key in workouts:
                continue

            filename = sanitize_filename(args.prefix + workout.name)
            workouts[key] = (workout, filename)

        weekly_totals.append(weekly_total)
        daily_tss_values.append(week_daily_tss)

    weekly_ctl_ramps = compute_weekly_ctl_ramp(daily_tss_values, args.starting_ctl)
    rewrite_training_plan(csv_path, rows, weekly_totals, weekly_ctl_ramps)

    # Write ZWO files, handling filename collisions
    written = []
    used_filenames: set[str] = set()
    for key, (workout, filename) in workouts.items():
        # Resolve collisions by appending a suffix
        final = filename
        counter = 2
        while final in used_filenames:
            final = f"{filename}_{counter}"
            counter += 1
        used_filenames.add(final)

        zwo_xml = build_zwo(workout)
        filepath = os.path.join(out_dir, f"{final}.zwo")
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(zwo_xml)
        written.append(final)

    # Summary
    print(f"\nGenerated {len(written)} ZWO files in {OUTPUT_DIR}/:")
    for name in sorted(written):
        print(f"  {name}.zwo")

    if skipped:
        print(f"\nSkipped {len(skipped)} sessions (rest/travel):")
        for s in skipped:
            print(s)

    if parse_failures:
        print(f"\nFailed to parse {len(parse_failures)} sessions:")
        for s in parse_failures:
            print(f"  {s}")


if __name__ == "__main__":
    main()
