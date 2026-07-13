from __future__ import annotations

import asyncio
import datetime as dt

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from config import settings
from cleanup.orphan_finder import run_cleanup_job
from ingest.drive_webhook import router as drive_router, mount


app = FastAPI(title="Law Firm RAG Worker", version="2.3456")


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok", "now": dt.datetime.utcnow().isoformat() + "Z"}


mount(app)


@app.on_event("startup")
async def schedule_jobs() -> None:
    sched = AsyncIOScheduler()
    sched.add_job(run_cleanup_job, "cron", hour=2, minute=0, id="nightly_cleanup")
    sched.start()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("ingest.worker:app", host="0.0.0.0", port=8001)
