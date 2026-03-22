# Local Codex Usage Viewer

Local Codex Usage Viewer is a small terminal tool that scans local Codex session logs and reconstructs usage without relying on hosted analytics.

It reads the files under `~/.codex` or `$CODEX_HOME`, rebuilds token deltas from local session JSONL logs, and renders a terminal dashboard or JSON report.

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

```bash
pipx install git+https://github.com/uricorn/local-codex-usage-viewer.git
```

Then run:

```bash
local-codex-usage-viewer
```

### Install with `pip` from GitHub

```bash
python3 -m pip install git+https://github.com/uricorn/local-codex-usage-viewer.git
```

### Install from a local clone

```bash
git clone https://github.com/uricorn/local-codex-usage-viewer.git
cd local-codex-usage-viewer
pipx install .
```

Or with `pip`:

```bash
python3 -m pip install .
```

### Run without installing

```bash
python3 codex_usage.py
```

## Usage

```bash
local-codex-usage-viewer
local-codex-usage-viewer --days 7
local-codex-usage-viewer --watch 5
local-codex-usage-viewer --json > usage.json
local-codex-usage-viewer --all --no-cost
local-codex-usage-viewer --censored
local-codex-usage-viewer --root /path/to/codex-home
```

## Output Notes

- Estimated cost is heuristic-only.
- The dashboard is useful for observability and rough comparisons, not billing reconciliation.
- `--censored` removes thread titles and hides the local source path from terminal and JSON output.

## Agent Skill

This repository includes a project skill at:

```text
.cursor/skills/local-codex-usage-viewer/SKILL.md
```

It gives agents a standard way to run the viewer for local Codex usage questions.

## Credit

This project is directly inspired by CodexBar's local-log scan for Codex usage.

- [CodexBar docs: cost usage local log scan](https://github.com/steipete/CodexBar/blob/main/docs/codex.md#cost-usage-local-log-scan)
- [CostUsageScanner.swift](https://github.com/steipete/CodexBar/blob/main/Sources/CodexBarCore/Vendored/CostUsage/CostUsageScanner.swift)
- [CostUsageScanner+Timestamp.swift](https://github.com/steipete/CodexBar/blob/main/Sources/CodexBarCore/Vendored/CostUsage/CostUsageScanner%2BTimestamp.swift)
- [CostUsagePricing.swift](https://github.com/steipete/CodexBar/blob/main/Sources/CodexBarCore/Vendored/CostUsage/CostUsagePricing.swift)

CodexBar is maintained by Peter Steinberger ([`steipete`](https://github.com/steipete)) and released under the MIT license. The parsing approach and pricing heuristics here were adapted from that work. See [NOTICE](NOTICE) for attribution details.
