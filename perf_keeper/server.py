"""REST API server for the perf-keeper diagnosis agent."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
import time
from fastapi import FastAPI
from pydantic import BaseModel, HttpUrl

from perf_keeper.agent import create_agent

logger = logging.getLogger(__name__)

_agent = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _agent
    _agent = create_agent()
    yield


app = FastAPI(title="Perf Keeper", lifespan=lifespan)


class AgentData(BaseModel):
    job_url: HttpUrl


class AgentResponse(BaseModel):
    passed: bool
    analysis: str
    analysis_duration_seconds: int


@app.post("/analyze", response_model=AgentResponse)
async def analyze(req: AgentData):
    logger.info("Received analysis request for %s", req.job_url)
    start_time = time.time()
    state = await _agent.ainvoke({"job_url": str(req.job_url)})
    passed = state["passed"]
    elapsed_time = time.time() - start_time
    if passed:
        return AgentResponse(passed=True, analysis="Job passed. No diagnosis required.", analysis_duration_seconds=int(elapsed_time))
    final = (state.get("final_report") or "").strip()
    return AgentResponse(passed=False, analysis=final, analysis_duration_seconds=int(elapsed_time))
