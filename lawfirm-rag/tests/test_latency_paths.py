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
