"""HTTP API: POST /agent/ask, plus the demo map page at /.

Run: uvicorn mapagent.api:app --reload

Questions are rate limited (see mapagent/limits.py). Behind a proxy that sets
X-Forwarded-For, such as Render, set MAP_AGENT_TRUST_PROXY=1 so limits apply per visitor.
"""
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from . import agent, providers
from .limits import Limited, Limiter
from .store import MemoryStore, PostgisStore

WEB = Path(__file__).resolve().parents[1] / "web"
app = FastAPI(title="Map agent")
log = logging.getLogger("uvicorn.error")
limiter = Limiter.from_env()


class AskRequest(BaseModel):
    question: Annotated[str, Field(min_length=1, max_length=agent.MAX_QUESTION)]


@lru_cache
def store():
    url = os.environ.get("MAP_AGENT_DATABASE_URL")
    if url:
        return PostgisStore(url)
    data = os.environ.get("MAP_AGENT_DATA")  # e.g. data/weld.json from scripts/build_weld.py
    return MemoryStore(data) if data else MemoryStore()


@lru_cache
def provider():
    return providers.get(os.environ.get("MAP_AGENT_PROVIDER", "claude"))


def client_id(request: Request):
    if os.environ.get("MAP_AGENT_TRUST_PROXY") == "1":
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@app.get("/health", include_in_schema=False)
def health():
    store()  # fails if the database is unreachable
    return {"ok": True}


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(WEB / "index.html")


@app.post("/agent/ask")
def ask(request: AskRequest, http: Request):
    client = client_id(http)
    try:
        limiter.check(client)
    except Limited as error:
        return JSONResponse({"detail": str(error)}, status_code=429, headers={"Retry-After": str(error.retry_after)})
    try:
        return agent.ask(request.question, store(), provider())
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    except Exception as error:  # report the cause instead of a bare 500; full detail goes to the log
        limiter.refund(client)  # a failed model request should not use up the visitor's or the day's limit
        log.exception("Question failed")
        name = type(error).__name__
        hint = " Check the API key the server was started with." if "Auth" in name or "api_key" in str(error) else ""
        raise HTTPException(502, f"The model request failed ({name}).{hint} The server log has the details.") from None
