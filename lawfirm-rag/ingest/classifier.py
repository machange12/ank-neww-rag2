from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from providers import make_chat_llm

logger = logging.getLogger(__name__)

ENTITY_TEXT_LIMIT = 6000
# doc_type/jurisdiction/language are reliably inferable from a document's
# opening section (title, heading, first few paragraphs) — no need to send
# the whole document. Capped so classification doesn't blow past small
# per-minute token limits (e.g. Groq's on_demand tier default TPM caps) on
# long judgments/statutes, which previously made classify_document() fail
# outright on any document over a few thousand tokens.
CLASSIFY_TEXT_LIMIT = 6000

DEFAULT_CLASSIFICATION = {
    "doc_type": "unknown",
    "jurisdiction": "Unknown",
    "language": "English",
}

ALLOWED_DOC_TYPES = {
    "judgment",
    "statute",
    "contract",
    "pleading",
    "legal_memo",
    "correspondence",
    "due_diligence",
    "precedent",
    "unknown",
}

CLASSIFY_SYSTEM_PROMPT = (
    "You are a legal document classifier for a Kenyan law firm. "
    "Classify the document and reply ONLY with a JSON object — no markdown, no preamble. "
    'Schema: {"doc_type": string, "jurisdiction": string, "language": string}\n\n'
    "doc_type must be exactly one of:\n"
    '- "judgment" — court decisions, rulings, orders\n'
    '- "statute" — acts, regulations, statutory instruments, legal notices\n'
    '- "contract" — agreements, NDAs, leases, employment contracts, SPA, SHA\n'
    '- "pleading" — plaints, defences, affidavits, submissions, petitions\n'
    '- "legal_memo" — internal memos, opinions, research notes\n'
    '- "correspondence" — letters, emails, client communications\n'
    '- "due_diligence" — DD reports, checklists, review summaries\n'
    '- "precedent" — template documents, standard forms\n'
    '- "unknown" — anything else\n\n'
    'jurisdiction: e.g. "Kenya", "East Africa", "England and Wales", "Unknown"\n'
    'language: e.g. "English", "Swahili", "Mixed"'
)

ENTITY_SYSTEM_PROMPTS: dict[str, str] = {
    "judgment": (
        "Extract structured legal metadata from this court judgment. "
        "Reply ONLY with JSON, no markdown. "
        'Schema: {"court": string, "case_number": string, "parties": [string], '
        '"judges": [string], "date_of_judgment": string, "outcome": string, '
        '"cited_statutes": [string], "cited_cases": [string], '
        '"legal_issues": [string], "matter_type": string}\n'
        "court: e.g. \"High Court of Kenya\", \"Court of Appeal\". "
        "case_number: e.g. \"Civil Case No. 123 of 2021\". "
        'outcome: one of "allowed", "dismissed", "settled", "varied", "". '
        "date_of_judgment: ISO date or \"\" if unknown. "
        'matter_type: one of "civil", "criminal", "constitutional", "commercial", '
        '"employment", "family", "land".'
    ),
    "contract": (
        "Extract structured legal metadata from this contract or agreement. "
        "Reply ONLY with JSON, no markdown. "
        'Schema: {"parties": [string], "contract_type": string, "governing_law": string, '
        '"effective_date": string, "expiry_date": string, "key_clauses": [string], '
        '"currency": string, "deal_value": string}\n'
        'contract_type: one of "NDA", "Employment", "Lease", "SPA", "SHA", '
        '"Service Agreement", "Loan", "Other". '
        "effective_date/expiry_date: ISO date or \"\" if unknown. "
        "currency: e.g. \"KES\", \"USD\", or \"\" if not mentioned. "
        'deal_value: e.g. "KES 50,000,000" or "" if not mentioned.'
    ),
    "statute": (
        "Extract structured legal metadata from this statute or regulation. "
        "Reply ONLY with JSON, no markdown. "
        'Schema: {"act_name": string, "chapter_number": string, "year_enacted": string, '
        '"jurisdiction": string, "subject_matter": [string]}\n'
        'chapter_number: e.g. "Cap. 486" or "". '
        'year_enacted: e.g. "2015" or "". '
        'jurisdiction: e.g. "Kenya", "East Africa". '
        'subject_matter: e.g. ["company law", "insolvency", "securities"].'
    ),
    "pleading": (
        "Extract structured legal metadata from this court pleading. "
        "Reply ONLY with JSON, no markdown. "
        'Schema: {"court": string, "case_number": string, "parties": [string], '
        '"pleading_type": string, "date_filed": string, "relief_sought": [string]}\n'
        'pleading_type: one of "Plaint", "Defence", "Affidavit", "Petition", '
        '"Submission", "Reply". date_filed: ISO date or "" if unknown.'
    ),
}

