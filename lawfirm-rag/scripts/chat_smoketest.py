from __future__ import annotations

import asyncio
import os
import sys

import httpx


BASE = os.environ.get("LAWFIRM_RAG_BASE", "http://localhost:8000")


async def _login(client: httpx.AsyncClient, email: str, password: str) -> str:
    r = await client.post(f"{BASE}/auth/login", json={"email": email, "password": password})
    r.raise_for_status()
    return r.json()["access_token"]


async def _query(client: httpx.AsyncClient, token: str, message: str) -> dict:
    r = await client.post(
        "/lawfirm-chat-trigger-006",
        json={"chatInput": message, "sessionId": "smoketest"},
        headers={"Authorization": f"Bearer {token}"},
    )
    r.raise_for_status()
    return r.json()


async def main() -> None:
    if len(sys.argv) < 5:
        print("usage: python scripts/chat_smoketest.py <partner_email> <partner_pw> <associate_email> <associate_pw> [query]")
        return
    query = sys.argv[5] if len(sys.argv) > 5 else "Summarise the latest employment policy."
    partner_email, partner_pw, assoc_email, assoc_pw = sys.argv[1:5]

    async with httpx.AsyncClient(base_url=BASE, timeout=60.0) as client:
        p_tok = await _login(client, partner_email, partner_pw)
        a_tok = await _login(client, assoc_email, assoc_pw)

        partner = await _query(client, p_tok, query)
        associate = await _query(client, a_tok, query)

        print("partner output:\n", partner.get("output", "")[:500], "\n")
        print("associate output:\n", associate.get("output", "")[:500], "\n")


if __name__ == "__main__":
    asyncio.run(main())
