---
name: local-codex-usage-viewer
description: Inspect local Codex usage logs and render offline summaries. Use when the user asks about Codex usage, token usage, local analytics, session activity, local cost estimates, or wants a privacy-aware usage report from ~/.codex or $CODEX_HOME.
---
# Local Codex Usage Viewer

## Use This Skill When

- The user wants to understand Codex usage without hosted analytics.
- The user asks for local token counts, session summaries, model usage, or rough cost estimates.
- The user wants a privacy-safe report to share with others.
- The user wants structured JSON output for post-processing or automation.

## Commands

Run the installed command when available:

```bash
local-codex-usage-viewer
```

Repository-local fallback:

```bash
python3 codex_usage.py
```

Useful options:

- `--days 7` for recent usage.
- `--since 2026-03-01 --until 2026-03-22` for an explicit date range.
- `--all` to scan all locally available history.
- `--watch 5` for a live-refresh dashboard.
- `--json` for machine-readable output.
- `--no-cost` to hide heuristic cost estimates.
- `--plain` to disable ANSI colors when plain terminal output is preferred.
- `--censored` to hide thread titles and the local source path.
- `--root /path/to/codex-home` to scan a different Codex home directory.

## Agent Guidance

1. Prefer the dashboard view for human-facing answers.
2. Prefer `--json` when you need to post-process or summarize results programmatically.
3. Use `--censored` before sharing output that could expose thread titles or the local Codex home path.
4. Use `--no-cost` when the user only wants activity, not a pricing estimate.
5. Treat cost as heuristic only. Do not present it as billing truth.
