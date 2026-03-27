from dataclasses import dataclass, field

# Constants
FTP = 200  # watts
WARMUP_DURATION = 600  # 10 minutes
WARMUP_LOW = 0.50  # 100W
WARMUP_HIGH = 0.70  # 140W
COOLDOWN_DURATION = 180  # 3 minutes
COOLDOWN_LOW = 0.50  # 100W
COOLDOWN_HIGH = 0.40  # 80W
OVER_UNDER_BLOCK = 120  # 2 minutes per over/under block
OVER_UNDER_THRESHOLD = 300  # 5 minutes - blocks longer than this become over-unders
OVER_UNDER_PLUS = 0.05  # +5% for over
OVER_UNDER_MINUS = 0.05  # -5% for under
REST_POWER = 0.60  # 60% FTP for interval recovery
Z2_POWER = 0.65  # default Z2 midpoint (130W)
RECOVERY_POWER = 0.50  # 100W for easy/recovery rides
INTERVAL_PLACEMENT_OFFSET = 600  # 10 min Z2 after warmup (20 min into session including warmup)


@dataclass
class Segment:
    """A single block within a workout."""
    kind: str  # "steady", "intervals", "ramp"
    duration_sec: int = 0
    power: float = 0.0  # FTP fraction
    power_high: float | None = None  # for ramps
    repeat: int | None = None  # for intervals
    on_duration: int | None = None
    off_duration: int | None = None
    on_power: float | None = None
    off_power: float | None = None


@dataclass
class Workout:
    """A parsed workout ready for ZWO generation."""
    name: str
    description: str
    segments: list[Segment] = field(default_factory=list)
    source_text: str = ""
