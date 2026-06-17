"""Daemon that polls Prow for completed jobs and runs analysis on failures."""
from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import signal
from datetime import datetime, timezone
from pathlib import Path

import httpx

from perf_keeper.agent import create_agent
from perf_keeper.config import get_config

logger = logging.getLogger(__name__)

STATE_FILENAME = "analyzed_jobs.json"
PROWJOBS_ENDPOINT = "/prowjobs.js"
PROWJOBS_OMIT = "pod_spec,decoration_config,annotations,labels"


class ProwWatcher:
    def __init__(
        self,
        job_patterns: list[str],
        since: datetime,
        poll_interval: int,
        output_dir: Path,
    ):
        self.job_patterns = job_patterns
        self.since = since
        self.poll_interval = poll_interval
        self.output_dir = output_dir
        self._state: dict[str, dict] = {}
        self._shutdown = asyncio.Event()

    async def run(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._load_state()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._shutdown.set)

        logger.info(
            "Watching jobs matching %s since %s (poll every %dm)",
            self.job_patterns,
            self.since.isoformat(),
            self.poll_interval,
        )

        while not self._shutdown.is_set():
            try:
                await self._poll_cycle()
            except Exception:
                logger.exception("Error during poll cycle")
            try:
                await asyncio.wait_for(
                    self._shutdown.wait(),
                    timeout=self.poll_interval * 60,
                )
                break
            except asyncio.TimeoutError:
                pass

        logger.info("Shutting down watcher")

    async def _poll_cycle(self) -> None:
        items = await self._fetch_prow_jobs()
        if items is None:
            return
        new_jobs = self._filter_jobs(items)
        logger.info(
            "Poll: %d total items, %d new failed jobs matching pattern",
            len(items),
            len(new_jobs),
        )
        if not new_jobs:
            return

        agent = create_agent()
        for job in new_jobs:
            if self._shutdown.is_set():
                break
            await self._analyze_job(agent, job)

    async def _fetch_prow_jobs(self) -> list[dict] | None:
        url = f"{get_config().prow_domain}{PROWJOBS_ENDPOINT}?omit={PROWJOBS_OMIT}"
        logger.debug("Fetching %s", url)
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
                return data.get("items", [])
        except Exception:
            logger.exception("Failed to fetch prowjobs")
            return None

    def _filter_jobs(self, items: list[dict]) -> list[dict]:
        result = []
        for item in items:
            spec = item.get("spec", {})
            status = item.get("status", {})

            job_name = spec.get("job", "")
            state = status.get("state", "")
            completion_time = status.get("completionTime")

            if state != "failure":
                continue
            if not any(fnmatch.fnmatch(job_name, p) for p in self.job_patterns):
                continue
            if not completion_time:
                continue

            try:
                completed_at = datetime.fromisoformat(completion_time.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                continue

            if completed_at < self.since:
                continue

            build_id = status.get("build_id", "")
            key = f"{job_name}/{build_id}"
            if key in self._state:
                continue

            result.append({
                "job_name": job_name,
                "build_id": build_id,
                "job_url": f"{get_config().prow_artifacts_url}/gcs/test-platform-results/logs/{job_name}/{build_id}/",
                "completed_at": completion_time,
                "prow_url": status.get("url", ""),
            })
        return result

    async def _analyze_job(self, agent, job: dict) -> None:
        job_name = job["job_name"]
        build_id = job["build_id"]
        key = f"{job_name}/{build_id}"
        logger.info("Analyzing %s", key)

        analyzed_at = datetime.now(timezone.utc).isoformat()
        entry: dict = {
            "job_name": job_name,
            "build_id": build_id,
            "job_url": job["job_url"],
            "prow_url": job["prow_url"],
            "completed_at": job["completed_at"],
            "analyzed_at": analyzed_at,
            "report_file": None,
            "error": None,
        }

        try:
            result = await agent.ainvoke({"job_url": job["job_url"]})
            report = self._build_report(job, result, analyzed_at)
            filename = f"{job_name}_{build_id}.md"
            report_path = self.output_dir / filename
            report_path.write_text(report, encoding="utf-8")
            entry["report_file"] = filename
            logger.info("Report saved: %s", report_path)
        except Exception as e:
            logger.exception("Failed to analyze %s", key)
            entry["error"] = str(e)

        self._state[key] = entry
        self._save_state()

    def _build_report(self, job: dict, result: dict, analyzed_at: str) -> str:
        job_name = job["job_name"]
        build_id = job["build_id"]
        failed_step = result.get("failed_step", "N/A")
        failed_test = result.get("failed_test", "N/A")
        failed_test_type = result.get("failed_test_type", "N/A")
        ocp_version = result.get("ocp_version", "N/A")
        regressing_version = result.get("regressing_version", "N/A")
        previous_version = result.get("previous_version", "N/A")
        final_report = (result.get("final_report") or "").strip()
        inp_tokens = result.get("input_tokens", 0)
        out_tokens = result.get("output_tokens", 0)

        lines = [
            f"# Job Analysis: {job_name}",
            "",
            "| Field | Value |",
            "|-------|-------|",
            f"| Job Name | `{job_name}` |",
            f"| Build ID | `{build_id}` |",
            f"| Job URL | {job['job_url']} |",
            f"| Prow URL | {job['prow_url']} |",
            f"| Completed | {job['completed_at']} |",
            f"| Analyzed | {analyzed_at} |",
            f"| Failed Step | `{failed_step}` |",
            f"| Failed Test | `{failed_test}` |",
            f"| Test Type | `{failed_test_type}` |",
            f"| OCP Version | `{ocp_version}` |",
            f"| Regressing Version | `{regressing_version}` |",
            f"| Previous Version | `{previous_version}` |",
            f"| LLM Tokens | input: {inp_tokens}, output: {out_tokens} |",
            "",
            "## Analysis",
            "",
        ]
        if final_report:
            lines.append(final_report)
        else:
            lines.append("_No analysis report was generated._")
        lines.append("")
        return "\n".join(lines)

    def _load_state(self) -> None:
        state_path = self.output_dir / STATE_FILENAME
        if state_path.exists():
            try:
                self._state = json.loads(state_path.read_text(encoding="utf-8"))
                logger.info("Loaded state: %d analyzed jobs", len(self._state))
            except (json.JSONDecodeError, OSError):
                logger.warning("Could not load state file, starting fresh")
                self._state = {}
        else:
            self._state = {}

    def _save_state(self) -> None:
        state_path = self.output_dir / STATE_FILENAME
        state_path.write_text(
            json.dumps(self._state, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
