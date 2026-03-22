# AGENTS.md

Use the Local Codex Usage Viewer for questions about local Codex usage, token usage, session activity, local analytics, rough cost estimates, or experimental local rate-limit progress.

This is especially useful when Codex analytics are off and someone still wants private, local-only usage tracking from `~/.codex` or `$CODEX_HOME`.

## Preferred Command

If installed:

```bash
local-codex-usage-viewer
```

Check whether it is already installed:

```bash
command -v local-codex-usage-viewer
```

If working from this repository checkout:

```bash
python3 codex_usage.py
```

If the command is missing and installation makes sense:

```bash
pipx install git+https://github.com/uricorn/local-codex-usage-viewer.git
```

## Useful Flags

- `--days 7` for recent usage.
- `--since YYYY-MM-DD --until YYYY-MM-DD` for an explicit date range.
- `--all` for all locally available history.
- `--watch 5` for a live-refresh dashboard.
- `--json` for machine-readable output.
- `--no-cost` to hide heuristic cost estimates.
- `--plain` to disable ANSI colors.
- `--censored` to hide thread titles and the local source path.
- `--root /path/to/codex-home` to scan a different Codex home directory.
- `daily` for day-by-day usage rows.
- `monthly` for month-by-month usage rows.
- `sessions` for detailed top-session output.

Useful JSON extraction:

```bash
local-codex-usage-viewer --json | jq '.limits'
```

## Notes

- Prefer the dashboard for human-facing answers.
- Prefer `--json` for post-processing or scripted summaries.
- Treat cost as heuristic only, not billing truth.
- Treat limit progress as experimental best-effort local metadata from `logs_1.sqlite`.
- `--censored` does not hide limit progress because that section has no thread titles or local path.
