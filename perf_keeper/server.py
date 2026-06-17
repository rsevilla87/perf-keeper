"""REST API server for the perf-keeper diagnosis agent."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

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


@app.post("/analyze", response_model=AgentResponse)
async def analyze(req: AgentData):
    logger.info("Received analysis request for %s", req.job_url)
    state = await _agent.ainvoke({"job_url": str(req.job_url)})
    passed = state["passed"]
    if passed:
        return AgentResponse(passed=True, analysis="Job passed. No diagnosis required.")
    final = (state.get("final_report") or "").strip()
    return AgentResponse(passed=False, analysis=final)
