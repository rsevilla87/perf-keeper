from typing import Annotated, Literal, NotRequired, TypedDict
from langgraph.graph.message import add_messages

# AgentState is the state of the agent
class AgentState(TypedDict):
    """State of the agent."""
    messages: Annotated[list, add_messages]
    passed: bool
    job_url: str
    job_name: str
    build_id: str
    failed_step: str
    failed_test: str
    failed_workload: NotRequired[str]
    job_result: str
    job_analysis: str
    version_diffs: str
    # Classifier output from get_failed_test_type (e.g. orion, kube-burner).
    failed_test_type: NotRequired[str]
    # Which analysis subgraph is active; used to route tools back to one node only.
    analysis_route: NotRequired[Literal["orion_analysis", "generic_analysis"]]
    # Tool-free consolidated report (Markdown) from the final_report node.
    final_report: NotRequired[str]
    # Cumulative LLM token usage across analysis + final_report invocations.
    input_tokens: NotRequired[int]
    output_tokens: NotRequired[int]

    # Regressing version and previous version
    regressing_version: NotRequired[str]
    regressing_uuid: NotRequired[str]
    previous_version: NotRequired[str]
    previous_uuid: NotRequired[str]
    # OCP version
    ocp_version: NotRequired[str]