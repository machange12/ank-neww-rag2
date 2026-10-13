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
