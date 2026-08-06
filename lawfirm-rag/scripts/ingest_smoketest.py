"""
ingest_smoketest.py
===================
End-to-end ingest + chat smoke test:
  1. Logs in via POST /auth/login and captures the access token.
  2. Triggers POST /documents/ingest-folder for the configured matter.
  3. Runs a generic legal query through the chat endpoint.
  4. Asserts the chat response has an answer and at least one source.

Usage:
    python scripts/ingest_smoketest.py

Environment (all optional, localhost defaults):
    BASE_URL=http://localhost:8000
    EMAIL=partner@ak.law
    PASSWORD=<password>
    MATTER_ID=            (leave empty to ingest into the firm-wide pool)
"""
from __future__ import annotations

import os
import sys

import httpx

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
EMAIL = os.getenv("EMAIL", "partner@ak.law")
PASSWORD = os.getenv("PASSWORD", "password")
MATTER_ID = os.getenv("MATTER_ID", "")


def main() -> int:
    print(f"Target: {BASE_URL}")
    print(f"User:   {EMAIL}   Matter: {MATTER_ID!r}")
    print("=" * 60)

    # 1. Login
    login_resp = httpx.post(
        f"{BASE_URL}/auth/login",
        json={"email": EMAIL, "password": PASSWORD},
        timeout=30,
    )
    login_resp.raise_for_status()
    data = login_resp.json()
    token = data["access_token"]
    print(f"Login OK — role={data.get('role')} access_level={data.get('access_level')}")

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # 2. Ingest folder
    ingest_resp = httpx.post(
        f"{BASE_URL}/documents/ingest-folder",
        headers=headers,
        json={"matter_id": MATTER_ID},
        timeout=1800,
    )
    print(f"Ingest HTTP status: {ingest_resp.status_code}")
    if ingest_resp.status_code != 200:
        print(f"FAIL — ingest endpoint errored: {ingest_resp.text}")
        return 1
    ingest = ingest_resp.json()
    print(
        f"Ingest result: total={ingest.get('total')} "
        f"ok={ingest.get('ok')} errors={len(ingest.get('errors') or [])}"
    )
    if ingest.get("errors"):
        for err in ingest["errors"]:
            print(f"  error: {err}")

    # 3. Chat query
    chat_resp = httpx.post(
        f"{BASE_URL}/lawfirm-chat-trigger-006",
        headers=headers,
        json={"chatInput": "What documents are available?"},
        timeout=120,
    )
    print(f"Chat HTTP status: {chat_resp.status_code}")
    if chat_resp.status_code != 200:
        print(f"FAIL — chat endpoint errored: {chat_resp.text}")
        return 1
    chat = chat_resp.json()

    answer = chat.get("answer") or ""
    sources = chat.get("sources") or []
    print(f"Answer ({len(answer)} chars): {answer[:300]}")
    print(f"Sources: {len(sources)}")

    # 4. Assertions
    if not answer.strip():
        print("FAIL — chat response has no answer")
        return 1
    if len(sources) < 1:
        print("FAIL — chat response has no sources")
        return 1

    print("\nPASS — ingest and chat smoke test completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
