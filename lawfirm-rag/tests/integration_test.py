"""End-to-end integration tests against a running FastAPI backend.

Plain script (no pytest). Run with:
    python tests/integration_test.py

The backend is expected to be running at the BASE_URL below.
"""
from __future__ import annotations

import sys
import traceback
from typing import Any, Callable

import requests

BASE_URL = "http://localhost:8001"

CREDENTIALS = {
    "admin": {"email": "admin@ak.law", "password": "Pork@2613"},
    "partner": {"email": "partner@ak.law", "password": "machanje@2613"},
    "assoc": {"email": "assoc@ak.law", "password": "myhero1@"},
}

SESSION = requests.Session()

results: list[tuple[str, bool, str]] = []
tokens: dict[str, str] = {}
partner_session_id: str | None = None


def bearer(user: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens[user]}"}


def record(name: str, passed: bool, detail: str = "") -> None:
    results.append((name, passed, detail))
    status = "PASS" if passed else "FAIL"
    print(f"{status}  {name}" + (f"  [{detail}]" if detail else ""))


def login_test() -> None:
    expected = {
        "admin": ("managing_partner", 5),
        "partner": ("partner", 4),
        "assoc": ("associate", 2),
    }
    for user, (exp_role, exp_level) in expected.items():
        name = f"Login {user} -> role={exp_role}, access_level={exp_level}"
        try:
            resp = SESSION.post(f"{BASE_URL}/auth/login", json=CREDENTIALS[user])
            if resp.status_code != 200:
                record(name, False, f"status={resp.status_code} body={resp.text[:200]}")
                continue
            data = resp.json()
            token = data.get("access_token")
            if not token:
                record(name, False, "no access_token in response")
                continue
            tokens[user] = token
            role = data.get("role")
            level = data.get("access_level")
            if role == exp_role and level == exp_level:
                record(name, True, f"token present, role={role}, level={level}")
            else:
                record(name, False, f"role={role}, level={level}, expected {exp_role}/{exp_level}")
        except Exception as exc:  # noqa: BLE001
            record(name, False, f"exception: {exc}")


def chat_session_test() -> None:
    global partner_session_id
    name = "Chat session creation as partner (200 + answer field)"
    try:
        resp = SESSION.post(
            f"{BASE_URL}/lawfirm-chat-trigger-006",
            json={
                "chatInput": "What is data breach liability under Kenyan law?",
                "sessionId": "partner-test-session-001",
            },
            headers=bearer("partner"),
        )
        if resp.status_code != 200:
            record(name, False, f"status={resp.status_code} body={resp.text[:200]}")
            return
        data = resp.json()
        if "answer" not in data:
            record(name, False, "no 'answer' field in response")
            return
        partner_session_id = data.get("session_id") or partner_session_id
        record(name, True, f"answer present, session_id={partner_session_id}")
    except Exception as exc:  # noqa: BLE001
        record(name, False, f"exception: {exc}")


def session_isolation_test() -> None:
    name = "Session isolation (partner sees sessions, associate does not see partner's)"
    try:
        partner_resp = SESSION.get(f"{BASE_URL}/sessions", headers=bearer("partner"))
        assoc_resp = SESSION.get(f"{BASE_URL}/sessions", headers=bearer("assoc"))

        if partner_resp.status_code != 200 or assoc_resp.status_code != 200:
            record(
                name,
                False,
                f"partner_status={partner_resp.status_code} assoc_status={assoc_resp.status_code}",
            )
            return

        partner_sessions = partner_resp.json().get("sessions", [])
        assoc_sessions = assoc_resp.json().get("sessions", [])

        if not isinstance(partner_sessions, list) or not isinstance(assoc_sessions, list):
            record(name, False, "sessions payload is not a list")
            return

        partner_ids = {s.get("session_id") for s in partner_sessions if s.get("session_id")}
        assoc_ids = {s.get("session_id") for s in assoc_sessions if s.get("session_id")}

        leak = partner_ids & assoc_ids
        if partner_session_id and partner_session_id not in partner_ids:
            record(name, False, f"partner's own session_id={partner_session_id} not in partner list")
        elif leak:
            record(name, False, f"session leak: associate sees {sorted(leak)}")
        else:
            record(name, True, f"partner has {len(partner_sessions)} sessions, assoc has {len(assoc_sessions)}, no overlap")
    except Exception as exc:  # noqa: BLE001
        record(name, False, f"exception: {exc}")