GENERIC_ENTITY_PROMPT = (
    "Extract structured legal metadata from this legal document. "
    "Reply ONLY with JSON, no markdown. "
    'Schema: {"parties": [string], "subject": string, "date": string, '
    '"key_topics": [string]}\n'
    'date: ISO date or "" if unknown.'
)


def _make_llm() -> Any:
    """Construct the classification LLM via the shared provider factory.

    Was a hardcoded ChatOpenAI(api_key=settings.openai_api_key) — deployments
    that only configure GROQ_API_KEY (no OPENAI_API_KEY) had no valid
    credentials here, so every call failed auth and silently degraded to the
    "unknown" doc_type default (caught by the broad except in
    classify_document/extract_legal_entities below). make_chat_llm() applies
    the same Groq-then-OpenAI provider selection already used for chat.
    """
    return make_chat_llm()


def _parse_json(content: str) -> dict[str, Any]:
    """Best-effort extraction of a JSON object from an LLM string response."""
    if not isinstance(content, str):
        content = str(content)
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if not match:
        raise ValueError("no JSON object found in LLM response")
    parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("LLM response JSON is not an object")
    return parsed


def classify_document(
    text: str,
    file_title: str,
    llm: Any | None = None,
) -> dict[str, str]:
    """
    Classify an ingested document's type, jurisdiction and language.

    Uses one cheap LLM call (gpt-4o-mini). The response is parsed defensively —
    any parse failure degrades safely to {"doc_type": "unknown",
    "jurisdiction": "Unknown", "language": "English"} so ingest never breaks.
    """
    if llm is None:
        llm = _make_llm()
    sample = text[:CLASSIFY_TEXT_LIMIT]
    human = HumanMessage(content=f"Document title: {file_title}\n\nDocument text:\n{sample}")
    try:
        response = llm.invoke([SystemMessage(content=CLASSIFY_SYSTEM_PROMPT), human])
        result = _parse_json(getattr(response, "content", ""))
        doc_type = str(result.get("doc_type") or "unknown").strip().lower()
        if doc_type not in ALLOWED_DOC_TYPES:
            doc_type = "unknown"
        return {
            "doc_type": doc_type,
            "jurisdiction": str(result.get("jurisdiction") or "Unknown").strip() or "Unknown",
            "language": str(result.get("language") or "English").strip() or "English",
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("classify_document failed, using defaults: %s", exc)
        return dict(DEFAULT_CLASSIFICATION)


def extract_legal_entities(
    text: str,
    doc_type: str,
    llm: Any | None = None,
) -> dict[str, Any]:
    """
    Extract structured legal entities from the full document text.

    The prompt is doc_type-aware (judgment/contract/statute/pleading get tailored
    schemas; everything else gets a generic parties/subject/date/key_topics
    schema). Only the first ``ENTITY_TEXT_LIMIT`` characters are sent to the LLM
    so the call stays cheap for every ingest. Any failure returns ``{}`` so the
    caller can continue with empty metadata.
    """
    if llm is None:
        llm = _make_llm()
    system_prompt = ENTITY_SYSTEM_PROMPTS.get(doc_type, GENERIC_ENTITY_PROMPT)
    sample = text[:ENTITY_TEXT_LIMIT]
    human = HumanMessage(content=sample)
    try:
        response = llm.invoke([SystemMessage(content=system_prompt), human])
        return _parse_json(getattr(response, "content", ""))
    except Exception as exc:  # noqa: BLE001
        logger.warning("extract_legal_entities failed for doc_type=%s: %s", doc_type, exc)
        return {}