from unittest.mock import MagicMock

from cryptography.hazmat.primitives.asymmetric import ec
from jose import jwk, jwt

import auth.jwt_validator as jv
from agents.rag_agent import _FailOpenRerank


def test_es256_token_verifies_locally(monkeypatch):
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = jwk.construct(priv.public_key(), "ES256").to_dict()
    monkeypatch.setattr(jv, "_jwks_key", lambda kid: pub if kid == "k1" else None)
    tok = jwt.encode({"sub": "u1", "exp": 9999999999}, priv, algorithm="ES256", headers={"kid": "k1"})
    assert jv.verify_jwt(tok)["sub"] == "u1"
    forged = jwt.encode({"sub": "u1"}, ec.generate_private_key(ec.SECP256R1()), algorithm="ES256", headers={"kid": "k1"})
    try:
        jv.verify_jwt(forged)
        raise AssertionError("forged token accepted")
    except jv.AuthError:
        pass


def test_rerank_failure_skips_api_for_a_while():
    inner = MagicMock()
    inner.compress_documents.side_effect = TimeoutError("hung")
    r = _FailOpenRerank(inner=inner, top_n=2)
    assert r.compress_documents([1, 2, 3], "q") == [1, 2]
    assert r.compress_documents([1, 2, 3], "q") == [1, 2]
    assert inner.compress_documents.call_count == 1


def test_answer_cache_ttl_and_keys(monkeypatch):
    from langchain_core.messages import AIMessage, HumanMessage

    import agents.rag_agent as ra

    c = ra._TTLCache(ttl=10, maxsize=2)
    now = [100.0]
    monkeypatch.setattr(ra.time, "monotonic", lambda: now[0])
    c.put("a", 1)
    assert c.get("a") == 1
    now[0] += 11
    assert c.get("a") is None  # expired
    c.put("a", 1); c.put("b", 2); c.put("c", 3)
    assert c.get("a") is None and c.get("c") == 3  # oldest evicted at maxsize

    hist = [HumanMessage(content="q1"), AIMessage(content="a1")]
    # per user, whitespace/case-insensitive, and conversation-specific
    assert ra._answer_cache_key("u1", "Who  sued?", []) == ra._answer_cache_key("u1", "who sued?", [])
    assert ra._answer_cache_key("u1", "who sued?", []) != ra._answer_cache_key("u2", "who sued?", [])
    assert ra._answer_cache_key("u1", "who sued?", []) != ra._answer_cache_key("u1", "who sued?", hist)


def test_short_follow_up_searches_with_previous_question():
    from langchain_core.messages import AIMessage, HumanMessage

    from agents.rag_agent import _retrieval_query

    hist = [HumanMessage(content="What orders did the judge make?"), AIMessage(content="Which case do you mean?")]
    assert _retrieval_query("the Ngeno estate", hist) == "What orders did the judge make?\nthe Ngeno estate"
    assert _retrieval_query("the Ngeno estate", []) == "the Ngeno estate"
    long_q = "What did the court decide about the distribution of the estate in Ngeno?"
    assert _retrieval_query(long_q, hist) == long_q
