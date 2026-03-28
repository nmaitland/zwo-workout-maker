import argparse
import csv
import json
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError

import models
from generate import DAYS, dedup_key, sanitize_filename
from intervals_icu_api import DEFAULT_API_BASE, api_request, get_api_key
from parser import load_power_zones, parse_description
from upload_intervals_icu import build_events, load_rows


def parse_args():
    parser = argparse.ArgumentParser(
        description="Upload generated ZWO workouts to the Intervals.icu workout library, optionally also scheduling them."
    )
    parser.add_argument("--ftp", type=int, default=200, help="Functional Threshold Power in watts (default: 200)")
    parser.add_argument("--plan", type=str, default="training-plan.csv", help="Path to training plan CSV")
    parser.add_argument("--mappings", type=str, default="power-mappings.csv", help="Path to power mappings CSV")
    parser.add_argument("--output-dir", type=str, default="output", help="Directory containing generated ZWO files")
    parser.add_argument("--prefix", type=str, default="", help="Filename prefix used when generating ZWO files")
    parser.add_argument("--api-key", type=str, default=None, help="Intervals.icu API key")
    parser.add_argument("--athlete-id", type=str, default="0", help="Intervals.icu athlete id or 0 for the API key owner")
    parser.add_argument("--api-base", type=str, default=DEFAULT_API_BASE, help="Intervals.icu API base URL")
    parser.add_argument("--folder-id", type=int, default=None, help="Existing Intervals.icu folder id")
    parser.add_argument("--folder-name", type=str, default="CTS Workout Library", help="Folder name to create or reuse")
    parser.add_argument("--workout-type", type=str, default="VirtualRide", help="Intervals.icu workout type")
    parser.add_argument("--as-plan", action="store_true", help="Upload the full dated plan to a library plan using day offsets")
    parser.add_argument("--also-schedule", action="store_true", help="Also upload the scheduled plan to the calendar after library upload")
    parser.add_argument("--start-time", type=str, default="06:00:00", help="Local workout start time in HH:MM:SS for calendar scheduling")
    parser.add_argument("--external-id-prefix", type=str, default="zwo-maker", help="Prefix for Intervals.icu external_id values")
    parser.add_argument("--dry-run", action="store_true", help="Write planned requests to JSON and do not upload")
    parser.add_argument("--dry-run-out", type=str, default="intervals-library.json", help="Path for the dry-run JSON payload")
    return parser.parse_args()


def parse_week_date(value: str) -> datetime:
    return datetime.strptime(value.strip(), "%d-%b-%y")


def build_library_workouts(
    rows: list[dict[str, str]],
    output_dir: Path,
    prefix: str,
    workout_type: str,
    folder_id: int,
    as_plan: bool,
) -> tuple[list[dict], list[str]]:
    workouts: list[dict] = []
    warnings: list[str] = []
    seen_keys: set[str] = set()
    first_plan_date = None

    if as_plan:
        dates = [parse_week_date(row["Week commencing"]) for row in rows if row.get("Week commencing", "").strip()]
        first_plan_date = min(dates) if dates else None

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

            key = dedup_key(workout)
            if not as_plan and key in seen_keys:
                continue
            seen_keys.add(key)

            filename = sanitize_filename(prefix + workout.name) + ".zwo"
            filepath = output_dir / filename
            if not filepath.exists():
                warnings.append(f"Missing ZWO file for {week_text} {day}: {filepath.name}")
                continue

            payload = {
                "folder_id": folder_id,
                "name": workout.display_name,
                "description": workout.source_text,
                "type": workout_type,
                "filename": filename,
                "file_contents": filepath.read_text(encoding="utf-8"),
            }

            if as_plan and first_plan_date is not None:
                workout_date = week_start + timedelta(days=day_index)
                payload["day"] = (workout_date.date() - first_plan_date.date()).days

            workouts.append(payload)

    return workouts, warnings


def write_dry_run(path: Path, payload: dict):
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_folders(api_base: str, athlete_id: str, api_key: str) -> list[dict]:
    response_text = api_request(api_base, athlete_id, api_key, "GET", "/folders")
    return json.loads(response_text)


def ensure_folder(api_base: str, athlete_id: str, api_key: str, folder_id: int | None, folder_name: str) -> tuple[int, bool]:
    if folder_id is not None:
        return folder_id, False

    folders = load_folders(api_base, athlete_id, api_key)
    for folder in folders:
        if folder.get("name") == folder_name:
            return int(folder["id"]), False

    response_text = api_request(api_base, athlete_id, api_key, "POST", "/folders", {"name": folder_name})
    folder = json.loads(response_text)
    return int(folder["id"]), True


def upload_library_workouts(api_base: str, athlete_id: str, api_key: str, workouts: list[dict]) -> list[dict]:
    responses = []
    for workout in workouts:
        response_text = api_request(api_base, athlete_id, api_key, "POST", "/workouts", workout)
        responses.append(json.loads(response_text))
    return responses


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

    dry_run_payload = {
        "folder_name": args.folder_name,
        "folder_id": args.folder_id,
        "as_plan": args.as_plan,
        "also_schedule": args.also_schedule,
        "workouts": [],
        "events": [],
    }

    api_key = None
    resolved_folder_id = args.folder_id
    created_folder = False
    if not args.dry_run:
        api_key = get_api_key(args.api_key)
        try:
            resolved_folder_id, created_folder = ensure_folder(
                args.api_base, args.athlete_id, api_key, args.folder_id, args.folder_name
            )
        except HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise SystemExit(f"Folder lookup/create failed with HTTP {exc.code}: {error_body}") from exc
        except URLError as exc:
            raise SystemExit(f"Folder lookup/create failed: {exc}") from exc

    if resolved_folder_id is None:
        resolved_folder_id = 0

    workouts, warnings = build_library_workouts(
        rows, output_dir, args.prefix, args.workout_type, resolved_folder_id, args.as_plan
    )
    dry_run_payload["folder_id"] = resolved_folder_id
    dry_run_payload["workouts"] = workouts

    if args.also_schedule:
        events, event_warnings = build_events(rows, output_dir, args.prefix, args.external_id_prefix, args.start_time)
        warnings.extend(event_warnings)
        dry_run_payload["events"] = events
    else:
        events = []

    if warnings:
        print("Warnings:")
        for warning in warnings:
            print(f"  {warning}")

    if not workouts:
        print("No workouts found to upload.")
        return

    write_dry_run(dry_run_out, dry_run_payload)
    print(f"Wrote dry-run payload to {dry_run_out.name}")

    if args.dry_run:
        print("Dry run only. No upload attempted.")
        return

    try:
        responses = upload_library_workouts(args.api_base, args.athlete_id, api_key, workouts)
        print(f"Uploaded {len(responses)} workouts to folder {resolved_folder_id}")
        if created_folder:
            print(f"Created folder '{args.folder_name}'")

        if events:
            api_request(args.api_base, args.athlete_id, api_key, "POST", "/events/bulk?upsert=true", events)
            print(f"Uploaded {len(events)} planned workout events to the calendar")
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Upload failed with HTTP {exc.code}: {error_body}") from exc
    except URLError as exc:
        raise SystemExit(f"Upload failed: {exc}") from exc


if __name__ == "__main__":
    main()
