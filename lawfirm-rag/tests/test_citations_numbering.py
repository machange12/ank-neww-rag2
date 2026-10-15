import time

from langchain_core.documents import Document

from agents.rag_agent import (
    _build_context_string,
    _finish_turn,
    _is_no_answer,
    _sources_from_docs,
    _strip_invalid_citations,
    _strip_markdown,
)


def _doc(fid, title, text, section=""):
    return Document(page_content=text, metadata={"file_id": fid, "file_title": title, "section_heading": section})


def test_context_numbers_match_sources_list():
    docs = [
        _doc("f1", "synth_04_westlands_commercial_lease.txt", "rent 280,000", "3. RENT"),
        _doc("f2", "Judgments/2026-kehc-12951-mutunga-v-republic.pdf", "appellant"),
        _doc("f1", "synth_04_westlands_commercial_lease.txt", "service charge 45,000"),
    ]
    sources = _sources_from_docs(docs)
    assert [(s["n"], s["title"]) for s in sources] == [
        (1, "Westlands Commercial Lease"),
        (2, "Mutunga v Republic [2026 KEHC 12951]"),
    ]
    ctx = _build_context_string(docs, sources)
    # both lease chunks carry [1]; the judgment carries [2]
    assert ctx.count("[1] Westlands Commercial Lease") == 2
    assert "[2] Mutunga v Republic" in ctx and "| 3. RENT" in ctx


def test_native_gpt_oss_citations_become_numbers():
    from agents.rag_agent import _normalize_citations

    assert _normalize_citations("died on 14 March 2019【1†L1-L3】 and 【2】.") == "died on 14 March 2019[1] and [2]."


def test_invalid_citation_numbers_are_removed():
    cleaned, removed = _strip_invalid_citations("Rent is KES 280,000 [1]. Notice is 6 months [4][2].", 2)
    assert cleaned == "Rent is KES 280,000 [1]. Notice is 6 months [2]."
    assert removed == 1


def test_is_no_answer_matches_canonical_opening():
    assert _is_no_answer("I could not find an answer in the firm's document repository.")
    assert _is_no_answer("  i could not find an answer in the firm's document repository. Try...")
    # A partial answer that happens to mention the phrase is NOT a no-answer.
    assert not _is_no_answer("Rent was 280,000 [1]. I could not find the notice period.")


def test_strip_markdown_removes_symbols():
    raw = "## Summary\n\n- **Rent** is KES 280,000 [1]\n- *Notice* is 6 months"
    cleaned = _strip_markdown(raw)
    assert "**" not in cleaned and "*" not in cleaned
    assert "#" not in cleaned
    assert not cleaned.lstrip().startswith("-")
    assert "Rent is KES 280,000 [1]" in cleaned


def _turn(sources):
    return {
        "sources": sources,
        "persist": lambda _answer: None,
        "cache_key": ("u", "q", ()),
        "t0": time.perf_counter(),
        "t_retrieved": time.perf_counter(),
    }


def test_no_answer_reply_drops_citations_and_sources():
    turn = _turn([{"n": 1, "title": "A", "key": "a"}, {"n": 2, "title": "B", "key": "b"}])
    result = _finish_turn("s1", turn, "I could not find an answer in the firm's document repository. [1][2]")
    assert "[1]" not in result["answer"] and "[2]" not in result["answer"]
    assert result["sources"] == []


def test_normal_answer_is_markdown_stripped_and_cited():
    turn = _turn([{"n": 1, "title": "A", "key": "a"}])
    result = _finish_turn("s1", turn, "**Rent** is KES 280,000 [1].")
    assert result["answer"] == "Rent is KES 280,000 [1]."
    assert len(result["sources"]) == 1
