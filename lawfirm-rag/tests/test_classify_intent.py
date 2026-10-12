from unittest.mock import MagicMock, patch

import pytest
from langchain_core.documents import Document

from agents.rag_agent import HybridRetriever, classify_intent


@pytest.mark.parametrize("query, intent, k", [
    ("What did the court hold in Wandi v Muchira?", "factual", 8),
    ("Compare the two succession rulings", "comparative", 15),
    ("Linganisha maamuzi haya mawili", "comparative", 15),
    ("Summarise the facts of the case", "summarization", 20),
    ("Nipe muhtasari wa kesi hii", "summarization", 20),
    ("How do I file a succession petition?", "procedural", 10),
])
def test_intent_and_depth(query, intent, k):
    out = classify_intent(query)
    assert (out["intent"], out["retrieve_k"]) == (intent, k)


@pytest.mark.parametrize("query, hint", [
    ("summarise the facts", None),                 # "act" inside "facts" must not match
    ("terms of the contract", "contract"),         # "act" inside "contract" must not match
    ("Wandi v Muchira", "judgment"),
    ("Section 35 of the Law of Succession Act", "statute"),
    ("who showcased evidence", None),              # "case" inside "showcased"
])
def test_doc_type_hint_whole_words(query, hint):
    assert classify_intent(query)["doc_type_hint"] == hint


def test_empty_filtered_search_retries_unfiltered():
    doc = Document(page_content="s.35 Law of Succession Act applied in a judgment")
    calls = []

    def fake_search(client, query, match_count, doc_type_filter):
        calls.append(doc_type_filter)
        return [] if doc_type_filter else [doc]

    r = HybridRetriever(user_client=MagicMock(), store=MagicMock(), k=8, doc_type_filter="statute")
    with patch("agents.rag_agent.hybrid_search", fake_search):
        assert r.invoke("section 35") == [doc]
    assert calls == ["statute", None]
    assert r.last_path == "hybrid_unfiltered"
