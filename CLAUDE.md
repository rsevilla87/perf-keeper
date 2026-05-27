# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

perf-keeper is an AI agent for diagnosing OpenShift Performance & Scale regressions in Prow CI jobs. Given a failed Prow job URL, it extracts metadata, identifies the failing test, gathers CI artifacts, and produces a structured root-cause analysis report. It uses Google Gemini via LangGraph/LangChain with built-in tools for payload comparison (Sippy, RHCOS RPMs, component RPMs).

## Development Commands

```bash
# Install dependencies
uv sync

# Install with dev dependencies (pytest)
uv sync --extra dev

# Run CLI mode
perf-keeper --prow-job-url "<URL>"

# Run server mode
perf-keeper --server --port 8080

# Run tests
uv run pytest
```

## Environment

Requires a `.env` file with `GITHUB_TOKEN`, `GOOGLE_API_KEY`, and optionally `MODEL_NAME`, `LLM_TEMPERATURE`, `MAX_OUTPUT_TOKENS`, `PROW_ARTIFACTS_URL`, `PROW_DOMAIN`, `SIPPY_BASE_URL`, `OCP_RELEASE_API_URL`. The `get_component_rpms` tool requires `oc` and `podman` CLI tools on the host.

## Architecture

**LangGraph agent** (`agent.py`): Builds a `StateGraph` with this flow:

```
START → extract_job_info → [passed? → END] → get_failed_test_info → classify_failed_test ↔ tools → run_analysis ↔ tools → final_report → END
```

- `extract_job_info` / `get_failed_test_info` / `passed_condition` live in `prow_utils.py` — pure URL parsing and HTTP fetches against Prow/gcsweb, no LLM calls
- `classify_failed_test` uses the LLM with `skills/test-classifier.md` to categorize the failure type (kube-burner, orion, k8s-netperf, ingress-perf, other)
- `run_analysis` loads `skills/{type}-analysis.md` as the system prompt (falls back to `generic-test-analysis.md`)
- `final_report` uses `skills/final-report.md` to produce the structured Markdown RCA — no tools bound
- `tools` is a LangGraph `ToolNode` shared between classifier and analysis; `route_after_tools` dispatches back to the correct node based on whether `failed_test_type` is set

**State** (`state.py`): `AgentState` is a `TypedDict` with `messages` (LangGraph message list), job metadata fields, and token counters. State fields are used as template variables in skill prompts via `format(**state)`.

**Skills** (`skills/`): Markdown prompt templates with `{variable}` placeholders filled from `AgentState`. Adding a new analysis type means creating `skills/<type>-analysis.md` and ensuring the classifier can return that type name.

**Tools** (`tools/`): `fetch_artifact` (generic HTTP GET), `fetch_github_pull_request` (GitHub REST API), `compare_releases` (Sippy payload diff), `compare_rhcos_rpms` (RHCOS RPM comparison), and `get_component_rpms` (component RPM listing via oc/podman).

**Server** (`server.py`): FastAPI app with a single `POST /analyze` endpoint. The agent is created once during lifespan startup.

**Entry point**: `perf_keeper.cli:main` registered as `perf-keeper` console script.
