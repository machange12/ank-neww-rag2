from langchain_core.documents import Document

from agents.rag_agent import _clarifying_question, _pretty_title

NGENO = "Judgments/2025-kehc-16269-estate-of-ngeno.pdf"
TVA = "Judgments/2026-kehc-12900-tv-africa-v-ca.pdf"
MUT = "Judgments/2026-kehc-12951-mutunga-v-republic.pdf"


def docs(*titles):
    return [Document(page_content="x", metadata={"file_title": t}) for t in titles]


def test_pretty_title():
    assert _pretty_title(MUT) == "Mutunga v Republic [2026 KEHC 12951]"
    assert _pretty_title("08_Commercial_Lease_Agreement.docx") == "Commercial Lease Agreement"
    assert _pretty_title("doc6_nda.pdf") == "NDA"


def test_asks_when_unnamed_reference_spans_documents():
    q = _clarifying_question("What orders did the judge make?", docs(NGENO, NGENO, "2025-kehc-16269-estate-of-ngeno.pdf", TVA))
    assert q and "Estate of Ngeno [2025 KEHC 16269]" in q and "TV Africa v CA" in q
    assert q.count("Ngeno") == 1  # same document under two paths listed once


def test_does_not_ask_when_document_is_named():
    assert _clarifying_question("Who were the parties in Mutunga v Republic?", docs(MUT, TVA)) is None
    assert _clarifying_question("What orders did the judge make in the Ngeno estate?", docs(NGENO, TVA)) is None


def test_does_not_ask_for_general_or_single_document_questions():
    assert _clarifying_question("What is the limitation period for appeals?", docs(MUT, TVA)) is None
    assert _clarifying_question("What orders did the judge make?", docs(NGENO, NGENO)) is None


def test_does_not_ask_when_user_names_an_unknown_document():
    q = "What did the court decide in the Mombasa port arbitration between Bahari Shipping and the Ports Authority?"
    assert _clarifying_question(q, docs(NGENO, TVA)) is None


def test_synthetic_prefix_stripped_from_titles():
    assert _pretty_title("synth_02_otieno_v_lakeside_logistics.txt") == "Otieno v Lakeside Logistics"


def test_named_document_chunks_take_priority_over_reranker():
    from agents.rag_agent import _prefer_named_document

    def chunk(title, i):
        return Document(page_content=f"{title} chunk {i}", metadata={"file_id": title, "file_title": title, "chunk_index": i})

    CHEBET, MUNG = "synth_03_chebet_v_republic.txt", "Judgments/2026-kehc-12982-mungathia-v-republic.pdf"
    candidates = [chunk(CHEBET, 0), chunk(CHEBET, 3), chunk(CHEBET, 2), chunk(CHEBET, 1), chunk(MUNG, 6)]
    reranked = [chunk(CHEBET, 0), chunk(MUNG, 6), chunk(MUNG, 0), chunk(MUNG, 9), chunk(MUT, 0)]
    out = _prefer_named_document("What was the outcome of Chebet v Republic?", candidates, reranked, 5)
    assert [d.metadata["file_id"] for d in out[:4]] == [CHEBET] * 4  # disposition chunk 3 now included
    assert {d.metadata["chunk_index"] for d in out[:4]} == {0, 1, 2, 3}
    # no document named -> reranker order unchanged
    assert _prefer_named_document("What is the limitation period?", candidates, reranked, 5) == reranked


def test_years_do_not_count_as_naming_a_document():
    from agents.rag_agent import _query_names_title

    assert not _query_names_title("Mary Wanjiku v ABC Contractors (HC-245-2026) damages?", "Mwarua v Pandya [2026 KEHC 12838]")
    assert _query_names_title("What did the court hold in Mwarua v Pandya?", "Mwarua v Pandya [2026 KEHC 12838]")
