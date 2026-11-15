"""HTTP API: POST /agent/ask, plus the demo map page at /.

Run: uvicorn mapagent.api:app --reload
"""
import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import agent, providers
from .store import MemoryStore, PostgisStore

WEB = Path(__file__).resolve().parents[1] / "web"
app = FastAPI(title="Map agent")


class AskRequest(BaseModel):
    question: Annotated[str, Field(min_length=1, max_length=agent.MAX_QUESTION)]


@lru_cache
def store():
    url = os.environ.get("MAP_AGENT_DATABASE_URL")
    return PostgisStore(url) if url else MemoryStore()


@lru_cache
def provider():
    return providers.get(os.environ.get("MAP_AGENT_PROVIDER", "claude"))


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(WEB / "index.html")


@app.post("/agent/ask")
def ask(request: AskRequest):
    try:
        return agent.ask(request.question, store(), provider())
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
