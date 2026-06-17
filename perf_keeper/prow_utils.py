from __future__ import annotations
import logging
import re
from langchain_core.messages import SystemMessage
from langgraph.graph import END
from perf_keeper.config import get_config
from perf_keeper.state import AgentState
import httpx

logger = logging.getLogger(__name__)

# Extract the job name and build id from the URL
# https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/test-platform-results/logs/periodic-ci-openshift-eng-ocp-qe-perfscale-ci-main-metal-4.22-nightly-x86-daily-virt-6nodes/2041788597949960192/
# URL format is https://{prow_domain}/view/gs/test-platform-results/logs/{job_name}/{build_id}
def extract_job_info(state: AgentState) -> dict:
    job_url = state["job_url"]
    try:
        match = re.search(r'/logs/([^/]+)/(\d+)/?', job_url)
        if match:
            job_name, build_id = match.group(1), match.group(2)
            logger.info(
                "extract_job_info: resolved job_name=%r build_id=%r",
                job_name,
                build_id,
            )
            job_state = get_job_state(job_name, build_id)
            result = {
                "job_name": job_name,
                "build_id": build_id,
            }
            if isinstance(job_state, dict):
                result.update(job_state)
            return result
        else:
            logger.warning("extract_job_info: no /logs/{job}/{build}/ pattern in URL: %s", job_url)
            return {
                "messages": [
                    SystemMessage(content=f"Couldn't extract job name and build id from URL: {job_url}"),
                ]
            }
    except Exception as e:
        logger.exception("extract_job_info: unexpected error while parsing URL")
        return {
            "error": f"Error parsing URL: {e}",
        }

def get_job_state(job_name: str, build_id: str) -> bool:
    """Check if a job is failed by checking the finished.json file"""
    try:
        url = f"{get_config().prow_artifacts_url}/gcs/test-platform-results/logs/{job_name}/{build_id}/finished.json"
        resp = httpx.get(url)
        resp.raise_for_status()
        json_data = resp.json()
        logger.info(f"get_job_state: job passed: {json_data.get('passed')}")
        return {
            "passed": json_data.get("passed"),
        }
    except Exception as e:
        logger.exception("get_job_state: unexpected error while checking job status")
        return {
            "error": f"Error checking job status: {e}",
        }

def passed_condition(state: AgentState) -> str:
    if state.get("passed"):
        return END
    return "get_failed_test_info"


def get_failed_test_info(state: AgentState) -> dict:
    job_name = state["job_name"]
    build_id = state["build_id"]
    url = f"{get_config().prow_artifacts_url}/gcs/test-platform-results/logs/{job_name}/{build_id}/artifacts/ci-operator.log"
    logger.info(f"get_failed_test_info: Extracting failed test/step info from: {url}")
    resp = httpx.get(url)
    # Look for the line containing
    # {"level":"error","msg":"\n  * could not run steps: step {step} failed: \"{step}\" test steps failed: \"{step}\" pod \"{step}-{test}\" failed: could not watch pod
    resp.raise_for_status()
    for line in resp.text.splitlines():
        if "could not run steps:" in line:
            step_name = re.search(r"could not run steps: step ([\w-]+)", line).group(1)
            pod_pattern = rf'pod \\?"{re.escape(step_name)}-([\w-]+)\\?"'
            test_name = re.search(pod_pattern, line).group(1)
            logger.info(f"get_failed_test: failed step={step_name} failed test={test_name}")
            return {
                "failed_step": step_name,
                "failed_test": test_name,
            }   
    return {
        "failed_step": None,
        "failed_test": None,
    }