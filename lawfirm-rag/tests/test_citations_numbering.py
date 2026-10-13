from langchain_core.documents import Document

from agents.rag_agent import _build_context_string, _sources_from_docs, _strip_invalid_citations


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


def test_invalid_citation_numbers_are_removed():
    cleaned, removed = _strip_invalid_citations("Rent is KES 280,000 [1]. Notice is 6 months [4][2].", 2)
    assert cleaned == "Rent is KES 280,000 [1]. Notice is 6 months [2]."
    assert removed == 1
