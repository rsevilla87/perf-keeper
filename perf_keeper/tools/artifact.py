"""Generic HTTP artifact fetching (CI logs, Prow gcsweb, or any GET-able URL)."""
from __future__ import annotations

import httpx
from langchain_core.tools import tool
import logging

logger = logging.getLogger(__name__)

@tool
def fetch_artifact(url: str) -> str:
    """Fetch text from an HTTP(S) URL (artifacts, raw logs, JSON reports, etc.).

    Args:
        url: Absolute URL to GET.
    """
    try:
        logger.info(f"Fetching artifact from: {url}")
        resp = httpx.get(url, follow_redirects=True)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        return f"Error fetching artifact: {e}"