def admin_access_test() -> None:
    try:
        admin_resp = SESSION.get(f"{BASE_URL}/admin/users", headers=bearer("admin"))
        partner_resp = SESSION.get(f"{BASE_URL}/admin/users", headers=bearer("partner"))

        ok_admin = admin_resp.status_code == 200
        ok_partner = partner_resp.status_code == 403

        if ok_admin and ok_partner:
            record("Admin endpoint: admin 200, partner 403", True, f"admin={admin_resp.status_code}, partner={partner_resp.status_code}")
        else:
            record(
                "Admin endpoint: admin 200, partner 403",
                False,
                f"admin={admin_resp.status_code} (want 200), partner={partner_resp.status_code} (want 403)",
            )
    except Exception as exc:  # noqa: BLE001
        record("Admin endpoint: admin 200, partner 403", False, f"exception: {exc}")


def upload_access_test() -> None:
    content = b"This is a test document"
    filename = "test.txt"

    try:
        # Partner uploads to M-2024-118 with access_level=5 (capped server-side).
        r1 = SESSION.post(
            f"{BASE_URL}/documents/upload",
            files={"file": (filename, content, "text/plain")},
            data={"matter_id": "M-2024-118", "access_level": "5"},
            headers=bearer("partner"),
        )
        if r1.status_code == 200:
            level = r1.json().get("access_level")
            record(
                "Partner upload M-2024-118 (200, level capped <=4)",
                level is not None and level <= 4,
                f"status={r1.status_code} access_level={level}",
            )
        else:
            record("Partner upload M-2024-118 (200, level capped <=4)", False, f"status={r1.status_code} body={r1.text[:200]}")

        # Associate uploads to same matter -> 403.
        r2 = SESSION.post(
            f"{BASE_URL}/documents/upload",
            files={"file": (filename, content, "text/plain")},
            data={"matter_id": "M-2024-118", "access_level": "2"},
            headers=bearer("assoc"),
        )
        record(
            "Associate upload M-2024-118 (403)",
            r2.status_code == 403,
            f"status={r2.status_code}",
        )

        # Partner uploads to non-administered matter -> 403.
        r3 = SESSION.post(
            f"{BASE_URL}/documents/upload",
            files={"file": (filename, content, "text/plain")},
            data={"matter_id": "M-2025-001", "access_level": "4"},
            headers=bearer("partner"),
        )
        record(
            "Partner upload M-2025-001 (403)",
            r3.status_code == 403,
            f"status={r3.status_code}",
        )
    except Exception as exc:  # noqa: BLE001
        record("Upload access control", False, f"exception: {exc}")


def feedback_ownership_test() -> None:
    name_own = "Feedback as partner on own session (200/201)"
    name_foreign = "Feedback as associate on partner's session (404)"

    if not partner_session_id:
        record(name_own, False, "no partner session_id available")
        record(name_foreign, False, "no partner session_id available")
        return

    body = {
        "session_id": partner_session_id,
        "query": "What is data breach liability under Kenyan law?",
        "answer_excerpt": "test",
        "rating": 1,
        "comment": "integration test feedback",
    }
    try:
        own = SESSION.post(f"{BASE_URL}/feedback", json=body, headers=bearer("partner"))
        record(name_own, own.status_code in (200, 201), f"status={own.status_code}")

        foreign = SESSION.post(f"{BASE_URL}/feedback", json=body, headers=bearer("assoc"))
        record(name_foreign, foreign.status_code == 404, f"status={foreign.status_code}")
    except Exception as exc:  # noqa: BLE001
        record(name_own, False, f"exception: {exc}")
        record(name_foreign, False, f"exception: {exc}")


TESTS: list[tuple[str, Callable[[], None]]] = [
    ("Login", login_test),
    ("Chat session creation", chat_session_test),
    ("Session isolation", session_isolation_test),
    ("Admin endpoint access", admin_access_test),
    ("Upload access control", upload_access_test),
    ("Feedback ownership", feedback_ownership_test),
]


def main() -> None:
    print(f"Target: {BASE_URL}")
    for name, fn in TESTS:
        print(f"\n--- {name} ---")
        try:
            fn()
        except Exception:  # noqa: BLE001
            record(f"{name} (unexpected error)", False, traceback.format_exc().splitlines()[-1])

    passed = sum(1 for _, ok, _ in results if ok)
    failed = len(results) - passed

    print("\n" + "=" * 60)
    print("SUMMARY")
    print(f"Total: {len(results)}   Passed: {passed}   Failed: {failed}")
    for name, ok, detail in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))
    print("=" * 60)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()