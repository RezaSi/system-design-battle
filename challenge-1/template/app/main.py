"""Skeleton implementation for challenge-1.

This boots a FastAPI app that answers /healthz but does not implement
shortening, resolving, or metadata. Use it as a starting point.

Two things you'll need to add to pass the grader:

1. Implement the four endpoints described in challenge-1/README.md.
2. Add a backing store with a Docker volume in docker-compose.yml — the
   grader does `docker compose restart` and verifies that previously
   shortened URLs still resolve. In-memory storage will not pass.

See challenge-1/learning.md for three example compose layouts (single
service with a SQLite volume, app + Postgres, app + Redis + Mongo) and
the RezaSi reference submission for a worked example.
"""

from __future__ import annotations

import uvicorn
from fastapi import FastAPI, HTTPException


app = FastAPI()


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.post("/shorten")
def shorten() -> dict:
    raise HTTPException(status_code=501, detail="not implemented")


@app.get("/api/codes/{code}")
def metadata(code: str) -> dict:
    raise HTTPException(status_code=501, detail="not implemented")


@app.get("/{code}")
def resolve(code: str) -> dict:
    raise HTTPException(status_code=501, detail="not implemented")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
