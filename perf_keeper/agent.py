import logging
import re
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from perf_keeper.config import get_config
from perf_keeper.tools.artifact import fetch_artifact
from perf_keeper.tools.github_pr import (
    fetch_commit_files,
    fetch_github_pull_request,
    fetch_pr_commits,
)
from perf_keeper.tools.openshift_release import (
    compare_releases,
    compare_rhcos_rpms,
    get_component_rpms,
)
from perf_keeper.prow_utils import extract_job_info, passed_condition, get_failed_test_info
from perf_keeper.state import AgentState
from perf_keeper.tools.workload_metadata import fetch_kube_burner_metadata

logger = logging.getLogger(__name__)

SKILLS_DIR = "skills"


def _usage_from_ai_message(msg: BaseMessage) -> tuple[int, int]:
    """Return (input_tokens, output_tokens) from an AIMessage, or (0, 0) if absent."""
    if not isinstance(msg, AIMessage):
        return (0, 0)
    meta = getattr(msg, "usage_metadata", None)
    if not isinstance(meta, dict):
        return (0, 0)
    inp = meta.get("input_tokens")
    out = meta.get("output_tokens")
    try:
        return (int(inp) if inp is not None else 0, int(out) if out is not None else 0)
    except (TypeError, ValueError):
        return (0, 0)


def _text_from_response_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "\n".join(parts)
    return str(content)


def _extract_labeled_value(content: str, label: str) -> str | None:
    """Extract value from a 'Label: value' line; tolerates spacing and backticks."""
    pattern = re.compile(rf"(?im)^\s*{re.escape(label)}\s*:\s*(.+?)\s*$")
    match = pattern.search(content)
    if not match:
        return None
    value = match.group(1).strip().strip("`\"'")
    return value or None


TOOLS = [
    fetch_artifact,
    fetch_github_pull_request,
    fetch_pr_commits,
    fetch_commit_files,
    compare_releases,
    compare_rhcos_rpms,
    get_component_rpms,
    fetch_kube_burner_metadata,
]


# Gemini requires an assistant tool-call turn to follow a *user* turn (or a tool
# result). We only persist AIMessage/ToolMessage in state, so follow-up turns
# must replay the same opening user message before history.
ANALYSIS_SYSTEM_PROMPT = """
    "Diagnose this OpenShift prow job. Use the procedures outlined in the system prompt to diagnose the job."
"""

