"""Fetch kube-burner jobSummary documents from Elasticsearch."""
from __future__ import annotations

import logging

import httpx
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from perf_keeper.config import get_config

logger = logging.getLogger(__name__)

class KubeBurnerJobMetadata(BaseModel):
    kube_burner_version: str= Field(default="unknown", description="The version of kube-burner used to run the job.")
    workload_flags: str= Field(default="unknown", description="The kube-burner workload flags used to run the benchmark.")
    execution_errors: str= Field(default="Not available", description="The execution errors observed in the benchmark.")

def _fetch_kube_burner_metadata(uuid: str) -> KubeBurnerJobMetadata:
    uuid = uuid.strip()
    if not uuid:
        return KubeBurnerJobMetadata()

    cfg = get_config()
    if not cfg.es_server:
        return KubeBurnerJobMetadata()

    es_server = cfg.es_server.rstrip("/")
    url = f"{es_server}/{cfg.es_index}/_search"
    logger.info("Fetching kube-burner metadata for uuid=%s", uuid)

    job_summary_query = {
        "query": {
            "bool": {
                "must": [
                    {"term": {"uuid.keyword": uuid}},
                    {"term": {"metricName.keyword": "jobSummary"}},
                ],
            },
        },
        "size": 1,
    }
    try:
        resp = httpx.post(
            url,
            json=job_summary_query,
            headers={"Content-Type": "application/json"},
            timeout=60,
            verify=cfg.es_verify,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.exception("Elasticsearch query failed for uuid=%s: %s", uuid, e)
        return KubeBurnerJobMetadata()

    hits = data.get("hits", {}).get("hits", [])
    if not hits:
        return KubeBurnerJobMetadata()

    jobSummary = hits[0]["_source"]
    logger.info("Kube-burner metadata for uuid=%s: kube_burner_version=%s, workload_flags=%s, execution_errors=%s", uuid, jobSummary.get("version", "unknown"), jobSummary.get("workloadFlags", jobSummary.get("workload_flags", "unknown")), jobSummary.get("executionErrors", "Not available"))
    return KubeBurnerJobMetadata(
        kube_burner_version=jobSummary.get("version", "unknown"),
        workload_flags=jobSummary.get("workload_flags", "unknown"),
        execution_errors=jobSummary.get("execution_errors", "Not available"),
    )


@tool
def fetch_kube_burner_metadata(uuid: str) -> KubeBurnerJobMetadata:
    """Fetch kube-burner jobSummary metadata from Elasticsearch by workload UUID.

    kube-burner indexes one jobSummary document per benchmark run, keyed by the
    benchmark UUID. ES server and index are read from config — pass only the uuid.

    Returns kube-burner version, workloadFlags, job name, timestamps, and pass/fail.

    Args:
        uuid: kube-burner benchmark UUID (extracted from the Build URL in the Orion report).
    """
    return _fetch_kube_burner_metadata(uuid)
