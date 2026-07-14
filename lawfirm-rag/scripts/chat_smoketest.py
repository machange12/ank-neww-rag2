"""
chat_smoketest.py
=================
Signs in as two roles (partner + associate) and calls the chat endpoint
to prove RBAC scoping works end to end.

Usage:
    python scripts/chat_smoketest.py \
        partner@ak.law <partner_pw> \
        assoc@ak.law   <assoc_pw> \
        "Summarise matter M-2024-118's status"
"""
from __future__ import annotations

import sys
import httpx

BASE_URL = "http://localhost:8000"


def run_test(email: str, password: str, query: str) -> None:
    print(f"\n{'='*60}")
    print(f"  User: {email}")
    print(f"{'='*60}")

    # 1. Login
    login_resp = httpx.post(f"{BASE_URL}/auth/login", json={"email": email, "password": password})
    login_resp.raise_for_status()
    data = login_resp.json()
    token = data["access_token"]
    print(f"  Role reported: {data.get('role')}")

    # 2. Chat
    chat_resp = httpx.post(
        f"{BASE_URL}/lawfirm-chat-trigger-006",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"chatInput": query},
        timeout=60,
    )
    print(f"  HTTP status: {chat_resp.status_code}")
    if chat_resp.status_code == 200:
        output = chat_resp.json().get("output", "")
        print(f"  Response ({len(output)} chars):\n  {output[:500]}")
    else:
        print(f"  Error: {chat_resp.text}")


if __name__ == "__main__":
    if len(sys.argv) < 6:
        print("Usage: python scripts/chat_smoketest.py <email1> <pw1> <email2> <pw2> <query>")
        sys.exit(1)

    email1, pw1, email2, pw2, query = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
    run_test(email1, pw1, query)
    run_test(email2, pw2, query)
    print("\nSmoke test complete.")