def create_agent() -> StateGraph:
    """Create the LangGraph diagnosis agent."""
    cfg = get_config()
    logger.info("Using model: %s", cfg.model_name)
    llm_base = ChatGoogleGenerativeAI(
        model=cfg.model_name,
        temperature=cfg.model_temperature,
        google_api_key=cfg.google_api_key or None,
    )
    llm_analysis_force_tools = llm_base.bind_tools(TOOLS, tool_choice="any")
    llm_analysis_auto = llm_base.bind_tools(TOOLS)

    async def classify_failed_test(state: AgentState) -> dict:
        """Get the type of the failed test from the state."""
        with open(f"{SKILLS_DIR}/test-classifier.md", "r") as f:
            system_prompt = f.read()
        system_prompt = system_prompt.format(**state,
            artifacts_base=cfg.prow_artifacts_url,
        )
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content="You're a test classifier. You need to classify the type of test that failed."),
        ]
        messages.extend(state.get("messages", []))
        llm = llm_base.bind_tools([fetch_artifact])
        response = llm.invoke(messages)
        out: dict = {"messages": [response]}
        if isinstance(response, AIMessage) and getattr(response, "tool_calls", None):
            return out
        failed_test_type = response.content[0]['text'] if isinstance(response.content, list) else response.content
        logger.info("Failed test type: %r", failed_test_type)
        out["failed_test_type"] = failed_test_type
        if "ocp_version" in response.content if isinstance(response.content, dict) else None:
            out["ocp_version"] = response.content["ocp_version"]
        return out

    async def run_analysis(state: AgentState) -> dict:
        failed_test_type = state.get("failed_test_type")
        prompt_file = f"{failed_test_type}-analysis.md"
        node_name = f"{failed_test_type}_analysis" if failed_test_type else "generic_analysis"
        logger.info(f"Running analysis: {prompt_file}")
        with open(f"{SKILLS_DIR}/{prompt_file}", "r") as f:
            system_prompt = f.read()
        prompt = system_prompt.format(**state,
            artifacts_base=cfg.prow_artifacts_url,
        )
        messages = [SystemMessage(content=prompt), HumanMessage(content=ANALYSIS_SYSTEM_PROMPT)]
        prior = state.get("messages") or []
        if prior:
            messages.extend(prior)
        # When the last turn is not tool output, require a tool call so analysis
        # cannot no-op (empty content, 0 output tokens) before the tool node runs.
        tail = prior[-1] if prior else None
        invoke_llm = (
            llm_analysis_force_tools
            if not isinstance(tail, ToolMessage)
            else llm_analysis_auto
        )
        logger.info(
            "%s: invoking model (%d message(s))",
            node_name,
            len(messages),
        )
        response = invoke_llm.invoke(messages)
        d_in, d_out = _usage_from_ai_message(response)
        out = {
            "messages": [response],
            "input_tokens": state.get("input_tokens", 0) + d_in,
            "output_tokens": state.get("output_tokens", 0) + d_out,
        }
        content = _text_from_response_content(response.content)
        regressing = _extract_labeled_value(content, "Regressing version")
        previous = _extract_labeled_value(content, "Previous version")
        failed_workload = _extract_labeled_value(content, "Failed workload")
        out["failed_workload"] = failed_workload or ""
        if regressing:
            out["regressing_version"] = regressing
        if previous:
            out["previous_version"] = previous
        if regressing and previous:
            logger.info("Versions: %s → %s", previous, regressing)
        regressing_uuid = _extract_labeled_value(content, "Regressing UUID")
        previous_uuid = _extract_labeled_value(content, "Previous UUID")
        if regressing_uuid:
            out["regressing_uuid"] = regressing_uuid
        if previous_uuid:
            out["previous_uuid"] = previous_uuid
        ocp_version = _extract_labeled_value(content, "ocp_version")
        if ocp_version:
            out["ocp_version"] = ocp_version
            logger.info("OCP version: %s", ocp_version)
        return out

    def tools_required(state: AgentState) -> str:
        """Continue to tool execution, or to final report when the model returned text only."""
        msgs = state.get("messages") or []
        if not msgs:
            # e.g. a node returned non-message updates only; safe default is no tools.
            return "next"
        last = msgs[-1]
        if last.tool_calls:
            return "tools"
        else:
            return "next"

    def route_after_tools(state: AgentState) -> str:
        """Resume the correct node after ToolNode execution.

        - If the classifier hasn't produced `failed_test_type` yet, tools were run for
          the classifier, so return to `classify_failed_test`.
        - Otherwise, tools were run for analysis, so return to `run_analysis`.
        """
        if not state.get("failed_test_type"):
            return "classify_failed_test"
        return "run_analysis"

    async def final_report(state: AgentState) -> dict:
        """Single tool-free pass: structured Markdown from the full message history."""
        logger.info("Analysis Node: final_report")
        with open(f"{SKILLS_DIR}/final-report.md", "r") as f:
            fmt_vars = {
                "regressing_version": "unknown",
                "previous_version": "unknown",
                "ocp_version": "unknown",
                **state,
            }
            system_prompt = f.read().format(**fmt_vars)
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(
                content=(
                    "The messages below are the full analysis thread. "
                    "Generate a final report based on the analysis thread using the format and the system template."
                )
            ),
        ]
        messages.extend(state.get("messages", []))
        response = llm_base.invoke(messages)
        d_in, d_out = _usage_from_ai_message(response)
        # The model may return a list of string, so we need to join them together.
        if isinstance(response.content, list):
            response_content = " ".join(response.content)
        else:
            response_content = response.content.strip()
        return {
            "messages": [response],
            "final_report": response_content,
            "input_tokens": state.get("input_tokens", 0) + d_in,
            "output_tokens": state.get("output_tokens", 0) + d_out,
        }

    workflow = StateGraph(AgentState)
    workflow.add_node("extract_job_info", extract_job_info)
    workflow.add_node("get_failed_test_info", get_failed_test_info)
    workflow.add_node("classify_failed_test", classify_failed_test)
    workflow.add_node("run_analysis", run_analysis)
    workflow.add_node("final_report", final_report)
    workflow.add_node("tools", ToolNode(TOOLS))

    # Define the flow
    workflow.add_edge(START, "extract_job_info")
    # Only conditional edges from set_job_state: an unconditional edge here would
    # still schedule get_failed_test even when passed_condition returns END.
    workflow.add_conditional_edges("extract_job_info", passed_condition)
    workflow.add_edge("get_failed_test_info", "classify_failed_test")
    workflow.add_conditional_edges("classify_failed_test", tools_required, {"tools": "tools", "next": "run_analysis"})
    workflow.add_conditional_edges(
        "run_analysis",
        tools_required,
        {
            "tools": "tools",
            "next": "final_report",
        }
    )
    workflow.add_conditional_edges(
        "tools",
        route_after_tools,
        {
            "classify_failed_test": "classify_failed_test",
            "run_analysis": "run_analysis",
        },
    )
    workflow.add_edge("final_report", END)
    return workflow.compile()
