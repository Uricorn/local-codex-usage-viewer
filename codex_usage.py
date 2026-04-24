#!/usr/bin/env python3
"""Scan local Codex session logs and render an offline usage dashboard.

Parsing and pricing heuristics are inspired by CodexBar's local usage scanner:
https://github.com/steipete/CodexBar
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable


VERSION = "0.1.2"
COMMANDS = ("dashboard", "daily", "weekly", "monthly", "sessions")

COMMAND_HELP = {
    "dashboard": {
        "summary": "Render the full terminal dashboard with cards, daily summary, model breakdown, top sessions, and experimental limit progress.",
        "examples": [
            "{prog}",
            "{prog} dashboard --days 7",
            "{prog} dashboard --watch 5",
            "{prog} dashboard --censored",
        ],
    },
    "daily": {
        "summary": "Render a focused day-by-day usage report with input, cached input, output, total tokens, cached ratio, and optional cost.",
        "examples": [
            "{prog} daily --days 7",
            "{prog} daily --since 2026-03-01 --until 2026-03-22",
            "{prog} daily --json",
        ],
    },
    "weekly": {
        "summary": "Render a focused week-by-week usage report using local Monday-based week buckets.",
        "examples": [
            "{prog} weekly --days 90",
            "{prog} weekly --all",
            "{prog} weekly --json",
        ],
    },
    "monthly": {
        "summary": "Render a focused month-by-month usage report across the selected local history window.",
        "examples": [
            "{prog} monthly --all",
            "{prog} monthly --json",
            "{prog} monthly --root /path/to/codex-home",
        ],
    },
    "sessions": {
        "summary": "Render a focused top-sessions report with per-session token totals and optional thread titles.",
        "examples": [
            "{prog} sessions --days 7",
            "{prog} sessions --censored",
            "{prog} sessions --json --limit 20",
        ],
    },
}

PRICING = {
    "gpt-5": (1.25e-6, 1e-5, 1.25e-7),
    "gpt-5-codex": (1.25e-6, 1e-5, 1.25e-7),
    "gpt-5-mini": (2.5e-7, 2e-6, 2.5e-8),
    "gpt-5-nano": (5e-8, 4e-7, 5e-9),
    "gpt-5-pro": (1.5e-5, 1.2e-4, None),
    "gpt-5.1": (1.25e-6, 1e-5, 1.25e-7),
    "gpt-5.1-codex": (1.25e-6, 1e-5, 1.25e-7),
    "gpt-5.1-codex-max": (1.25e-6, 1e-5, 1.25e-7),
    "gpt-5.1-codex-mini": (2.5e-7, 2e-6, 2.5e-8),
    "gpt-5.2": (1.75e-6, 1.4e-5, 1.75e-7),
    "gpt-5.2-codex": (1.75e-6, 1.4e-5, 1.75e-7),
    "gpt-5.2-pro": (2.1e-5, 1.68e-4, None),
    "gpt-5.3-chat-latest": (1.75e-6, 1.4e-5, 1.75e-7),
    "gpt-5.3-codex": (1.75e-6, 1.4e-5, 1.75e-7),
    "gpt-5.3-codex-spark": (0.0, 0.0, 0.0),
    "gpt-5.3-codex-spark-preview": (0.0, 0.0, 0.0),
    "gpt-5.4": (2.5e-6, 1.5e-5, 2.5e-7),
    "gpt-5.4-mini": (7.5e-7, 4.5e-6, 7.5e-8),
    "gpt-5.4-nano": (2e-7, 1.25e-6, 2e-8),
    "gpt-5.4-pro": (3e-5, 1.8e-4, None),
    "gpt-5.5": (5e-6, 3e-5, 5e-7),
    "gpt-5.5-pro": (3e-5, 1.8e-4, None),
}

# Energy heuristic rates are intentionally rough. They provide a consistent
# relative signal from local token counts rather than a wall-power measurement.
BASE_ENERGY_RATES_WH = (2.5e-4, 7.5e-4, 2.5e-5)
MODEL_ENERGY_MULTIPLIER = {
    "gpt-5": 1.0,
    "gpt-5-codex": 1.0,
    "gpt-5-mini": 0.35,
    "gpt-5-nano": 0.12,
    "gpt-5-pro": 1.6,
    "gpt-5.1": 1.0,
    "gpt-5.1-codex": 1.0,
    "gpt-5.1-codex-max": 1.1,
    "gpt-5.1-codex-mini": 0.35,
    "gpt-5.2": 1.05,
    "gpt-5.2-codex": 1.05,
    "gpt-5.2-pro": 1.65,
    "gpt-5.3-chat-latest": 1.05,
    "gpt-5.3-codex": 1.05,
    "gpt-5.3-codex-spark": 0.2,
    "gpt-5.3-codex-spark-preview": 0.2,
    "gpt-5.4": 1.1,
    "gpt-5.4-mini": 0.4,
    "gpt-5.4-nano": 0.15,
    "gpt-5.4-pro": 1.75,
    "gpt-5.5": 1.15,
    "gpt-5.5-pro": 1.8,
}
DEFAULT_GRID_INTENSITY_G_CO2E_PER_KWH = 400.0
TREE_ABSORPTION_G_CO2E_PER_YEAR = 22_000.0

MODEL_DATE_SUFFIX = re.compile(r"-\d{4}-\d{2}-\d{2}$")
MODEL_REASONING_SUFFIX = re.compile(r"-(low|medium|high|xhigh)$")
GPT_VERSION_RE = re.compile(r"^gpt-(\d+(?:\.\d+)?)(.*)$")
SESSION_ID_RE = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.IGNORECASE,
)
FILENAME_DAY_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


@dataclass
class UsageEvent:
    session_id: str
    session_title: str | None
    day: str
    timestamp: str
    model: str
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    plan_type: str | None
    estimated_energy_wh: float
    estimated_cost_usd: float | None
    estimated_cost_is_guess: bool = False

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class Aggregate:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    events: int = 0
    estimated_energy_wh: float = 0.0
    estimated_cost_usd: float = 0.0
    has_cost: bool = False
    has_guessed_cost: bool = False

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def cached_ratio(self) -> float:
        if self.input_tokens <= 0:
            return 0.0
        return self.cached_input_tokens / self.input_tokens

    @property
    def estimated_emissions_g_co2e(self) -> float:
        return (self.estimated_energy_wh / 1000.0) * DEFAULT_GRID_INTENSITY_G_CO2E_PER_KWH

    @property
    def tree_offset_hours(self) -> float:
        tree_absorption_per_hour = TREE_ABSORPTION_G_CO2E_PER_YEAR / (365.0 * 24.0)
        if tree_absorption_per_hour <= 0:
            return 0.0
        return self.estimated_emissions_g_co2e / tree_absorption_per_hour

    def add(self, event: UsageEvent) -> None:
        self.input_tokens += event.input_tokens
        self.cached_input_tokens += event.cached_input_tokens
        self.output_tokens += event.output_tokens
        self.events += 1
        self.estimated_energy_wh += event.estimated_energy_wh
        if event.estimated_cost_usd is not None:
            self.estimated_cost_usd += event.estimated_cost_usd
            self.has_cost = True
            if event.estimated_cost_is_guess:
                self.has_guessed_cost = True


@dataclass
class SessionAggregate(Aggregate):
    session_id: str = ""
    title: str | None = None
    first_day: str | None = None
    last_day: str | None = None
    first_seen: str | None = None
    last_seen: str | None = None
    models: dict[str, int] = field(default_factory=dict)
    plan_types: dict[str, int] = field(default_factory=dict)

    def add(self, event: UsageEvent) -> None:
        super().add(event)
        self.first_day = min(filter(None, [self.first_day, event.day]), default=event.day)
        self.last_day = max(filter(None, [self.last_day, event.day]), default=event.day)
        self.first_seen = earlier_timestamp(self.first_seen, event.timestamp)
        self.last_seen = later_timestamp(self.last_seen, event.timestamp)
        self.models[event.model] = self.models.get(event.model, 0) + event.total_tokens
        if event.plan_type:
            self.plan_types[event.plan_type] = self.plan_types.get(event.plan_type, 0) + 1

    @property
    def top_model(self) -> str | None:
        if not self.models:
            return None
        return max(self.models.items(), key=lambda item: item[1])[0]


@dataclass
class ScanDiagnostics:
    discovered_files: int = 0
    scanned_files: int = 0
    duplicate_session_files: int = 0
    parsed_events: int = 0
    invalid_lines: int = 0
    empty_sessions: int = 0


@dataclass
class LimitWindow:
    used_percent: float | None = None
    window_minutes: int | None = None
    reset_after_seconds: int | None = None
    reset_at: str | None = None


@dataclass
class LimitBucket:
    allowed: bool | None = None
    limit_reached: bool | None = None
    primary: LimitWindow | None = None
    secondary: LimitWindow | None = None


@dataclass
class LimitSnapshot:
    captured_at: str | None = None
    plan_type: str | None = None
    credits_has_credits: bool | None = None
    credits_unlimited: bool | None = None
    credits_balance: float | None = None
    standard: LimitBucket | None = None
    code_review: LimitBucket | None = None
    additional: dict[str, LimitBucket] = field(default_factory=dict)


@dataclass
class UsageReport:
    root: str
    generated_at: str
    since: str | None
    until: str | None
    summary: Aggregate
    daily: dict[str, Aggregate]
    daily_session_counts: dict[str, int]
    weekly: dict[str, Aggregate]
    weekly_session_counts: dict[str, int]
    monthly: dict[str, Aggregate]
    monthly_session_counts: dict[str, int]
    models: dict[str, Aggregate]
    sessions: dict[str, SessionAggregate]
    plan_types: list[str]
    limits: LimitSnapshot | None
    diagnostics: ScanDiagnostics


class TerminalUI:
    MAX_CARD_COLUMNS = 4
    COLORS = {
        "reset": "\033[0m",
        "bold": "\033[1m",
        "dim": "\033[2m",
        "cyan": "\033[36m",
        "green": "\033[32m",
        "yellow": "\033[33m",
        "blue": "\033[34m",
        "magenta": "\033[35m",
        "red": "\033[31m",
        "gray": "\033[90m",
    }
    SPINNER = ["|", "/", "-", "\\"]

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self.last_progress_width = 0
        self.spinner_index = 0

    def style(self, text: str, *names: str) -> str:
        if not self.enabled or not names:
            return text
        prefix = "".join(self.COLORS[name] for name in names)
        return f"{prefix}{text}{self.COLORS['reset']}"

    def clear(self) -> None:
        if self.enabled:
            sys.stdout.write("\033[2J\033[H")
            sys.stdout.flush()

    def update_progress(self, current: int, total: int, path: Path) -> None:
        if not self.enabled:
            return
        spinner = self.SPINNER[self.spinner_index % len(self.SPINNER)]
        self.spinner_index += 1
        width = shutil.get_terminal_size((100, 24)).columns
        path_text = shorten_middle(str(path), max(20, width - 28))
        message = f"{spinner} Scanning {current}/{max(total, 1)}  {path_text}"
        padded = message.ljust(max(self.last_progress_width, len(message)))
        sys.stderr.write("\r" + self.style(padded, "cyan"))
        sys.stderr.flush()
        self.last_progress_width = len(padded)

    def finish_progress(self) -> None:
        if not self.enabled or self.last_progress_width == 0:
            return
        sys.stderr.write("\r" + (" " * self.last_progress_width) + "\r")
        sys.stderr.flush()
        self.last_progress_width = 0

    def panel(self, title: str, lines: list[str], color: str = "blue") -> str:
        content_widths = [len(strip_ansi(title))]
        content_widths.extend(len(strip_ansi(line)) for line in lines)
        width = max(content_widths, default=0) + 2
        top = f"┌{'─' * (width + 2)}┐"
        bottom = f"└{'─' * (width + 2)}┘"
        title_line = f"│ {pad_visible(self.style(title, 'bold', color), width)} │"
        body = [f"│ {pad_visible(line, width)} │" for line in lines]
        return "\n".join([self.style(top, color), title_line, *body, self.style(bottom, color)])

    def cards(self, cards: list[tuple[str, str, str, str]]) -> str:
        if not cards:
            return ""
        term_width = shutil.get_terminal_size((100, 24)).columns
        card_width = 24
        columns = max(1, min(len(cards), self.MAX_CARD_COLUMNS, term_width // (card_width + 2)))
        rendered = [self._card(*card, width=card_width) for card in cards]
        rows = []
        for start in range(0, len(rendered), columns):
            batch = rendered[start : start + columns]
            split = [item.splitlines() for item in batch]
            max_lines = max(len(block) for block in split)
            for block in split:
                while len(block) < max_lines:
                    block.append(" " * len(strip_ansi(block[0])))
            for idx in range(max_lines):
                rows.append("  ".join(block[idx] for block in split))
        return "\n".join(rows)

    def _card(self, title: str, value: str, subtitle: str, color: str, width: int) -> str:
        inner = width - 2
        top = self.style(f"┌{'─' * width}┐", color)
        bottom = self.style(f"└{'─' * width}┘", color)
        lines = [
            f"│ {pad_visible(self.style(title, 'bold', color), inner)} │",
            f"│ {pad_visible(self.style(value, 'bold'), inner)} │",
            f"│ {pad_visible(self.style(subtitle, 'dim'), inner)} │",
        ]
        return "\n".join([top, *lines, bottom])


def default_codex_home() -> Path:
    override = os.environ.get("CODEX_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".codex"


def as_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    return 0


def as_float(value: object) -> float | None:
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    return None


def as_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Local Codex Usage Viewer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=COMMANDS,
        default="dashboard",
        help="Report type to render. Defaults to dashboard.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=default_codex_home(),
        help="Codex home directory. Defaults to $CODEX_HOME or ~/.codex.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Rolling day window to include. Ignored with --all or --since.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Include all locally available history.",
    )
    parser.add_argument(
        "--since",
        type=str,
        help="Inclusive start date in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--until",
        type=str,
        help="Inclusive end date in YYYY-MM-DD format. Defaults to today.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Rows to show in model and session sections.",
    )
    parser.add_argument(
        "--daily-limit",
        type=int,
        default=14,
        help="Rows to show in the daily section.",
    )
    parser.add_argument(
        "--watch",
        type=float,
        default=0.0,
        help="Refresh every N seconds.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of the terminal dashboard.",
    )
    parser.add_argument(
        "--no-cost",
        action="store_true",
        help="Skip estimated-cost calculation and hide cost fields.",
    )
    parser.add_argument(
        "--plain",
        action="store_true",
        help="Disable ANSI colors.",
    )
    parser.add_argument(
        "--censored",
        action="store_true",
        help="Hide thread titles in dashboard and JSON output.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {VERSION}",
    )
    parser.epilog = build_general_help_epilog(parser.prog)
    return parser


def parse_args(parser: argparse.ArgumentParser, argv: list[str] | None = None) -> argparse.Namespace:
    return parser.parse_args(argv)


def build_general_help_epilog(prog: str) -> str:
    lines = [
        "Commands:",
        "  dashboard   Full dashboard view (default)",
        "  daily       Day-by-day usage report",
        "  weekly      Week-by-week usage report",
        "  monthly     Month-by-month usage report",
        "  sessions    Top sessions report",
        "",
        "Examples:",
        f"  {prog}",
        f"  {prog} daily --days 7",
        f"  {prog} weekly --days 90",
        f"  {prog} monthly --all",
        f"  {prog} sessions --days 7 --censored",
        f"  {prog} help daily",
    ]
    return "\n".join(lines)


def print_command_help(parser: argparse.ArgumentParser, topic: str | None) -> None:
    if topic is None:
        parser.print_help()
        return

    command = COMMAND_HELP[topic]
    prog = parser.prog
    examples = "\n".join(f"  {example.format(prog=prog)}" for example in command["examples"])
    text = "\n".join(
        [
            f"{prog} {topic}",
            "",
            command["summary"],
            "",
            "Examples:",
            examples,
            "",
            "Common options:",
            "  --days N                   Rolling day window",
            "  --since YYYY-MM-DD         Inclusive start date",
            "  --until YYYY-MM-DD         Inclusive end date",
            "  --all                      Include all local history",
            "  --json                     Emit machine-readable JSON",
            "  --censored                 Hide thread titles and local path",
            "  --no-cost                  Hide heuristic cost estimates",
            "  --root /path/to/codex-home Scan a different Codex home",
        ]
    )
    print(text)


def maybe_handle_help_command(parser: argparse.ArgumentParser, argv: list[str]) -> bool:
    if not argv:
        return False

    if argv[0] == "help":
        if len(argv) > 2:
            parser.error("help accepts at most one topic")
        topic = argv[1] if len(argv) == 2 else None
        if topic is not None and topic not in COMMANDS:
            parser.error(f"unknown help topic: {topic}")
        print_command_help(parser, topic)
        return True

    if argv[0] in COMMANDS and any(arg in ("-h", "--help") for arg in argv[1:]):
        print_command_help(parser, argv[0])
        return True

    return False


def parse_day(text: str) -> date:
    return date.fromisoformat(text)


def choose_window(args: argparse.Namespace) -> tuple[date | None, date | None]:
    if args.all:
        return None, None
    today = datetime.now().astimezone().date()
    until = parse_day(args.until) if args.until else today
    since = parse_day(args.since) if args.since else until - timedelta(days=max(args.days - 1, 0))
    return since, until


def list_session_files(root: Path, since: date | None, until: date | None) -> list[Path]:
    sessions_root = root / "sessions"
    archived_root = root / "archived_sessions"
    files: list[Path] = []
    yielded: set[Path] = set()

    for path in iter_partitioned_files(sessions_root, since, until):
        files.append(path)
        yielded.add(path)

    if archived_root.is_dir():
        for path in sorted(archived_root.glob("*.jsonl")):
            if path in yielded:
                continue
            day = day_from_filename(path.name)
            if day is not None and since is not None and until is not None and (day < since or day > until):
                continue
            files.append(path)

    return files


def iter_partitioned_files(root: Path, since: date | None, until: date | None) -> Iterable[Path]:
    if not root.is_dir():
        return
    if since is None or until is None:
        yield from sorted(root.rglob("*.jsonl"))
        return

    current = since
    while current <= until:
        day_dir = root / f"{current.year:04d}" / f"{current.month:02d}" / f"{current.day:02d}"
        if day_dir.is_dir():
            yield from sorted(day_dir.glob("*.jsonl"))
        current += timedelta(days=1)


def day_from_filename(filename: str) -> date | None:
    match = FILENAME_DAY_RE.search(filename)
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def load_session_index(root: Path) -> dict[str, dict]:
    session_index = root / "session_index.jsonl"
    mapping: dict[str, dict] = {}
    if not session_index.is_file():
        return mapping
    with session_index.open() as handle:
        for line in handle:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            session_id = item.get("id")
            if session_id:
                mapping[session_id] = item
    return mapping


def load_limit_snapshot(root: Path) -> LimitSnapshot | None:
    db_path = root / "logs_1.sqlite"
    if not db_path.is_file():
        return None

    query = """
        SELECT ts, feedback_log_body
        FROM logs
        WHERE target = 'codex_api::endpoint::responses_websocket'
          AND feedback_log_body LIKE '%websocket event: {"type":"codex.rate_limits"%'
        ORDER BY ts DESC, ts_nanos DESC, id DESC
        LIMIT 25
    """
    prefix = "websocket event: "

    try:
        connection = sqlite3.connect(str(db_path))
        try:
            rows = connection.execute(query).fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return None

    for ts, body in rows:
        if not body or prefix not in body:
            continue
        raw_payload = body.split(prefix, 1)[1].strip()
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            continue
        if payload.get("type") != "codex.rate_limits":
            continue

        credits = payload.get("credits") if isinstance(payload.get("credits"), dict) else {}
        additional_payload = (
            payload.get("additional_rate_limits")
            if isinstance(payload.get("additional_rate_limits"), dict)
            else {}
        )
        additional: dict[str, LimitBucket] = {}
        for name, item in additional_payload.items():
            bucket = parse_limit_bucket(item)
            if bucket is not None:
                additional[name] = bucket

        snapshot = LimitSnapshot(
            captured_at=epoch_to_local_timestamp(ts),
            plan_type=payload.get("plan_type") if isinstance(payload.get("plan_type"), str) else None,
            credits_has_credits=as_bool(credits.get("has_credits")),
            credits_unlimited=as_bool(credits.get("unlimited")),
            credits_balance=as_float(credits.get("balance")),
            standard=parse_limit_bucket(payload.get("rate_limits")),
            code_review=parse_limit_bucket(payload.get("code_review_rate_limits")),
            additional=additional,
        )
        if has_limit_snapshot_data(snapshot):
            return snapshot

    return None


def has_limit_snapshot_data(snapshot: LimitSnapshot) -> bool:
    return any(
        [
            snapshot.plan_type,
            snapshot.standard,
            snapshot.code_review,
            snapshot.additional,
            snapshot.credits_has_credits is not None,
            snapshot.credits_unlimited is not None,
            snapshot.credits_balance is not None,
        ]
    )


def parse_limit_bucket(payload: object) -> LimitBucket | None:
    if not isinstance(payload, dict):
        return None
    bucket = LimitBucket(
        allowed=as_bool(payload.get("allowed")),
        limit_reached=as_bool(payload.get("limit_reached")),
        primary=parse_limit_window(payload.get("primary")),
        secondary=parse_limit_window(payload.get("secondary")),
    )
    if any([bucket.allowed is not None, bucket.limit_reached is not None, bucket.primary, bucket.secondary]):
        return bucket
    return None


def parse_limit_window(payload: object) -> LimitWindow | None:
    if not isinstance(payload, dict):
        return None
    window = LimitWindow(
        used_percent=as_float(payload.get("used_percent")),
        window_minutes=as_int(payload.get("window_minutes")) or None,
        reset_after_seconds=as_int(payload.get("reset_after_seconds")) or None,
        reset_at=epoch_to_local_timestamp(payload.get("reset_at")),
    )
    if any(
        [
            window.used_percent is not None,
            window.window_minutes is not None,
            window.reset_after_seconds is not None,
            window.reset_at is not None,
        ]
    ):
        return window
    return None


def strip_model_suffixes(model: str) -> str:
    stripped = MODEL_DATE_SUFFIX.sub("", model)
    return MODEL_REASONING_SUFFIX.sub("", stripped)


def pricing_variant(model: str) -> str:
    match = GPT_VERSION_RE.match(model)
    suffix = match.group(2) if match else model
    if "spark" in suffix:
        return "spark"
    if suffix.endswith("-pro"):
        return "pro"
    if suffix.endswith("-nano"):
        return "nano"
    if suffix.endswith("-mini"):
        return "mini"
    if "-codex" in suffix:
        return "codex"
    return "base"


def pricing_version_key(model: str) -> tuple[int, ...]:
    match = GPT_VERSION_RE.match(model)
    if not match:
        return ()
    return tuple(int(part) for part in match.group(1).split("."))


def pricing_major_version(model: str) -> int | None:
    key = pricing_version_key(model)
    return key[0] if key else None


def guess_pricing(model: str) -> tuple[float, float, float | None] | None:
    match = GPT_VERSION_RE.match(model)
    if not match:
        return None
    if match.group(2) and not match.group(2).startswith("-"):
        return None

    variant = pricing_variant(model)
    major_version = pricing_major_version(model)
    base_model = re.sub(r"-(codex|max|mini|nano|pro|spark|preview).*$", "", model)
    if variant == "codex" and base_model in PRICING:
        return PRICING[base_model]

    if variant == "spark":
        return PRICING.get("gpt-5.3-codex-spark-preview")

    candidate_variants = {"base"} if variant == "codex" else {variant}
    candidates = [
        (key, pricing)
        for key, pricing in PRICING.items()
        if pricing_major_version(key) == major_version
        and pricing_variant(key) in candidate_variants
        and not all(rate == 0.0 for rate in pricing if rate is not None)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: pricing_version_key(item[0]))[1]


def normalize_model(raw: str | None) -> str:
    if not raw:
        return "unknown"
    model = raw.strip()
    if model.startswith("openai/"):
        model = model.removeprefix("openai/")
    if model in PRICING:
        return model
    stripped = strip_model_suffixes(model)
    if stripped in PRICING or guess_pricing(stripped) is not None:
        return stripped
    return model


def resolve_pricing(model: str) -> tuple[tuple[float, float, float | None], bool] | None:
    normalized = normalize_model(model)
    pricing = PRICING.get(normalized)
    if pricing is not None:
        return pricing, False
    guessed = guess_pricing(strip_model_suffixes(normalized))
    if guessed is None:
        return None
    return guessed, True


def estimate_cost_details(
    model: str,
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
) -> tuple[float, bool] | None:
    resolved = resolve_pricing(model)
    if resolved is None:
        return None
    pricing, is_guess = resolved
    input_rate, output_rate, cached_rate = pricing
    cached = max(0, min(cached_input_tokens, input_tokens))
    non_cached = max(0, input_tokens - cached)
    cache_read_rate = cached_rate if cached_rate is not None else input_rate
    cost = non_cached * input_rate + cached * cache_read_rate + output_tokens * output_rate
    return cost, is_guess


def estimate_cost(model: str, input_tokens: int, cached_input_tokens: int, output_tokens: int) -> float | None:
    estimated = estimate_cost_details(model, input_tokens, cached_input_tokens, output_tokens)
    return None if estimated is None else estimated[0]


def estimate_energy(model: str, input_tokens: int, cached_input_tokens: int, output_tokens: int) -> float:
    multiplier = MODEL_ENERGY_MULTIPLIER.get(normalize_model(model), 1.0)
    input_rate, output_rate, cached_rate = (rate * multiplier for rate in BASE_ENERGY_RATES_WH)
    cached = max(0, min(cached_input_tokens, input_tokens))
    non_cached = max(0, input_tokens - cached)
    return (non_cached * input_rate) + (cached * cached_rate) + (output_tokens * output_rate)


def parse_local_day(timestamp: str) -> str | None:
    parsed = parse_local_timestamp(timestamp)
    return parsed.date().isoformat() if parsed else None


def epoch_to_local_timestamp(timestamp: object) -> str | None:
    if isinstance(timestamp, bool):
        return None
    if not isinstance(timestamp, (int, float)):
        return None
    parsed = datetime.fromtimestamp(int(timestamp), tz=timezone.utc).astimezone()
    return parsed.isoformat(timespec="seconds")


def parse_local_timestamp(timestamp: str) -> datetime | None:
    if not timestamp:
        return None
    normalized = timestamp.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone()


def normalize_local_timestamp(timestamp: str) -> str | None:
    parsed = parse_local_timestamp(timestamp)
    if parsed is None:
        return None
    return parsed.isoformat(timespec="seconds")


def fallback_session_id(path: Path) -> str:
    match = SESSION_ID_RE.search(path.name)
    return match.group(1) if match else path.stem


def parse_session_file(
    path: Path,
    session_index: dict[str, dict],
    with_cost: bool,
) -> tuple[str, str | None, list[UsageEvent], int]:
    current_model: str | None = None
    previous_totals: tuple[int, int, int] | None = None
    session_id: str | None = None
    last_plan_type: str | None = None
    events: list[UsageEvent] = []
    invalid_lines = 0

    with path.open() as handle:
        for line in handle:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                invalid_lines += 1
                continue

            item_type = item.get("type")
            payload = item.get("payload") or {}

            if item_type == "session_meta":
                session_id = (
                    payload.get("session_id")
                    or payload.get("sessionId")
                    or payload.get("id")
                    or item.get("session_id")
                    or item.get("sessionId")
                    or item.get("id")
                )
                continue

            if item_type == "turn_context":
                current_model = payload.get("model") or (payload.get("info") or {}).get("model") or current_model
                continue

            if item_type != "event_msg" or payload.get("type") != "token_count":
                continue

            info = payload.get("info") or {}
            total_usage = info.get("total_token_usage") or {}
            last_usage = info.get("last_token_usage") or {}
            if not total_usage and not last_usage:
                continue

            normalized_timestamp = normalize_local_timestamp(item.get("timestamp", ""))
            if normalized_timestamp is None:
                continue
            day = parse_local_day(normalized_timestamp)
            if day is None:
                continue

            model = (
                info.get("model")
                or info.get("model_name")
                or payload.get("model")
                or item.get("model")
                or current_model
                or "gpt-5"
            )
            plan_type = (payload.get("rate_limits") or {}).get("plan_type") or last_plan_type
            if plan_type:
                last_plan_type = plan_type

            input_delta, cached_delta, output_delta, previous_totals = token_delta(
                total_usage=total_usage,
                last_usage=last_usage,
                previous_totals=previous_totals,
            )
            if input_delta == 0 and cached_delta == 0 and output_delta == 0:
                continue

            effective_session_id = session_id or fallback_session_id(path)
            session_title = (session_index.get(effective_session_id) or {}).get("thread_name")
            normalized_model = normalize_model(model)
            estimated_energy = estimate_energy(normalized_model, input_delta, cached_delta, output_delta)
            cost_details = None if not with_cost else estimate_cost_details(normalized_model, input_delta, cached_delta, output_delta)
            estimated = None if cost_details is None else cost_details[0]
            events.append(
                UsageEvent(
                    session_id=effective_session_id,
                    session_title=session_title,
                    day=day,
                    timestamp=normalized_timestamp,
                    model=normalized_model,
                    input_tokens=input_delta,
                    cached_input_tokens=cached_delta,
                    output_tokens=output_delta,
                    plan_type=plan_type,
                    estimated_energy_wh=estimated_energy,
                    estimated_cost_usd=estimated,
                    estimated_cost_is_guess=False if cost_details is None else cost_details[1],
                )
            )

    effective_session_id = session_id or fallback_session_id(path)
    session_title = (session_index.get(effective_session_id) or {}).get("thread_name")
    return effective_session_id, session_title, events, invalid_lines


def token_delta(
    *,
    total_usage: dict,
    last_usage: dict,
    previous_totals: tuple[int, int, int] | None,
) -> tuple[int, int, int, tuple[int, int, int] | None]:
    if total_usage:
        input_total = as_int(total_usage.get("input_tokens"))
        cached_total = as_int(total_usage.get("cached_input_tokens", total_usage.get("cache_read_input_tokens")))
        output_total = as_int(total_usage.get("output_tokens"))
        if previous_totals is None:
            input_delta, cached_delta, output_delta = input_total, cached_total, output_total
        else:
            input_delta = max(0, input_total - previous_totals[0])
            cached_delta = max(0, cached_total - previous_totals[1])
            output_delta = max(0, output_total - previous_totals[2])
        return input_delta, min(cached_delta, input_delta), output_delta, (input_total, cached_total, output_total)

    input_delta = max(0, as_int(last_usage.get("input_tokens")))
    cached_delta = max(0, as_int(last_usage.get("cached_input_tokens", last_usage.get("cache_read_input_tokens"))))
    output_delta = max(0, as_int(last_usage.get("output_tokens")))
    return input_delta, min(cached_delta, input_delta), output_delta, previous_totals


def collect_events(
    root: Path,
    since: date | None,
    until: date | None,
    with_cost: bool,
    progress: Callable[[int, int, Path], None] | None = None,
) -> tuple[list[UsageEvent], ScanDiagnostics]:
    session_index = load_session_index(root)
    seen_session_ids: set[str] = set()
    diagnostics = ScanDiagnostics()
    files = list_session_files(root, since, until)
    diagnostics.discovered_files = len(files)
    collected: list[UsageEvent] = []

    for index, path in enumerate(files, start=1):
        if progress:
            progress(index, diagnostics.discovered_files, path)
        session_id, _, events, invalid_lines = parse_session_file(path, session_index, with_cost=with_cost)
        diagnostics.scanned_files += 1
        diagnostics.invalid_lines += invalid_lines

        if session_id in seen_session_ids:
            diagnostics.duplicate_session_files += 1
            continue
        seen_session_ids.add(session_id)

        if not events:
            diagnostics.empty_sessions += 1
            continue

        for event in events:
            if since is not None and event.day < since.isoformat():
                continue
            if until is not None and event.day > until.isoformat():
                continue
            collected.append(event)

    diagnostics.parsed_events = len(collected)
    return collected, diagnostics


def aggregate(events: list[UsageEvent]) -> tuple[Aggregate, dict[str, Aggregate], dict[str, Aggregate], dict[str, SessionAggregate]]:
    summary = Aggregate()
    daily: dict[str, Aggregate] = defaultdict(Aggregate)
    models: dict[str, Aggregate] = defaultdict(Aggregate)
    sessions: dict[str, SessionAggregate] = {}

    for event in events:
        summary.add(event)
        daily[event.day].add(event)
        models[event.model].add(event)
        if event.session_id not in sessions:
            sessions[event.session_id] = SessionAggregate(session_id=event.session_id, title=event.session_title)
        sessions[event.session_id].add(event)

    return summary, daily, models, sessions


def month_from_day(day_text: str) -> str:
    return day_text[:7]


def week_start_from_day(day_text: str) -> str:
    parsed = date.fromisoformat(day_text)
    return (parsed - timedelta(days=parsed.weekday())).isoformat()


def build_report(
    root: Path,
    since: date | None,
    until: date | None,
    events: list[UsageEvent],
    diagnostics: ScanDiagnostics,
) -> UsageReport:
    summary, daily, models, sessions = aggregate(events)
    plan_types = sorted({event.plan_type for event in events if event.plan_type})
    daily_session_sets: dict[str, set[str]] = defaultdict(set)
    weekly: dict[str, Aggregate] = defaultdict(Aggregate)
    weekly_session_sets: dict[str, set[str]] = defaultdict(set)
    monthly: dict[str, Aggregate] = defaultdict(Aggregate)
    monthly_session_sets: dict[str, set[str]] = defaultdict(set)
    for event in events:
        daily_session_sets[event.day].add(event.session_id)
        week_key = week_start_from_day(event.day)
        weekly[week_key].add(event)
        weekly_session_sets[week_key].add(event.session_id)
        month_key = month_from_day(event.day)
        monthly[month_key].add(event)
        monthly_session_sets[month_key].add(event.session_id)
    limits = load_limit_snapshot(root)
    return UsageReport(
        root=str(root),
        generated_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        since=since.isoformat() if since else None,
        until=until.isoformat() if until else None,
        summary=summary,
        daily=daily,
        daily_session_counts={day: len(session_ids) for day, session_ids in daily_session_sets.items()},
        weekly=weekly,
        weekly_session_counts={key: len(session_ids) for key, session_ids in weekly_session_sets.items()},
        monthly=monthly,
        monthly_session_counts={key: len(session_ids) for key, session_ids in monthly_session_sets.items()},
        models=models,
        sessions=sessions,
        plan_types=plan_types,
        limits=limits,
        diagnostics=diagnostics,
    )


def format_int(value: int) -> str:
    return f"{value:,}"


def format_compact_int(value: int) -> str:
    absolute = abs(value)
    if absolute >= 1_000_000_000:
        return f"{value / 1_000_000_000:.1f}B"
    if absolute >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if absolute >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


def format_cost(value: float | None, *, guessed: bool = False) -> str:
    if value is None:
        return "-"
    formatted = f"${value:,.2f}"
    return f"~{formatted}" if guessed else formatted


def format_energy(value: float) -> str:
    if value >= 1000:
        return f"{value / 1000:.2f} kWh"
    if value >= 10:
        return f"{value:.1f} Wh"
    if value >= 1:
        return f"{value:.2f} Wh"
    return f"{value * 1000:.0f} mWh"


def format_energy_compact(value: float) -> str:
    if value >= 1000:
        return f"{value / 1000:.1f}kWh"
    if value >= 1:
        return f"{value:.0f}Wh"
    return f"{value * 1000:.0f}mWh"


def format_tree_offset(hours: float) -> str:
    if hours < (1 / 60):
        return f"{hours * 3600:.0f} tree-sec"
    if hours < 1:
        return f"{hours * 60:.0f} tree-min"
    if hours < 24:
        return f"{hours:.1f} tree-hours"
    if hours < 24 * 30:
        return f"{hours / 24:.1f} tree-days"
    return f"{hours / (24 * 30):.1f} tree-months"


def format_tree_offset_compact(hours: float) -> str:
    if hours < (1 / 60):
        return f"{hours * 3600:.0f}s"
    if hours < 1:
        return f"{hours * 60:.0f}m"
    if hours < 24:
        return f"{hours:.1f}h"
    if hours < 24 * 30:
        return f"{hours / 24:.1f}d"
    return f"{hours / (24 * 30):.1f}mo"


def format_percent(value: float) -> str:
    return f"{value * 100:.1f}%"


def shorten_middle(text: str, width: int) -> str:
    if width <= 0 or len(text) <= width:
        return text
    if width <= 5:
        return text[:width]
    keep = width - 1
    left = keep // 2
    right = keep - left
    return text[:left] + "…" + text[-right:]


def strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def pad_visible(text: str, width: int, *, right: bool = False) -> str:
    visible_width = len(strip_ansi(text))
    padding = max(width - visible_width, 0)
    return (" " * padding + text) if right else (text + " " * padding)


def earlier_timestamp(current: str | None, candidate: str | None) -> str | None:
    if candidate is None:
        return current
    if current is None:
        return candidate
    return candidate if parse_local_timestamp(candidate) < parse_local_timestamp(current) else current


def later_timestamp(current: str | None, candidate: str | None) -> str | None:
    if candidate is None:
        return current
    if current is None:
        return candidate
    return candidate if parse_local_timestamp(candidate) > parse_local_timestamp(current) else current


def format_pretty_datetime(timestamp: str | None) -> str:
    if not timestamp:
        return "-"
    parsed = parse_local_timestamp(timestamp)
    if parsed is None:
        return timestamp
    return parsed.strftime("%a %d %b %Y %H:%M")


def format_short_datetime(timestamp: str | None) -> str:
    if not timestamp:
        return "-"
    parsed = parse_local_timestamp(timestamp)
    if parsed is None:
        return timestamp
    return parsed.strftime("%d %b %H:%M")


def format_pretty_day(day_text: str) -> str:
    try:
        parsed = date.fromisoformat(day_text)
    except ValueError:
        return day_text
    return parsed.strftime("%a %d %b")


def format_pretty_month(month_text: str) -> str:
    try:
        parsed = date.fromisoformat(f"{month_text}-01")
    except ValueError:
        return month_text
    return parsed.strftime("%b %Y")


def format_pretty_week(week_text: str) -> str:
    try:
        parsed = date.fromisoformat(week_text)
    except ValueError:
        return week_text
    return f"Week of {parsed.strftime('%d %b %Y')}"


def format_window_minutes(value: int | None) -> str:
    if value is None:
        return "-"
    if value % (60 * 24) == 0:
        return f"{value // (60 * 24)}d"
    if value % 60 == 0:
        return f"{value // 60}h"
    return f"{value}m"


def format_relative_duration(seconds: int | None) -> str:
    if seconds is None:
        return "-"
    if seconds <= 0:
        return "now"
    days, remainder = divmod(seconds, 60 * 60 * 24)
    hours, remainder = divmod(remainder, 60 * 60)
    minutes = remainder // 60
    if days > 0:
        return f"in {days}d"
    if hours > 0:
        return f"in {hours}h"
    if minutes > 0:
        return f"in {minutes}m"
    return "in <1m"


def metric_bar(value: int, maximum: int, width: int = 14, unicode_ok: bool = True) -> str:
    if maximum <= 0:
        return "-" * width
    filled = max(0, min(width, round(width * (value / maximum))))
    full = "█" if unicode_ok else "#"
    empty = "░" if unicode_ok else "."
    return full * filled + empty * (width - filled)


def percent_bar(value: float | None, width: int = 14, unicode_ok: bool = True) -> str:
    if value is None:
        return "-" * width
    clamped = max(0.0, min(100.0, value))
    filled = max(0, min(width, round(width * (clamped / 100.0))))
    full = "█" if unicode_ok else "#"
    empty = "░" if unicode_ok else "."
    return full * filled + empty * (width - filled)


def render_table(headers: list[str], rows: list[list[str]], align_right: set[int] | None = None) -> str:
    if align_right is None:
        align_right = set()
    widths = [len(header) for header in headers]
    for row in rows:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], len(strip_ansi(cell)))

    def pad(cell: str, width: int, right: bool) -> str:
        text_width = len(strip_ansi(cell))
        padding = max(width - text_width, 0)
        return (" " * padding + cell) if right else (cell + " " * padding)

    def render_row(row: list[str]) -> str:
        pieces = []
        for idx, cell in enumerate(row):
            pieces.append(pad(cell, widths[idx], idx in align_right))
        return "  ".join(pieces)

    divider = render_row(["-" * width for width in widths])
    return "\n".join([render_row(headers), divider, *[render_row(row) for row in rows]])


def build_limit_panel(snapshot: LimitSnapshot, ui: TerminalUI, *, unicode_ok: bool) -> str | None:
    rows: list[list[str]] = []

    def append_rows(label: str, bucket: LimitBucket | None) -> None:
        if bucket is None:
            return
        status_suffix = ""
        if bucket.allowed is False:
            status_suffix = " (blocked)"
        elif bucket.limit_reached:
            status_suffix = " (reached)"
        for tier_name, window in (("Primary", bucket.primary), ("Secondary", bucket.secondary)):
            if window is None:
                continue
            rows.append(
                [
                    shorten_middle(f"{label}{status_suffix}", 34),
                    tier_name,
                    format_percent(window.used_percent / 100.0) if window.used_percent is not None else "-",
                    percent_bar(window.used_percent, unicode_ok=unicode_ok),
                    format_window_minutes(window.window_minutes),
                    format_relative_duration(window.reset_after_seconds),
                ]
            )

    append_rows("Standard", snapshot.standard)
    append_rows("Code Review", snapshot.code_review)
    for name, bucket in sorted(snapshot.additional.items()):
        append_rows(name, bucket)

    if not rows:
        return None

    status_bits: list[str] = []
    if snapshot.plan_type:
        status_bits.append(f"Plan: {snapshot.plan_type}")
    if snapshot.credits_has_credits is not None:
        status_bits.append(f"Credits: {'yes' if snapshot.credits_has_credits else 'no'}")
    if snapshot.credits_unlimited is not None:
        status_bits.append(f"Unlimited: {'yes' if snapshot.credits_unlimited else 'no'}")
    if snapshot.credits_balance is not None:
        status_bits.append(f"Balance: {snapshot.credits_balance:g}")

    lines: list[str] = []
    if snapshot.captured_at:
        lines.append(f"Snapshot: {format_pretty_datetime(snapshot.captured_at)}")
    if status_bits:
        lines.append(" • ".join(status_bits))
    limit_table = render_table(
        ["Scope", "Tier", "Used", "Progress", "Window", "Reset"],
        rows,
        align_right={2},
    )
    lines.extend(limit_table.splitlines())
    return ui.panel("Limit Progress (Experimental)", lines, color="red")


def sorted_sessions(report: UsageReport, *, with_cost: bool, limit: int | None = None) -> list[SessionAggregate]:
    rows = sorted(
        report.sessions.values(),
        key=lambda item: item.estimated_cost_usd if (with_cost and item.has_cost) else item.total_tokens,
        reverse=True,
    )
    return rows if limit is None else rows[:limit]


def build_daily_rows(report: UsageReport, *, with_cost: bool, limit: int, unicode_ok: bool) -> list[list[str]]:
    rows: list[list[str]] = []
    max_daily_total = max((aggregate.total_tokens for aggregate in report.daily.values()), default=0)
    for day_key, aggregate_row in sorted(report.daily.items(), reverse=True)[:limit]:
        row = [
            format_pretty_day(day_key),
            str(report.daily_session_counts.get(day_key, 0)),
            format_int(aggregate_row.input_tokens),
            format_int(aggregate_row.cached_input_tokens),
            format_int(aggregate_row.output_tokens),
            format_int(aggregate_row.total_tokens),
            format_energy_compact(aggregate_row.estimated_energy_wh),
            format_tree_offset_compact(aggregate_row.tree_offset_hours),
            format_percent(aggregate_row.cached_ratio),
            metric_bar(aggregate_row.total_tokens, max_daily_total, unicode_ok=unicode_ok),
        ]
        if with_cost:
            row.append(
                format_cost(
                    aggregate_row.estimated_cost_usd if aggregate_row.has_cost else None,
                    guessed=aggregate_row.has_guessed_cost,
                )
            )
        rows.append(row)
    return rows


def build_monthly_rows(report: UsageReport, *, with_cost: bool, limit: int, unicode_ok: bool) -> list[list[str]]:
    rows: list[list[str]] = []
    max_monthly_total = max((aggregate.total_tokens for aggregate in report.monthly.values()), default=0)
    for month_key, aggregate_row in sorted(report.monthly.items(), reverse=True)[:limit]:
        row = [
            format_pretty_month(month_key),
            str(report.monthly_session_counts.get(month_key, 0)),
            format_int(aggregate_row.input_tokens),
            format_int(aggregate_row.cached_input_tokens),
            format_int(aggregate_row.output_tokens),
            format_int(aggregate_row.total_tokens),
            format_energy_compact(aggregate_row.estimated_energy_wh),
            format_tree_offset_compact(aggregate_row.tree_offset_hours),
            format_percent(aggregate_row.cached_ratio),
            metric_bar(aggregate_row.total_tokens, max_monthly_total, unicode_ok=unicode_ok),
        ]
        if with_cost:
            row.append(
                format_cost(
                    aggregate_row.estimated_cost_usd if aggregate_row.has_cost else None,
                    guessed=aggregate_row.has_guessed_cost,
                )
            )
        rows.append(row)
    return rows


def build_weekly_rows(report: UsageReport, *, with_cost: bool, limit: int, unicode_ok: bool) -> list[list[str]]:
    rows: list[list[str]] = []
    max_weekly_total = max((aggregate.total_tokens for aggregate in report.weekly.values()), default=0)
    for week_key, aggregate_row in sorted(report.weekly.items(), reverse=True)[:limit]:
        row = [
            format_pretty_week(week_key),
            str(report.weekly_session_counts.get(week_key, 0)),
            format_int(aggregate_row.input_tokens),
            format_int(aggregate_row.cached_input_tokens),
            format_int(aggregate_row.output_tokens),
            format_int(aggregate_row.total_tokens),
            format_energy_compact(aggregate_row.estimated_energy_wh),
            format_tree_offset_compact(aggregate_row.tree_offset_hours),
            format_percent(aggregate_row.cached_ratio),
            metric_bar(aggregate_row.total_tokens, max_weekly_total, unicode_ok=unicode_ok),
        ]
        if with_cost:
            row.append(
                format_cost(
                    aggregate_row.estimated_cost_usd if aggregate_row.has_cost else None,
                    guessed=aggregate_row.has_guessed_cost,
                )
            )
        rows.append(row)
    return rows


def build_model_rows(report: UsageReport, *, with_cost: bool, limit: int, unicode_ok: bool) -> list[list[str]]:
    rows: list[list[str]] = []
    max_model_total = max((aggregate.total_tokens for aggregate in report.models.values()), default=0)
    selected = sorted(
        report.models.items(),
        key=lambda item: item[1].estimated_cost_usd if (with_cost and item[1].has_cost) else item[1].total_tokens,
        reverse=True,
    )[:limit]
    for model, aggregate_row in selected:
        row = [
            model,
            format_int(aggregate_row.total_tokens),
            format_percent(aggregate_row.cached_ratio),
            format_int(aggregate_row.output_tokens),
            metric_bar(aggregate_row.total_tokens, max_model_total, unicode_ok=unicode_ok),
        ]
        if with_cost:
            row.append(
                format_cost(
                    aggregate_row.estimated_cost_usd if aggregate_row.has_cost else None,
                    guessed=aggregate_row.has_guessed_cost,
                )
            )
        rows.append(row)
    return rows


def build_session_rows(
    report: UsageReport,
    *,
    with_cost: bool,
    limit: int,
    unicode_ok: bool,
    censored: bool,
) -> list[list[str]]:
    rows: list[list[str]] = []
    max_session_total = max((session.total_tokens for session in report.sessions.values()), default=0)
    for session in sorted_sessions(report, with_cost=with_cost, limit=limit):
        row = [
            format_short_datetime(session.last_seen or session.last_day),
            session.top_model or "-",
            format_int(session.input_tokens),
            format_int(session.cached_input_tokens),
            format_int(session.output_tokens),
            format_int(session.total_tokens),
            format_percent(session.cached_ratio),
            metric_bar(session.total_tokens, max_session_total, unicode_ok=unicode_ok),
        ]
        if with_cost:
            row.append(
                format_cost(
                    session.estimated_cost_usd if session.has_cost else None,
                    guessed=session.has_guessed_cost,
                )
            )
        if not censored:
            row.append(shorten_middle(session.title or session.session_id, 40))
        rows.append(row)
    return rows


def window_label(report: UsageReport) -> str:
    if report.since is None or report.until is None:
        return "All local history"
    if report.since == report.until:
        return format_pretty_day(report.since)
    return f"{format_pretty_day(report.since)} to {format_pretty_day(report.until)}"


def build_cards(report: UsageReport, with_cost: bool) -> list[tuple[str, str, str, str]]:
    cards = [
        ("Sessions", format_int(len(report.sessions)), "unique local sessions", "blue"),
        ("Active Days", format_int(len(report.daily)), "days with usage", "cyan"),
        ("Total Tokens", format_compact_int(report.summary.total_tokens), "input + output", "green"),
        ("Cached Ratio", format_percent(report.summary.cached_ratio), "cached / input", "yellow"),
        ("Est. Energy", format_energy(report.summary.estimated_energy_wh), "token heuristic", "red"),
        ("Tree Offset", format_tree_offset(report.summary.tree_offset_hours), "one tree absorbing CO2", "green"),
    ]
    if with_cost:
        cost_value = format_cost(
            report.summary.estimated_cost_usd if report.summary.has_cost else None,
            guessed=report.summary.has_guessed_cost,
        )
        subtitle = "includes guessed rates" if report.summary.has_guessed_cost else "heuristic only"
        cards.append(("Est. Cost", cost_value, subtitle, "magenta"))
    else:
        cards.append(("Output Tokens", format_compact_int(report.summary.output_tokens), "non-cached output", "magenta"))
    return cards


def build_header_panel(report: UsageReport, ui: TerminalUI, *, censored: bool) -> str:
    header_lines = [
        f"Window: {window_label(report)}",
        f"Generated: {format_pretty_datetime(report.generated_at)}",
        (
            f"Scanned {report.diagnostics.scanned_files}/{report.diagnostics.discovered_files} files"
            f" • {report.diagnostics.parsed_events} usage events"
            f" • {report.diagnostics.duplicate_session_files} duplicate session files skipped"
        ),
    ]
    if report.plan_types:
        header_lines.append(f"Plan types: {', '.join(report.plan_types)}")
    if censored:
        header_lines.append("Privacy: thread titles hidden")
    return ui.panel("Local Codex Usage Viewer", header_lines, color="cyan")


def build_notes_panel(report: UsageReport, ui: TerminalUI, *, censored: bool) -> str:
    footer_lines = [
        (
            f"Source root: {'[hidden]' if censored else report.root}"
            f" • invalid lines: {report.diagnostics.invalid_lines}"
            f" • empty sessions: {report.diagnostics.empty_sessions}"
        ),
        (
            "Limit progress is experimental and uses best-effort local codex.rate_limits websocket events."
            if report.limits is not None
            else "No local rate-limit snapshot found."
        ),
        (
            "Energy and tree offset use a token-weighted heuristic."
            " They are not wall-power measurements."
        ),
        "Estimated cost uses a local heuristic. Treat it as relative guidance, not billing truth.",
    ]
    if report.summary.has_guessed_cost:
        footer_lines.append(
            "Costs prefixed with ~ include fallback prices guessed from the nearest known model family."
        )
    return ui.panel("Notes", footer_lines, color="gray")


def build_overview_panels(report: UsageReport, ui: TerminalUI, *, with_cost: bool, censored: bool) -> list[str]:
    unicode_ok = not ui.enabled or os.environ.get("TERM", "") != "dumb"
    panels = [build_header_panel(report, ui, censored=censored), ui.cards(build_cards(report, with_cost))]
    if report.limits is not None:
        limit_panel = build_limit_panel(report.limits, ui, unicode_ok=unicode_ok)
        if limit_panel is not None:
            panels.append(limit_panel)
    return panels


def build_compact_period_panel(
    title: str,
    color: str,
    rows: list[list[str]],
    ui: TerminalUI,
    *,
    with_cost: bool,
) -> str | None:
    if not rows:
        return None
    compact_rows = []
    for row in rows:
        compact = [row[0], row[1], row[5], row[4], row[6], row[7], row[9]]
        if with_cost:
            compact.append(row[10])
        compact_rows.append(compact)
    headers = ["Period", "Sess", "Total", "Output", "Energy", "Trees", "Activity"]
    align_right = {1, 2, 3, 4, 5}
    if with_cost:
        headers.append("Est. Cost")
        align_right.add(7)
    panel_table = render_table(headers, compact_rows, align_right=align_right)
    return ui.panel(title, panel_table.splitlines(), color=color)


def build_dashboard(
    report: UsageReport,
    ui: TerminalUI,
    *,
    limit: int,
    daily_limit: int,
    with_cost: bool,
    censored: bool,
) -> str:
    unicode_ok = not ui.enabled or os.environ.get("TERM", "") != "dumb"
    lines: list[str] = build_overview_panels(report, ui, with_cost=with_cost, censored=censored)
    weekly_limit = max(1, min(limit, 8))
    monthly_limit = max(1, min(limit, 6))

    daily_panel = build_compact_period_panel(
        "Daily",
        "green",
        build_daily_rows(report, with_cost=with_cost, limit=daily_limit, unicode_ok=unicode_ok),
        ui,
        with_cost=with_cost,
    )
    if daily_panel is not None:
        lines.append(daily_panel)

    weekly_panel = build_compact_period_panel(
        "Weekly",
        "cyan",
        build_weekly_rows(report, with_cost=with_cost, limit=weekly_limit, unicode_ok=unicode_ok),
        ui,
        with_cost=with_cost,
    )
    if weekly_panel is not None:
        lines.append(weekly_panel)

    monthly_panel = build_compact_period_panel(
        "Monthly",
        "yellow",
        build_monthly_rows(report, with_cost=with_cost, limit=monthly_limit, unicode_ok=unicode_ok),
        ui,
        with_cost=with_cost,
    )
    if monthly_panel is not None:
        lines.append(monthly_panel)

    model_rows = build_model_rows(report, with_cost=with_cost, limit=limit, unicode_ok=unicode_ok)
    if model_rows:
        headers = ["Model", "Total", "Cached", "Output", "Activity"]
        align_right = {1, 2, 3}
        if with_cost:
            headers.append("Est. Cost")
            align_right.add(5)
        model_table = render_table(headers, model_rows, align_right=align_right)
        lines.append(ui.panel("Models", model_table.splitlines(), color="yellow"))

    session_rows = []
    for row in build_session_rows(report, with_cost=with_cost, limit=limit, unicode_ok=unicode_ok, censored=censored):
        compact = [row[0], row[1], row[5], row[7]]
        if with_cost:
            compact.append(row[8])
        if not censored:
            compact.append(row[-1])
        session_rows.append(compact)
    if session_rows:
        headers = ["Last Seen", "Model", "Total", "Activity"]
        align_right = {2}
        if with_cost:
            headers.append("Est. Cost")
            align_right.add(4)
        if not censored:
            headers.append("Thread")
        session_table = render_table(headers, session_rows, align_right=align_right)
        lines.append(ui.panel("Top Sessions", session_table.splitlines(), color="magenta"))

    lines.append(build_notes_panel(report, ui, censored=censored))
    return "\n\n".join(lines)


def render_daily_report(report: UsageReport, ui: TerminalUI, *, with_cost: bool, limit: int, censored: bool) -> str:
    unicode_ok = not ui.enabled or os.environ.get("TERM", "") != "dumb"
    lines = build_overview_panels(report, ui, with_cost=with_cost, censored=censored)
    rows = build_daily_rows(report, with_cost=with_cost, limit=limit, unicode_ok=unicode_ok)
    if rows:
        headers = ["Day", "Sess", "Input", "Cached", "Output", "Total", "Energy", "Trees", "Cached%", "Activity"]
        align_right = {1, 2, 3, 4, 5, 6, 7, 8}
        if with_cost:
            headers.append("Est. Cost")
            align_right.add(10)
        lines.append(ui.panel("Daily Report", render_table(headers, rows, align_right=align_right).splitlines(), color="green"))
    lines.append(build_notes_panel(report, ui, censored=censored))
    return "\n\n".join(lines)


def render_monthly_report(report: UsageReport, ui: TerminalUI, *, with_cost: bool, limit: int, censored: bool) -> str:
    unicode_ok = not ui.enabled or os.environ.get("TERM", "") != "dumb"
    lines = build_overview_panels(report, ui, with_cost=with_cost, censored=censored)
    rows = build_monthly_rows(report, with_cost=with_cost, limit=limit, unicode_ok=unicode_ok)
    if rows:
        headers = ["Month", "Sess", "Input", "Cached", "Output", "Total", "Energy", "Trees", "Cached%", "Activity"]
        align_right = {1, 2, 3, 4, 5, 6, 7, 8}
        if with_cost:
            headers.append("Est. Cost")
            align_right.add(10)
        lines.append(ui.panel("Monthly Report", render_table(headers, rows, align_right=align_right).splitlines(), color="yellow"))
    lines.append(build_notes_panel(report, ui, censored=censored))
    return "\n\n".join(lines)


def render_weekly_report(report: UsageReport, ui: TerminalUI, *, with_cost: bool, limit: int, censored: bool) -> str:
    unicode_ok = not ui.enabled or os.environ.get("TERM", "") != "dumb"
    lines = build_overview_panels(report, ui, with_cost=with_cost, censored=censored)
    rows = build_weekly_rows(report, with_cost=with_cost, limit=limit, unicode_ok=unicode_ok)
    if rows:
        headers = ["Week", "Sess", "Input", "Cached", "Output", "Total", "Energy", "Trees", "Cached%", "Activity"]
        align_right = {1, 2, 3, 4, 5, 6, 7, 8}
        if with_cost:
            headers.append("Est. Cost")
            align_right.add(10)
        lines.append(ui.panel("Weekly Report", render_table(headers, rows, align_right=align_right).splitlines(), color="cyan"))
    lines.append(build_notes_panel(report, ui, censored=censored))
    return "\n\n".join(lines)


def render_sessions_report(report: UsageReport, ui: TerminalUI, *, with_cost: bool, limit: int, censored: bool) -> str:
    unicode_ok = not ui.enabled or os.environ.get("TERM", "") != "dumb"
    lines = build_overview_panels(report, ui, with_cost=with_cost, censored=censored)
    rows = build_session_rows(report, with_cost=with_cost, limit=limit, unicode_ok=unicode_ok, censored=censored)
    if rows:
        headers = ["Last Seen", "Model", "Input", "Cached", "Output", "Total", "Cached%", "Activity"]
        align_right = {2, 3, 4, 5, 6}
        if with_cost:
            headers.append("Est. Cost")
            align_right.add(8)
        if not censored:
            headers.append("Thread")
        lines.append(ui.panel("Sessions Report", render_table(headers, rows, align_right=align_right).splitlines(), color="magenta"))
    lines.append(build_notes_panel(report, ui, censored=censored))
    return "\n\n".join(lines)


def build_json_report(report: UsageReport, *, censored: bool) -> dict:
    return {
        "report_type": "dashboard",
        "root": None if censored else report.root,
        "generated_at": report.generated_at,
        "window": {"since": report.since, "until": report.until},
        "summary": {**asdict(report.summary), "tree_offset_hours": report.summary.tree_offset_hours},
        "plan_types": report.plan_types,
        "limits": asdict(report.limits) if report.limits is not None else None,
        "diagnostics": asdict(report.diagnostics),
        "daily": {
            key: {**asdict(value), "tree_offset_hours": value.tree_offset_hours}
            for key, value in sorted(report.daily.items())
        },
        "daily_session_counts": report.daily_session_counts,
        "weekly": {
            key: {**asdict(value), "tree_offset_hours": value.tree_offset_hours}
            for key, value in sorted(report.weekly.items())
        },
        "weekly_session_counts": report.weekly_session_counts,
        "monthly": {
            key: {**asdict(value), "tree_offset_hours": value.tree_offset_hours}
            for key, value in sorted(report.monthly.items())
        },
        "monthly_session_counts": report.monthly_session_counts,
        "models": {
            key: {**asdict(value), "tree_offset_hours": value.tree_offset_hours}
            for key, value in sorted(report.models.items())
        },
        "sessions": {
            key: {
                **asdict(value),
                "title": None if censored else value.title,
                "top_model": value.top_model,
                "tree_offset_hours": value.tree_offset_hours,
            }
            for key, value in sorted(report.sessions.items())
        },
    }


def build_focused_json_report(
    report: UsageReport,
    *,
    command: str,
    with_cost: bool,
    limit: int,
    censored: bool,
) -> dict:
    payload = {
        "report_type": command,
        "root": None if censored else report.root,
        "generated_at": report.generated_at,
        "window": {"since": report.since, "until": report.until},
        "summary": {**asdict(report.summary), "tree_offset_hours": report.summary.tree_offset_hours},
        "plan_types": report.plan_types,
        "limits": asdict(report.limits) if report.limits is not None else None,
        "diagnostics": asdict(report.diagnostics),
    }
    if command == "daily":
        rows = []
        for day_key, aggregate_row in sorted(report.daily.items(), reverse=True)[:limit]:
            item = {
                "day": day_key,
                "label": format_pretty_day(day_key),
                "sessions": report.daily_session_counts.get(day_key, 0),
                "input_tokens": aggregate_row.input_tokens,
                "cached_input_tokens": aggregate_row.cached_input_tokens,
                "output_tokens": aggregate_row.output_tokens,
                "total_tokens": aggregate_row.total_tokens,
                "cached_ratio": aggregate_row.cached_ratio,
                "estimated_energy_wh": aggregate_row.estimated_energy_wh,
                "tree_offset_hours": aggregate_row.tree_offset_hours,
            }
            if with_cost:
                item["estimated_cost_usd"] = aggregate_row.estimated_cost_usd if aggregate_row.has_cost else None
                item["has_guessed_cost"] = aggregate_row.has_guessed_cost
            rows.append(item)
        payload["rows"] = rows
        return payload
    if command == "weekly":
        rows = []
        for week_key, aggregate_row in sorted(report.weekly.items(), reverse=True)[:limit]:
            item = {
                "week_start": week_key,
                "label": format_pretty_week(week_key),
                "sessions": report.weekly_session_counts.get(week_key, 0),
                "input_tokens": aggregate_row.input_tokens,
                "cached_input_tokens": aggregate_row.cached_input_tokens,
                "output_tokens": aggregate_row.output_tokens,
                "total_tokens": aggregate_row.total_tokens,
                "cached_ratio": aggregate_row.cached_ratio,
                "estimated_energy_wh": aggregate_row.estimated_energy_wh,
                "tree_offset_hours": aggregate_row.tree_offset_hours,
            }
            if with_cost:
                item["estimated_cost_usd"] = aggregate_row.estimated_cost_usd if aggregate_row.has_cost else None
                item["has_guessed_cost"] = aggregate_row.has_guessed_cost
            rows.append(item)
        payload["rows"] = rows
        return payload
    if command == "monthly":
        rows = []
        for month_key, aggregate_row in sorted(report.monthly.items(), reverse=True)[:limit]:
            item = {
                "month": month_key,
                "label": format_pretty_month(month_key),
                "sessions": report.monthly_session_counts.get(month_key, 0),
                "input_tokens": aggregate_row.input_tokens,
                "cached_input_tokens": aggregate_row.cached_input_tokens,
                "output_tokens": aggregate_row.output_tokens,
                "total_tokens": aggregate_row.total_tokens,
                "cached_ratio": aggregate_row.cached_ratio,
                "estimated_energy_wh": aggregate_row.estimated_energy_wh,
                "tree_offset_hours": aggregate_row.tree_offset_hours,
            }
            if with_cost:
                item["estimated_cost_usd"] = aggregate_row.estimated_cost_usd if aggregate_row.has_cost else None
                item["has_guessed_cost"] = aggregate_row.has_guessed_cost
            rows.append(item)
        payload["rows"] = rows
        return payload
    if command == "sessions":
        rows = []
        for session in sorted_sessions(report, with_cost=with_cost, limit=limit):
            item = {
                "session_id": session.session_id,
                "title": None if censored else session.title,
                "last_seen": session.last_seen,
                "top_model": session.top_model,
                "input_tokens": session.input_tokens,
                "cached_input_tokens": session.cached_input_tokens,
                "output_tokens": session.output_tokens,
                "total_tokens": session.total_tokens,
                "cached_ratio": session.cached_ratio,
                "first_day": session.first_day,
                "last_day": session.last_day,
                "estimated_energy_wh": session.estimated_energy_wh,
                "tree_offset_hours": session.tree_offset_hours,
            }
            if with_cost:
                item["estimated_cost_usd"] = session.estimated_cost_usd if session.has_cost else None
                item["has_guessed_cost"] = session.has_guessed_cost
            rows.append(item)
        payload["rows"] = rows
        return payload
    return build_json_report(report, censored=censored)


def ui_enabled(args: argparse.Namespace) -> bool:
    if args.json or args.plain:
        return False
    if not sys.stdout.isatty() or not sys.stderr.isatty():
        return False
    return os.environ.get("TERM", "").lower() != "dumb"


def run_once(args: argparse.Namespace, ui: TerminalUI) -> UsageReport:
    root = args.root.expanduser()
    since, until = choose_window(args)
    events, diagnostics = collect_events(
        root,
        since,
        until,
        with_cost=not args.no_cost,
        progress=ui.update_progress if ui.enabled else None,
    )
    ui.finish_progress()
    return build_report(root, since, until, events, diagnostics)


def render_plain_or_json(report: UsageReport, args: argparse.Namespace, ui: TerminalUI) -> str:
    if args.json:
        payload = (
            build_json_report(report, censored=args.censored)
            if args.command == "dashboard"
            else build_focused_json_report(
                report,
                command=args.command,
                with_cost=not args.no_cost,
                limit=args.limit,
                censored=args.censored,
            )
        )
        return json.dumps(payload, indent=2, sort_keys=True)

    if report.diagnostics.parsed_events == 0 and report.limits is None:
        return "No local Codex usage found in the selected window."

    if args.command == "daily":
        return render_daily_report(
            report,
            ui=ui,
            with_cost=not args.no_cost,
            limit=args.limit,
            censored=args.censored,
        )
    if args.command == "weekly":
        return render_weekly_report(
            report,
            ui=ui,
            with_cost=not args.no_cost,
            limit=args.limit,
            censored=args.censored,
        )
    if args.command == "monthly":
        return render_monthly_report(
            report,
            ui=ui,
            with_cost=not args.no_cost,
            limit=args.limit,
            censored=args.censored,
        )
    if args.command == "sessions":
        return render_sessions_report(
            report,
            ui=ui,
            with_cost=not args.no_cost,
            limit=args.limit,
            censored=args.censored,
        )

    return build_dashboard(
        report,
        ui=ui,
        limit=args.limit,
        daily_limit=args.daily_limit,
        with_cost=not args.no_cost,
        censored=args.censored,
    )


def main() -> int:
    parser = build_parser()
    argv = sys.argv[1:]
    if maybe_handle_help_command(parser, argv):
        return 0
    args = parse_args(parser, argv)
    ui = TerminalUI(enabled=ui_enabled(args))

    try:
        while True:
            report = run_once(args, ui)
            output = render_plain_or_json(report, args, ui)

            if args.json:
                print(output)
                return 0

            if ui.enabled:
                ui.clear()
            print(output)

            if args.watch <= 0:
                return 0

            time.sleep(args.watch)
    except KeyboardInterrupt:
        if ui.enabled:
            ui.finish_progress()
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
