"""Shared helper for extracting `.data` from a Supabase/PostgREST response.

The codebase previously did this inline as ``getattr(response, "data", None)
or response`` in 15 places. That pattern is broken for any RPC whose valid
answer is falsy: an empty list (zero matching rows — completely normal) or a
bare boolean/scalar RPC returning ``False``/``0``. ``or`` can't distinguish
"attribute is missing" from "attribute is present and legitimately falsy", so
it silently falls back to the whole response object in both cases.

Two confirmed live consequences before this fix:
  * ``authz/service.py``'s ``can_administer_matter_ref()`` calls a
    boolean-returning RPC. When the DB correctly answered ``False``,
    ``False or response`` produced the (truthy) response object, and
    ``bool(response)`` was always ``True`` — an authorization bypass on the
    document upload/ingest matter-admin check.
  * ``search/hybrid.py``'s hybrid search RPC returns a list. Whenever it
    legitimately matched zero rows, ``[] or response`` produced the response
    object; iterating it (most Postgrest response types are iterable as
    ``(field, value)`` pairs) raised ``'tuple' object has no attribute
    'get'``, silently degrading every such query to the slower vector-only
    fallback path and doubling the RPC round-trips per query.
"""
from __future__ import annotations

from typing import Any


def response_data(response: Any) -> Any:
    """Correctly extract `.data` from a Supabase/PostgREST response object.

    Returns ``response.data`` whenever the attribute exists — including when
    its value is falsy (``[]``, ``False``, ``0``, ``None``) — and only falls
    back to ``response`` itself when the attribute is genuinely absent
    (e.g. a plain dict/list already unwrapped by a caller or test fake).
    """
    if hasattr(response, "data"):
        return response.data
    return response
