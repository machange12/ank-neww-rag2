"""Root logging configuration, shared by the app and the ingest worker.

Previously neither process called ``logging.basicConfig``/``dictConfig``
anywhere, so the root logger had no handler. Every ``logger.info()`` and
``logger.debug()`` call across the codebase (retrieval-quality logs in
``agents/rag_agent.py``, ingest/scheduler/cleanup logs) was silently dropped;
only WARNING+ reached stderr, via Python's built-in unformatted
``logging.lastResort`` handler. Call ``configure_logging()`` once, as early as
possible in each process's entrypoint, before any request/job can log.
"""
from __future__ import annotations

import logging

from config import settings

_CONFIGURED = False


def configure_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
    )
    _CONFIGURED = True
