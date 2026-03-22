# AGENTS.md

Use the Local Codex Usage Viewer for questions about local Codex usage, token usage, session activity, local analytics, or rough cost estimates.

## Preferred Command

If installed:

```bash
local-codex-usage-viewer
```

If working from this repository checkout:

```bash
python3 codex_usage.py
```

## Useful Flags

- `--days 7` for recent usage.
- `--since YYYY-MM-DD --until YYYY-MM-DD` for an explicit date range.
- `--all` for all locally available history.
- `--watch 5` for a live-refresh dashboard.
- `--json` for machine-readable output.
- `--no-cost` to hide heuristic cost estimates.
- `--censored` to hide thread titles and the local source path.

## Notes

- Prefer the dashboard for human-facing answers.
- Prefer `--json` for post-processing or scripted summaries.
- Treat cost as heuristic only, not billing truth.
