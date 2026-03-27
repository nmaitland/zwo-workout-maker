# ZWO Workout Maker

Converts a cycling training plan spreadsheet into Zwift `.zwo` workout files.

## How it works

Reads a CSV training plan (`training-plan.csv`) with weekly sessions described as free text (e.g. `3×10 @ 200–210 W (1.5 h)`, `Z2 120–140 W (2.5 h)`) and generates structured Zwift workouts applying these rules:

- **Power as % of FTP** — all targets are FTP fractions, not raw watts
- **Warmup/cooldown** — every workout gets a 10 min ramp warmup and 3 min cooldown
- **Over-unders** — any steady block longer than 5 min is converted to 2-min over/under intervals at target ±5%, keeping indoor sessions engaging
- **Interval rest** — recovery between intervals is 50% of interval duration at 60% FTP
- **Interval placement** — intervals start at most 20 min into the session (including warmup), with endurance filler before and after
- **Deduplication** — identical workout structures produce a single `.zwo` file
- **Zone naming** — filenames and descriptions use zone names (Endurance, Tempo, SweetSpot, Threshold, EventPace) from a configurable power mappings file
- **Coaching messages** — motivational text events at key moments during each workout

## Setup

Python 3.10+ required. No external dependencies — uses only the standard library.

## Input files

| File | Description |
|------|-------------|
| `training-plan.csv` | Your training plan with columns: Week commencing, Phase, Total hours, Mon–Sun |
| `power-mappings.csv` | Maps power ranges to zone names (Endurance, Tempo, SweetSpot, etc.) |
| `workout-rules.txt` | Reference document describing the workout construction rules |

### CSV format

```
Week commencing,Phase / weekly TSS,Total hours,Mon,Tue,Wed,Thu,Fri,Sat,Sun
23-Mar-26,Base | 600–650 TSS,12–13 h,Rest,3×10 @ 200–210 W (1.5 h),Z2 120–140 W (2.5 h),...
```

Each day cell contains a free-text session description. Supported formats include:
- `3×10 @ 200–210 W (1.5 h)` — intervals with total duration
- `Z2 120–140 W (2.5 h)` — steady state with power range
- `3.5 h + 2×30 @ 180–190 W` — long ride with intervals at end
- `60 min incl. 3×3 min @ 190–200 W` — endurance with embedded intervals
- `Tempo 150–160 W (2 h)` — zone-based steady state
- `130km @ 145` — distance-based sessions
- `Rest`, `TRAVEL (no ride)` — skipped automatically

## Usage

```bash
python generate.py
python generate.py --ftp 220
python generate.py --ftp 220 --prefix "MyPlan_"
python generate.py --plan path/to/plan.csv --mappings path/to/zones.csv
```

| Argument | Default | Description |
|----------|---------|-------------|
| `--ftp` | 200 | Functional Threshold Power in watts |
| `--prefix` | *(none)* | Prefix for output filenames (e.g. `MyPlan_`) |
| `--plan` | `training-plan.csv` | Path to training plan CSV |
| `--mappings` | `power-mappings.csv` | Path to power mappings CSV |

Generated `.zwo` files are written to `./output/`.

## Configuration

Additional constants in `models.py`:

| Constant | Default | Description |
|----------|---------|-------------|
| `WARMUP_DURATION` | 600 | Warmup length in seconds (10 min) |
| `COOLDOWN_DURATION` | 180 | Cooldown length in seconds (3 min) |
| `OVER_UNDER_BLOCK` | 120 | Over/under interval length in seconds (2 min) |
| `OVER_UNDER_THRESHOLD` | 300 | Blocks longer than this become over-unders (5 min) |
| `REST_POWER` | 0.60 | Recovery power between intervals (60% FTP) |

## Project structure

```
generate.py   — Entry point: reads CSV, deduplicates, writes .zwo files
parser.py     — Regex-based free-text session parser
builder.py    — Converts parsed workouts to ZWO XML
models.py     — Data models and configuration constants
```
