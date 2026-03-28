import argparse
import csv
import json
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError

from generate import DAYS, dedup_key, sanitize_filename
from intervals_icu_api import DEFAULT_API_BASE, api_request, get_api_key
from parser import load_power_zones, parse_description
import models


def parse_args():
    parser = argparse.ArgumentParser(
        description="Upload generated ZWO workouts from the training plan to an Intervals.icu calendar."
    )
    parser.add_argument("--ftp", type=int, default=200, help="Functional Threshold Power in watts (default: 200)")
    parser.add_argument("--plan", type=str, default="training-plan.csv", help="Path to training plan CSV")
    parser.add_argument("--mappings", type=str, default="power-mappings.csv", help="Path to power mappings CSV")
    parser.add_argument("--output-dir", type=str, default="output", help="Directory containing generated ZWO files")
    parser.add_argument("--prefix", type=str, default="", help="Filename prefix used when generating ZWO files")
    parser.add_argument("--api-key", type=str, default=None, help="Intervals.icu API key")
    parser.add_argument("--athlete-id", type=str, default="0", help="Intervals.icu athlete id or 0 for the API key owner")
    parser.add_argument("--api-base", type=str, default=DEFAULT_API_BASE, help="Intervals.icu API base URL")
    parser.add_argument("--start-time", type=str, default="06:00:00", help="Local workout start time in HH:MM:SS")
    parser.add_argument("--external-id-prefix", type=str, default="zwo-maker", help="Prefix for Intervals.icu external_id values")
    parser.add_argument("--dry-run", action="store_true", help="Write the API payload to JSON and do not upload")
    parser.add_argument("--dry-run-out", type=str, default="intervals-events.json", help="Path for the dry-run JSON payload")
    return parser.parse_args()


def parse_week_date(value: str) -> datetime:
    return datetime.strptime(value.strip(), "%d-%b-%y")


def load_rows(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def build_events(
    rows: list[dict[str, str]],
    output_dir: Path,
    prefix: str,
    external_id_prefix: str,
    start_time: str,
) -> tuple[list[dict], list[str]]:
    events: list[dict] = []
    warnings: list[str] = []

    for row in rows:
        week_text = row.get("Week commencing", "").strip()
        if not week_text:
            continue

        week_start = parse_week_date(week_text)

        for day_index, day in enumerate(DAYS):
            cell = row.get(day, "").strip()
            if not cell:
                continue

            workout = parse_description(cell)
            if workout is None:
                continue

            workout_date = week_start + timedelta(days=day_index)
            filename = sanitize_filename(prefix + workout.name) + ".zwo"
            filepath = output_dir / filename
            if not filepath.exists():
                warnings.append(f"Missing ZWO file for {week_text} {day}: {filepath.name}")
                continue

            file_contents = filepath.read_text(encoding="utf-8")
            external_id = f"{external_id_prefix}:{workout_date.date().isoformat()}:{dedup_key(workout)}"
            events.append(
                {
                    "category": "WORKOUT",
                    "start_date_local": f"{workout_date.date().isoformat()}T{start_time}",
                    "name": workout.display_name,
                    "description": workout.source_text,
                    "filename": filename,
                    "file_contents": file_contents,
                    "external_id": external_id,
                }
            )

    return events, warnings


def write_dry_run(path: Path, events: list[dict]):
    path.write_text(json.dumps(events, indent=2), encoding="utf-8")


def upload_events(api_base: str, athlete_id: str, api_key: str, events: list[dict]) -> str:
    return api_request(api_base, athlete_id, api_key, "POST", "/events/bulk?upsert=true", events)


def main():
    args = parse_args()

    script_dir = Path(__file__).resolve().parent
    csv_path = (script_dir / args.plan).resolve()
    mappings_path = (script_dir / args.mappings).resolve()
    output_dir = (script_dir / args.output_dir).resolve()
    dry_run_out = (script_dir / args.dry_run_out).resolve()

    models.FTP = args.ftp
    load_power_zones(str(mappings_path))

    rows = load_rows(csv_path)
    events, warnings = build_events(rows, output_dir, args.prefix, args.external_id_prefix, args.start_time)

    if warnings:
        print("Warnings:")
        for warning in warnings:
            print(f"  {warning}")

    if not events:
        print("No workout events found to upload.")
        return

    write_dry_run(dry_run_out, events)
    print(f"Wrote {len(events)} planned workout events to {dry_run_out.name}")

    if args.dry_run:
        print("Dry run only. No upload attempted.")
        return

    api_key = get_api_key(args.api_key)

    try:
        response_text = upload_events(args.api_base, args.athlete_id, api_key, events)
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Upload failed with HTTP {exc.code}: {error_body}") from exc
    except URLError as exc:
        raise SystemExit(f"Upload failed: {exc}") from exc

    print(f"Uploaded {len(events)} planned workout events to Intervals.icu")
    print(response_text)


if __name__ == "__main__":
    main()
