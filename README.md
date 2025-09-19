## DCX GitHub Action

Run the DCX scanner in your CI. This action installs the `dcx` CLI from the DMC download service and runs a full scan of your repository, automatically handling data quality and classification workflows.

### What it does

- **Installs `dcx` CLI** from the DMC service using a release `tag` (default: `latest`).
- **Runs a scan** against your repository using dcx cli:
  - Otherwise, runs: `dcx full-pipeline <repo>`.
- Uses optional AI settings if provided.

### Inputs

- **dcx_tag**: Release tag or `latest` (default: `latest`).
- **repo_path**: Target repository path to scan (default: `${{ github.workspace }}`).
- **max_checks**: Max poll iterations when waiting on AI results (default: `30`).
- **delay**: Delay (seconds) between poll checks (default: `1.0`).
- **ai_endpoint**: Optional AI endpoint URL.
- **ai_api_key**: Optional AI API key.
- **dcx_service_token**: Required token to download/install DCX (use GitHub Secret).

### Required secrets

- **DCX_SERVICE_TOKEN**: Used to authenticate with the DMC download service.

### How the action works (under the hood)

- The composite action (`action.yaml`) invokes `dcx.py`.
- `dcx.py`:
  - Ensures `uv` is available (installs if missing).
  - Resolves the DCX download URL (defaults to `https://api.withdmc.com/v1/dmc/github/releases/download?tag=<tag>`).
  - Downloads the archive (supports JSON envelope that points to an actual asset URL), then installs via `uv tool install` with a `pip` fallback.
  - Detects the latest scan directory under `output/` to infer the scan id.
  - Runs dcx cli workflow:
    - CLI flow: `dcx full-pipeline <repo>`.

### Example usage

Use the included sample workflow as a starting point.

```yaml
name: DCX Action Workflow

on:
  push:
    branches: [main]

jobs:
  dcx-analysis:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout Client Code
        uses: actions/checkout@v3

      - name: Run DCX Action
        uses: DQX-AI/dcx-action@main
        with:
          dcx_service_token: ${{ secrets.DCX_SERVICE_TOKEN }}
          dcx_tag: latest
          repo_path: ${{ github.workspace }}
          scanner_dir: .
          max_checks: "30"
          delay: "1.0"
```

You can also copy `sample-dcx-scan.yml` into `.github/workflows/dcx-scan.yml` in your repository and adjust as needed.

### Environment variables (set by the action)

These are wired from inputs and consumed by `dcx.py`:

- `DCX_URL` (optional; overrides default service URL)
- `DCX_TAG`
- `REPO_PATH`
- `SCANNER_DIR`
- `MAX_CHECKS`
- `DELAY`
- `AI_ENDPOINT` (optional)
- `AI_API_KEY` (optional)
- `DCX_SERVICE_TOKEN` (required)
- `GITHUB_TOKEN` (automatically provided by GitHub Actions)

Note: `dcx.py` primarily uses `DCX_URL`, `DCX_TAG`, `REPO_PATH`, `SCANNER_DIR`, `MAX_CHECKS`, `DELAY`, `AI_ENDPOINT`, `AI_API_KEY`, and `DCX_SERVICE_TOKEN`.

### Outputs

- Results are written under `output/` in your workspace. The script logs the final scan directory, e.g., `output/dcx-scan-<timestamp>`.

### Local debugging

Run the bootstrap locally to test (requires Python 3):

```bash
export DCX_SERVICE_TOKEN=your_token_here
python3 dcx.py
```

Optional flags via env vars:

```bash
export DCX_TAG=latest
export REPO_PATH=$(pwd)
export SCANNER_DIR=.
export MAX_CHECKS=30
export DELAY=1.0
# Optional AI
export AI_ENDPOINT=...
export AI_API_KEY=...
```

### Troubleshooting

- **Install failures**: The script tries `uv tool install` and falls back to `pip3 --user`. Check logs for `install step` messages.
- **Authentication**: Confirm `DCX_SERVICE_TOKEN` is set as a secret and passed via `with.dcx_service_token`.
- **Network**: The download service must be reachable from your runner.

### License

<>
