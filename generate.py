import argparse
import csv
import os
import re
from parser import parse_description, load_power_zones
from builder import build_zwo
import models
from models import Workout

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
OUTPUT_DIR = "output"


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
    parser.add_argument("--prefix", type=str, default="", help="Prefix for output filenames (e.g. 'MyPlan_')")
    parser.add_argument("--plan", type=str, default=None, help="Path to training plan CSV (default: training-plan.csv)")
    parser.add_argument("--mappings", type=str, default=None, help="Path to power mappings CSV (default: power-mappings.csv)")
    return parser.parse_args()


def main():
    args = parse_args()
    script_dir = os.path.dirname(os.path.abspath(__file__))
    csv_path = args.plan or os.path.join(script_dir, "training-plan.csv")
    mappings_path = args.mappings or os.path.join(script_dir, "power-mappings.csv")
    out_dir = os.path.join(script_dir, OUTPUT_DIR)
    os.makedirs(out_dir, exist_ok=True)

    models.FTP = args.ftp
    load_power_zones(mappings_path)

    workouts: dict[str, tuple[Workout, str]] = {}  # dedup_key -> (workout, filename)
    skipped = []
    parse_failures = []

    with open(csv_path, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            week = row.get("Week commencing", "").strip()
            if not week:
                continue
            for day in DAYS:
                cell = row.get(day, "").strip()
                if not cell:
                    continue

                workout = parse_description(cell)
                if workout is None:
                    skipped.append(f"  {week} {day}: {cell}")
                    continue

                key = dedup_key(workout)
                if key in workouts:
                    continue

                filename = sanitize_filename(args.prefix + workout.name)
                workouts[key] = (workout, filename)

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
