"""Skeleton implementation for challenge-2.

Boots a FastAPI app that answers /healthz and returns 501 for the rest.
Your job is to implement /api/work and /api/quota with a working rate
limiter.
"""

from __future__ import annotations

import uvicorn
from fastapi import FastAPI, HTTPException


app = FastAPI()


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.post("/api/work")
def work() -> dict:
    raise HTTPException(status_code=501, detail="not implemented")


@app.get("/api/quota")
def quota() -> dict:
    raise HTTPException(status_code=501, detail="not implemented")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
