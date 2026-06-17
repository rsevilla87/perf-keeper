"""CLI entry point for the perf-keeper diagnosis agent."""
from __future__ import annotations

import argparse
import logging
import asyncio
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from langchain_core.messages import AIMessage, BaseMessage
from urllib.parse import urlparse

from perf_keeper.config import load_config, get_config
from perf_keeper.agent import create_agent
from perf_keeper.server import app


def _text_from_message_content(content: object) -> str | None:
    if content is None:
        return None
    if isinstance(content, str):
        s = content.strip()
        return s or None
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") == "text" and block.get("text") is not None:
                    parts.append(str(block["text"]))
        s = "\n".join(parts).strip()
        return s or None
    return None


def _last_diagnosis_text(messages: list[BaseMessage]) -> str | None:
    """Prefer the latest AIMessage with non-empty text (skips tool-only turns)."""
    if not messages:
        return None
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            text = _text_from_message_content(msg.content)
            if text:
                return text
    return _text_from_message_content(messages[-1].content)


def _print_token_totals(result: dict) -> None:
    inp = int(result.get("input_tokens") or 0)
    out = int(result.get("output_tokens") or 0)
    total = inp + out
    print(f"LLM tokens — input: {inp}  output: {out}  total: {total}")


async def run_non_interactive(job_url: str, *, print_token_usage: bool = False):
    logger = logging.getLogger(__name__)
    agent = create_agent()
    result = await agent.ainvoke({"job_url": job_url})
    if result.get("passed"):
        logger.info("✅ Job passed. No diagnosis required.")
        return
    final = (result.get("final_report") or "").strip()
    if final:
        print(f"\n{final}\n")
    else:
        messages = result.get("messages") or []
        text = _last_diagnosis_text(messages)
        if text:
            print(f"\n{text}\n")
        else:
            logger.warning("No final_report text and no assistant message to print.")
    if print_token_usage:
        _print_token_totals(result)

def main():
    parser = argparse.ArgumentParser(description="OpenShift Perf & Scale Diagnosis Agent",
                                     formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--config", type=str, default="config.yaml",
                        help="Path to the YAML configuration file")
    parser.add_argument("--prow-job-url", type=str, help="Prow job URL to diagnose")
    parser.add_argument(
        "--print-token-usage",
        action="store_true",
        help="After the run, print cumulative LLM input/output/total tokens",
    )
    parser.add_argument(
        "--server",
        action="store_true",
        help="Rest API server mode",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Server mode port (only used with --server)",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Daemon mode: poll Prow for failed jobs matching job_names in the config file",
    )
    parser.add_argument(
        "--since",
        type=str,
        default=None,
        help="Only analyze jobs completed after this date (ISO format, e.g. 2026-06-01). Defaults to now",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    log_level = getattr(logging, cfg.log_level.upper())
    logging.basicConfig(level=log_level, format="%(levelname)s %(name)s: %(message)s")
    for noisy in (
        "httpcore",
        "google_genai",
        "google",
        "langchain",
        "langchain_google_genai",
        "langgraph",
        "uvicorn.access",
        "httpx",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger("perf_keeper").setLevel(log_level)

    if args.server:
        uvicorn.run(app, host="0.0.0.0", port=args.port, log_level=cfg.log_level.lower())
        return
    if args.watch:
        from perf_keeper.watcher import ProwWatcher

        if not cfg.job_names:
            parser.error("Config file must contain a non-empty 'job_names' list for --watch mode")
        if args.since:
            since = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
        else:
            since = datetime.now(timezone.utc)
        watcher = ProwWatcher(
            job_patterns=cfg.job_names,
            since=since,
            poll_interval=cfg.poll_interval,
            output_dir=Path(cfg.output_dir),
        )
        asyncio.run(watcher.run())
        return
    if args.prow_job_url:
        try:
            urlparse(args.prow_job_url)
        except ValueError:
            parser.error("Invalid Prow job URL")
    else:
        parser.error("--prow-job-url is required (or use --server or --watch mode)")
    asyncio.run(
        run_non_interactive(args.prow_job_url, print_token_usage=args.print_token_usage)
    )

if __name__ == "__main__":
    main()
