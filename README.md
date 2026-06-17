# perf-keeper

AI agent for diagnosing OpenShift Performance & Scale regressions in Prow CI jobs.

Given a failed Prow job URL, the agent automatically extracts job metadata, identifies the failing test, gathers evidence from CI artifacts, and produces a structured root-cause analysis report.

## How it works

The agent is built on [LangGraph](https://github.com/langchain-ai/langgraph) and follows this workflow:

1. **Extract job info** - Parse the Prow job URL for job name and build ID
2. **Check job status** - If the job passed, exit early
3. **Identify failing test** - Parse `ci-operator.log` to find the failed step/test
4. **Route analysis** - Choose between Orion (performance regression) or generic (test failure) analysis
5. **AI-guided analysis** - The LLM uses tools to fetch artifacts, inspect GitHub PRs, compare OCP payloads, and correlate findings
6. **Final report** - Generate a structured Markdown RCA report

### Analysis types

- **Orion analysis**: For performance regression tests (`openshift-qe-orion*`). Analyzes regression metrics, compares OCP release payloads via Sippy, fetches RHCOS RPM diffs, and identifies suspect PRs.
- **Generic analysis**: For other test failures. Categorizes failures (infrastructure, installation, test execution, day-2 ops), analyzes benchmarks, and cross-references system health.

### Tools available to the agent

| Tool | Description |
|------|-------------|
| `fetch_artifact` | Fetch text from any HTTP URL (CI logs, JSON reports, etc.) |
| `fetch_github_pull_request` | Get PR metadata (title, body, labels, state) via GitHub REST API |
| `fetch_pr_commits` | Get list of commits in a GitHub PR |
| `fetch_commit_files` | Get files changed in a specific commit |
| `compare_releases` | Compare two OCP payloads via Sippy to identify PR changes |
| `compare_rhcos_rpms` | Compare RHCOS RPM differences between versions |
| `get_component_rpms` | Retrieve component-specific RPM information |

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- A Google Gemini API key
- A GitHub personal access token (for PR analysis)

## Installation

```bash
# Clone the repository
git clone <repo-url>
cd perf-keeper

# Install dependencies with uv (recommended)
uv sync

# Or with pip
pip install -e .

# For development dependencies (pytest, etc.)
uv sync --extra dev
# or
pip install -e ".[dev]"
```

## Configuration

Create a `config.yaml` file in the project root by copying the example template:

```bash
cp config.yaml.example config.yaml
```

Then edit `config.yaml` and add your API credentials:

```yaml
# Required secrets
github_token: YOUR_GITHUB_TOKEN_HERE    # GitHub personal access token with repo read access
google_api_key: YOUR_GOOGLE_API_KEY_HERE # Google Gemini API key

# LLM Configuration (optional - defaults shown)
model_name: gemini-2.5-flash
model_temperature: 0.0

# Logging
log_level: INFO

# URLs (optional - defaults for OpenShift CI)
prow_artifacts_url: https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com
prow_domain: https://prow.ci.openshift.org
github_api_url: https://api.github.com
sippy_base_url: https://sippy.dptools.openshift.org/api
ocp_release_api_url: https://amd64.ocp.releases.ci.openshift.org/api/v1

# Watch mode (optional - for periodic job monitoring)
job_names:
  - "periodic-ci-openshift-eng-ocp-qe-perfscale-ci-main-aws-4.22*"
poll_interval: 15  # minutes
output_dir: ./reports
```

> **Important**: The `config.yaml` file contains secrets and is excluded from version control via `.gitignore`. Never commit this file.

## Usage

### CLI mode (single job analysis)

Diagnose a specific failed Prow job:

```bash
# Diagnose a failed Prow job
perf-keeper --prow-job-url "https://prow.ci.openshift.org/view/gs/test-platform-results/logs/<job-name>/<build-id>/"

# Show LLM token usage after the run
perf-keeper --prow-job-url "https://prow.ci.openshift.org/view/gs/..." --print-token-usage

# Use a custom config file
perf-keeper --config /path/to/config.yaml --prow-job-url "https://..."
```

If the job passed, the agent exits early with a success message. Otherwise, it prints the final RCA report to stdout.


### Watch mode (daemon for periodic monitoring)

Monitor Prow jobs continuously and automatically analyze failures:

```bash
# Start the watcher daemon
perf-keeper --watch

# Analyze jobs completed after a specific date
perf-keeper --watch --since 2026-06-01

# Use a custom config file
perf-keeper --config /path/to/config.yaml --watch
```

Watch mode requires configuring `job_names` in `config.yaml` with job name patterns to monitor:

```yaml
job_names:
  - "periodic-ci-openshift-eng-ocp-qe-perfscale-ci-main-aws-4.22*"
  - "periodic-ci-openshift-eng-ocp-qe-perfscale-ci-main-gcp-*"
poll_interval: 15  # Poll every 15 minutes
output_dir: ./reports  # Where to save analysis reports
```

The watcher will:
- Poll Prow at the configured interval for jobs matching the patterns
- Automatically analyze any new failures
- Save structured Markdown reports to `output_dir`
- Track analyzed jobs in `analyzed_jobs.json` to avoid re-analyzing
- Gracefully handle `SIGINT` and `SIGTERM` for clean shutdown

### Server mode (REST API)

Run as a REST API server:

```bash
# Start the server
perf-keeper --server --port 8080

# With a custom config file
perf-keeper --config /path/to/config.yaml --server --port 8080
```

The server exposes a `/analyze` endpoint:

```bash
curl -X POST "http://localhost:8080/analyze" \
  -H "Content-Type: application/json" \
  -d '{"job_url": "https://prow.ci.openshift.org/view/gs/test-platform-results/logs/<job-name>/<build-id>/"}'
```

Response:

```json
{
    "passed": false,
    "analysis": "The job failed because of the following reason..."
}