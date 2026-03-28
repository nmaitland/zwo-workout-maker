# ZWO Workout Maker

Converts a cycling training plan spreadsheet into Zwift `.zwo` workout files and can optionally prepare or upload the resulting plan to Intervals.icu.

## What it does

The generator reads a CSV training plan with weekly sessions described as free text and produces structured Zwift workouts.

- Power targets are stored as fractions of FTP, not fixed watts.
- Every workout gets a 10 minute ramp warmup and a 3 minute cooldown.
- Long steady blocks are converted into over-under style work to keep indoor rides moving.
- Interval recovery defaults to 60 percent FTP.
- Identical workout structures are deduplicated into a single `.zwo` file.
- Workout descriptions include computed `IF`, `VI`, and `TSS`.
- The input plan CSV is normalized and rewritten with `Phase`, `Weekly TSS`, and `Weekly CTL Ramp` columns.
- Generated workouts can be exported as Intervals.icu event or library payload JSON, or uploaded directly with an API key.

## Setup

Python 3.10 or newer is required. The project uses only the standard library.

## Inputs

| File | Description |
|------|-------------|
| `training-plan.csv` | Training plan with columns `Week commencing`, `Phase / weekly TSS`, `Total hours`, and `Mon` through `Sun` |
| `power-mappings.csv` | Maps power ranges to zone names used in filenames and descriptions |
| `workout-rules.txt` | Reference notes for workout construction rules |

Example CSV header and row:

```csv
Week commencing,Phase / weekly TSS,Total hours,Mon,Tue,Wed,Thu,Fri,Sat,Sun
23-Mar-26,Base | 600-650 TSS,12-13 h,Rest,3x10 Threshold (1.5 h),Endurance (2.5 h),...
```

Supported day-cell formats include:

- `3x10 @ 200-210 W (1.5 h)`
- `Z2 120-140 W (2.5 h)`
- `3.5 h + 2x30 @ 180-190 W`
- `60 min incl. 3x3 min @ 190-200 W`
- `Tempo 150-160 W (2 h)`
- `130km @ 145`
- `Rest` or `TRAVEL (no ride)` for skipped days

## Generate workouts

```bash
python generate.py
python generate.py --ftp 220
python generate.py --ftp 220 --prefix CTS_
python generate.py --plan path/to/plan.csv --mappings path/to/zones.csv
python generate.py --starting-ctl 56 --clean-output
```

| Argument | Default | Description |
|----------|---------|-------------|
| `--ftp` | `200` | Functional Threshold Power in watts |
| `--starting-ctl` | `0` | Starting CTL used for weekly CTL ramp calculations |
| `--prefix` | empty | Prefix added to output filenames |
| `--plan` | `training-plan.csv` | Training plan CSV path |
| `--mappings` | `power-mappings.csv` | Power mapping CSV path |
| `--clean-output` | off | Removes existing `.zwo` files from `output/` before regeneration |

Generated `.zwo` files are written to `output/`.

## Intervals.icu

Two helper scripts support Intervals.icu:

- `upload_intervals_icu.py` builds a dated calendar event payload and can upload it to the athlete calendar.
- `upload_intervals_library.py` builds a workout library payload and can optionally also schedule the plan.

Both scripts accept `--api-key`, but the recommended approach is to set `INTERVALS_ICU_API_KEY` in your shell environment.

Dry-run examples:

```bash
python upload_intervals_icu.py --dry-run
python upload_intervals_library.py --dry-run
python upload_intervals_library.py --as-plan --also-schedule --dry-run
```

Upload examples:

```bash
python upload_intervals_icu.py --athlete-id 0
python upload_intervals_library.py --folder-name "CTS Workout Library"
python upload_intervals_library.py --as-plan --also-schedule
```

Generated JSON payload files such as `intervals-events.json`, `intervals-library.json`, and `intervals-library-plan.json` are local artifacts and are ignored by Git.

## Configuration

Core workout-generation constants live in `models.py`, including warmup and cooldown durations, over-under settings, and recovery power.

## Project structure

```text
generate.py                  Entry point for CSV parsing, metric calculation, and ZWO generation
parser.py                    Free-text workout parser
builder.py                   Zwift XML builder and in-workout coaching text
metrics.py                   Workout metric calculations such as IF, VI, TSS, and CTL ramp inputs
upload_intervals_icu.py      Intervals.icu calendar event export and upload
upload_intervals_library.py  Intervals.icu library export and upload
intervals_icu_api.py         Shared Intervals.icu API helpers
models.py                    Data models and generation constants
```
