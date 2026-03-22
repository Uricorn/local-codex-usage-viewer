# Local Codex Usage Viewer

Local Codex Usage Viewer is a small terminal tool that scans local Codex session logs and reconstructs usage without relying on hosted analytics.

It reads the files under `~/.codex` or `$CODEX_HOME`, rebuilds token deltas from local session JSONL logs, and renders a terminal dashboard or JSON report.

![Local Codex Usage Viewer screenshot](docs/screenshot.png)

## Features

- Offline usage reconstruction from local Codex logs.
- Styled terminal dashboard with progress while scanning.
- `--watch` mode for live refresh.
- `--json` mode for scripting and automation.
- `--censored` mode to hide thread titles.
- Model breakdowns, session summaries, daily usage, and heuristic cost estimates.

## Requirements

- Python 3.10+
- Local Codex logs in `~/.codex` or another directory passed via `--root`

## Installation

### Install with `pipx` from GitHub

Use this if you want a clean, isolated install and a globally available `local-codex-usage-viewer` command.

```bash
pipx install git+https://github.com/uricorn/local-codex-usage-viewer.git
```

This installs the latest version from GitHub and exposes the CLI on your shell path:

```bash
local-codex-usage-viewer
```

### Install with `pip` from GitHub

Use this if you want the tool inside the current Python environment instead of an isolated `pipx` environment.

```bash
python3 -m pip install git+https://github.com/uricorn/local-codex-usage-viewer.git
```

### Install from a local clone

Use this when you want to inspect the code, make changes, or install a local checkout.

```bash
git clone https://github.com/uricorn/local-codex-usage-viewer.git
cd local-codex-usage-viewer
pipx install .
```

That installs the checked-out copy, not the GitHub remote tip. If you prefer the current Python environment instead of `pipx`:

```bash
python3 -m pip install .
```

### Run without installing

Use this for a one-off run directly from a checkout.

```bash
python3 codex_usage.py
```

## Usage

```bash
local-codex-usage-viewer
```

Scans the default Codex home directory and renders the terminal dashboard.

```bash
local-codex-usage-viewer --days 7
```

Limits the report to the last 7 days instead of the default rolling window.

```bash
local-codex-usage-viewer --watch 5
```

Refreshes the dashboard every 5 seconds so you can keep it open while working.

```bash
local-codex-usage-viewer --json > usage.json
```

Writes machine-readable JSON instead of the dashboard, which is useful for scripts and automation.

```bash
local-codex-usage-viewer --all --no-cost
```

Scans all locally available history and hides heuristic cost estimates.

```bash
local-codex-usage-viewer --censored
```

Hides thread titles and the local source path so the output is safer to share.

```bash
local-codex-usage-viewer --root /path/to/codex-home
```

Scans a different Codex home directory instead of the default `~/.codex` or `$CODEX_HOME`.

## Output Notes

- Estimated cost is heuristic-only.
- The dashboard is useful for observability and rough comparisons, not billing reconciliation.
- `--censored` removes thread titles and hides the local source path from terminal and JSON output.

## Codex Integration

For repository-local Codex behavior, see:

```text
AGENTS.md
```

For an installable Codex skill, this repository includes:

```text
skills/local-codex-usage-viewer/SKILL.md
```

To install that skill into Codex, place it under:

```text
$CODEX_HOME/skills/local-codex-usage-viewer
```

That gives Codex a reusable skill for local usage questions even outside this repository.

## Credit

This project is directly inspired by CodexBar's local-log scan for Codex usage.

- [CodexBar docs: cost usage local log scan](https://github.com/steipete/CodexBar/blob/main/docs/codex.md#cost-usage-local-log-scan)
- [CostUsageScanner.swift](https://github.com/steipete/CodexBar/blob/main/Sources/CodexBarCore/Vendored/CostUsage/CostUsageScanner.swift)
- [CostUsageScanner+Timestamp.swift](https://github.com/steipete/CodexBar/blob/main/Sources/CodexBarCore/Vendored/CostUsage/CostUsageScanner%2BTimestamp.swift)
- [CostUsagePricing.swift](https://github.com/steipete/CodexBar/blob/main/Sources/CodexBarCore/Vendored/CostUsage/CostUsagePricing.swift)

CodexBar is maintained by Peter Steinberger ([`steipete`](https://github.com/steipete)) and released under the MIT license. The parsing approach and pricing heuristics here were adapted from that work. See [NOTICE](NOTICE) for attribution details.
