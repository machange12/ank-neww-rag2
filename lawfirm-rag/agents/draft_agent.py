"""Grounded legal drafting pipeline ("Draft Studio").

Reuses the chat RAG core (retrieval chain, citation guards, LLM factory) so a
draft is grounded in exactly the same authorized corpus — plus the curated
clause bank — as a chat answer. The load-bearing safety property is the
per-section no-source guard: if nothing supports a section, the section is
returned as ``needs_source`` and the LLM is never asked to fill the gap from
general knowledge. See docs/decisions/0006-drafting-assistant.md.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from agents.rag_agent import (
    _build_context_string,
    _make_retriever,
    _normalize_citations,
    _sources_from_docs,
    _strip_invalid_citations,
    _strip_markdown,
)
from search.clauses import search_clauses

logger = logging.getLogger(__name__)

# Retrieval depth per section. Smaller than a broad chat query: a section
# needs the few most on-point precedents/clauses, not corpus-wide recall.
SECTION_RETRIEVE_K = 8
CLAUSE_RETRIEVE_K = 6

NO_SOURCE_MARKER = "NO SOURCE"
NO_SOURCE_SECTION = (
    "NO SOURCE: the firm's document repository and clause bank contain nothing "
    "that supports this section. Add a source document or clause, or draft this "
    "section manually."
)

DRAFT_SECTION_TEMPLATE = """You are a legal drafting assistant for Anjarwalla & Khanna Advocates.

You are drafting ONE section of a legal document.
Document type: {doc_type}
Section heading: {heading}
Drafter's instructions: {brief}

RULES:
1. Draft ONLY this section, in formal legal prose.
2. Use ONLY the retrieved firm sources below. Never invent a clause, statutory reference, party name, defined term, date or figure that is not in the sources or the drafter's instructions.
3. Cite every statement drawn from a source with its number in square brackets, e.g. [1] or [2][3], using ONLY the numbers shown in the sources. Never cite a file name or a number that is not shown.
4. If the sources do not support this section, reply with exactly this and nothing else: "{no_source}"
5. Plain text only — no markdown, no bullet points, no headings. Write prose paragraphs.
6. Write in the same language as the drafter's instructions (English or Swahili).

RETRIEVED SOURCES:
{context}
"""

DRAFT_SKELETON_TEMPLATE = """You are a legal drafting assistant for Anjarwalla & Khanna Advocates.
Propose the section outline for a "{doc_type}" document titled "{title}".
Drafter's brief: {brief}

Return ONLY a JSON array of objects, each shaped exactly like:
{{"heading": "string", "clause_type": "string", "guidance": "string", "required": true}}
Use 4-10 sections appropriate to this document type under Kenyan practice.
Output the JSON array and nothing else — no prose, no markdown fences."""

REFINE_SECTION_TEMPLATE = """You are a legal drafting assistant for Anjarwalla & Khanna Advocates.
Revise the section below according to the instruction.

RULES:
1. Do NOT introduce facts, clauses, references or defined terms that are not already in the section.
2. Keep every [n] citation that remains accurate and do not invent new ones.
3. Return only the revised section text, as plain prose, no markdown.

Section heading: {heading}
Instruction: {instruction}

Current text:
{content}
"""

PLAYBOOK_CHECK_TEMPLATE = """You are a legal drafting assistant for Anjarwalla & Khanna Advocates.
Check the draft sections below against each firm review rule.

RULES (use the id shown for each, exactly):
{rules}

DRAFT SECTIONS:
{sections}

Return ONLY a JSON array, one object per rule, each shaped exactly like:
{{"rule_id": "string", "order_index": 0, "status": "ok", "note": "string"}}
- "order_index" is the section number that the rule bears on, or null if none.
- "status" is "ok" if the draft complies with the rule, "breach" if it violates
  it, or "unclear" if the sections do not allow a determination.
- "note" is one short sentence explaining the verdict.
Output the JSON array and nothing else — no prose, no markdown fences."""

# Fallback skeletons used when no curated template exists and the skeleton LLM
# call fails or returns unusable output. Deliberately generic and clause-typed.
DEFAULT_SKELETONS: dict[str, list[dict[str, Any]]] = {
    "nda": [
        {"heading": "Parties", "clause_type": "parties", "guidance": "Identify the parties and their roles.", "required": True},
        {"heading": "Definitions", "clause_type": "definitions", "guidance": "Define confidential information and key terms.", "required": True},
        {"heading": "Confidentiality Obligations", "clause_type": "confidentiality", "guidance": "Obligations of the receiving party.", "required": True},
        {"heading": "Permitted Disclosures", "clause_type": "permitted_disclosure", "guidance": "Carve-outs where disclosure is allowed.", "required": False},
        {"heading": "Term and Termination", "clause_type": "term", "guidance": "Duration of the confidentiality obligation.", "required": True},
        {"heading": "Governing Law and Dispute Resolution", "clause_type": "governing_law", "guidance": "Governing law and forum.", "required": True},
    ],
    "lease": [
        {"heading": "Parties and Premises", "clause_type": "parties", "guidance": "Landlord, tenant and the demised premises.", "required": True},
        {"heading": "Term and Rent", "clause_type": "term", "guidance": "Lease term, rent and payment terms.", "required": True},
        {"heading": "Tenant Obligations", "clause_type": "tenant_obligations", "guidance": "Repair, use and compliance obligations.", "required": True},
        {"heading": "Landlord Obligations", "clause_type": "landlord_obligations", "guidance": "Quiet enjoyment, repairs, insurance.", "required": True},
        {"heading": "Termination and Default", "clause_type": "termination", "guidance": "Forfeiture, breach and remedies.", "required": True},
        {"heading": "Governing Law", "clause_type": "governing_law", "guidance": "Governing law and jurisdiction.", "required": True},
    ],
    "engagement_letter": [
        {"heading": "Engagement and Scope", "clause_type": "scope", "guidance": "Scope of legal services.", "required": True},
        {"heading": "Fees and Billing", "clause_type": "fees", "guidance": "Fee basis, disbursements and billing.", "required": True},
        {"heading": "Client Obligations", "clause_type": "client_obligations", "guidance": "Instructions, cooperation and information.", "required": True},
        {"heading": "Conflicts and Confidentiality", "clause_type": "confidentiality", "guidance": "Confidentiality and conflict handling.", "required": True},
        {"heading": "Termination", "clause_type": "termination", "guidance": "Termination and effect.", "required": True},
        {"heading": "Governing Law", "clause_type": "governing_law", "guidance": "Governing law.", "required": True},
    ],
    "memo": [
        {"heading": "Issue", "clause_type": "issue", "guidance": "The legal question presented.", "required": True},
        {"heading": "Brief Answer", "clause_type": "brief_answer", "guidance": "Short conclusion.", "required": True},
        {"heading": "Facts", "clause_type": "facts", "guidance": "Material facts.", "required": True},
        {"heading": "Discussion", "clause_type": "discussion", "guidance": "Analysis grounded in the sources.", "required": True},
        {"heading": "Conclusion", "clause_type": "conclusion", "guidance": "Recommendation.", "required": True},
    ],
}

GENERIC_SKELETON: list[dict[str, Any]] = [
    {"heading": "Parties", "clause_type": "parties", "guidance": "Identify the parties.", "required": True},
    {"heading": "Background and Purpose", "clause_type": "recitals", "guidance": "Recitals and purpose.", "required": False},
    {"heading": "Operative Provisions", "clause_type": "operative", "guidance": "Core obligations and rights.", "required": True},
    {"heading": "Term and Termination", "clause_type": "termination", "guidance": "Duration and termination.", "required": False},
    {"heading": "Governing Law and Dispute Resolution", "clause_type": "governing_law", "guidance": "Governing law and forum.", "required": True},
]


def default_skeleton(doc_type: str) -> list[dict[str, Any]]:
    """Deterministic fallback skeleton for a document type."""
    key = (doc_type or "").strip().lower().replace(" ", "_")
    base = DEFAULT_SKELETONS.get(key, GENERIC_SKELETON)
    return [dict(section) for section in base]


def format_brief(brief: Any) -> str:
    """Render a brief (dict of key terms, or a string) as readable text."""
    if brief is None:
        return ""
    if isinstance(brief, str):
        return brief.strip()
    if isinstance(brief, dict):
        lines = []
        for key, value in brief.items():
            if value in (None, "", [], {}):
                continue
            if isinstance(value, (list, tuple)):
                value = ", ".join(str(v) for v in value)
            lines.append(f"{str(key).replace('_', ' ').strip()}: {value}")
        return "\n".join(lines)
    return str(brief)


def _coerce_skeleton(items: Any, fallback: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return fallback
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        heading = str(item.get("heading") or "").strip()
        if not heading:
            continue
        out.append({
            "heading": heading,
            "clause_type": str(item.get("clause_type") or "").strip(),
            "guidance": str(item.get("guidance") or "").strip(),
            "required": bool(item.get("required", True)),
        })
    return out or fallback


def parse_skeleton(raw_text: str, fallback: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract a JSON array of sections from an LLM reply; else the fallback.

    Tolerates prose before/after the array and markdown code fences.
    """
    if not raw_text:
        return fallback
    start = raw_text.find("[")
    end = raw_text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return fallback
    try:
        parsed = json.loads(raw_text[start : end + 1])
    except (ValueError, TypeError):
        return fallback
    return _coerce_skeleton(parsed, fallback)


def build_section_query(section: dict[str, Any], brief_text: str) -> str:
    """Search text for one section: heading + clause type + guidance + brief."""
    parts = [
        str(section.get("heading") or "").strip(),
        str(section.get("clause_type") or "").strip().replace("_", " "),
        str(section.get("guidance") or "").strip(),
        brief_text.strip(),
    ]
    return "\n".join(part for part in parts if part).strip()


def is_no_source_answer(content: str) -> bool:
    return content.strip().upper().startswith(NO_SOURCE_MARKER)


async def propose_skeleton(
    doc_type: str,
    title: str,
    brief: Any,
    user_client: Any,
    llm: Any,
    template_skeleton: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return a section outline. A curated template's skeleton always wins."""
    fallback = default_skeleton(doc_type)
    if template_skeleton:
        coerced = _coerce_skeleton(template_skeleton, fallback)
        if coerced:
            return coerced
    try:
        prompt = DRAFT_SKELETON_TEMPLATE.format(
            doc_type=doc_type or "legal document",
            title=title or "Untitled",
            brief=format_brief(brief) or "(none)",
        )
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        raw = getattr(response, "content", "") or ""
        return parse_skeleton(raw, fallback)
    except Exception as exc:  # noqa: BLE001
        logger.warning("skeleton generation failed, using default | doc_type=%s | %s", doc_type, exc)
        return fallback


async def _prepare_section(
    section: dict[str, Any],
    brief_text: str,
    doc_type: str,
    user_client: Any,
    llm: Any,
) -> dict[str, Any]:
    """Retrieve grounding for one section.

    Returns ``{"final": {...}}`` when nothing supports the section (the
    no-source guard), otherwise the prompt messages + numbered sources.
    """
    query = build_section_query(section, brief_text)
    docs: list[Any] = []
    try:
        retriever, _hybrid = _make_retriever(user_client, llm, k=SECTION_RETRIEVE_K, use_multi_query=False)
        result = await retriever.ainvoke(query)
        if isinstance(result, list):
            docs = result
    except Exception as exc:  # noqa: BLE001
        logger.warning("draft section retrieval failed | section=%r | %s", section.get("heading"), exc)

    clauses = search_clauses(user_client, query, match_count=CLAUSE_RETRIEVE_K, doc_type_filter=doc_type or None)
    combined = [*docs, *clauses]

    if not combined:
        logger.info("draft section has no grounding | section=%r", section.get("heading"))
        return {"final": {"content": NO_SOURCE_SECTION, "sources": [], "status": "needs_source"}}

    sources = _sources_from_docs(combined)
    messages = [
        SystemMessage(content=DRAFT_SECTION_TEMPLATE.format(
            doc_type=doc_type or "legal document",
            heading=section.get("heading") or "",
            brief=brief_text or "(none)",
            no_source=NO_SOURCE_SECTION,
            context=_build_context_string(combined, sources),
        )),
        HumanMessage(content=query),
    ]
    return {"messages": messages, "sources": sources}


def _finish_section(content: str, sources: list[dict[str, Any]]) -> dict[str, Any]:
    content = _normalize_citations(content)
    if is_no_source_answer(content):
        return {"content": NO_SOURCE_SECTION, "sources": [], "status": "needs_source"}
    content = _strip_markdown(content)
    content, bad = _strip_invalid_citations(content, len(sources))
    if bad:
        logger.warning("draft section: removed %d invalid citation(s) | section sources=%d", bad, len(sources))
    return {"content": content, "sources": sources, "status": "generated"}


async def generate_section(
    section: dict[str, Any],
    brief_text: str,
    doc_type: str,
    user_client: Any,
    llm: Any,
) -> dict[str, Any]:
    """Generate one grounded section (non-streaming)."""
    prepared = await _prepare_section(section, brief_text, doc_type, user_client, llm)
    if "final" in prepared:
        return prepared["final"]
    response = await llm.ainvoke(prepared["messages"])
    content = getattr(response, "content", "") or ""
    return _finish_section(content, prepared["sources"])


async def stream_generate_section(
    section: dict[str, Any],
    brief_text: str,
    doc_type: str,
    user_client: Any,
    llm: Any,
):
    """Stream one grounded section.

    Yields ``{"type": "token", "text": ...}`` then a final
    ``{"type": "result", "content", "sources", "status"}``.
    """
    prepared = await _prepare_section(section, brief_text, doc_type, user_client, llm)
    if "final" in prepared:
        result = prepared["final"]
        yield {"type": "token", "text": result["content"]}
        yield {"type": "result", **result}
        return

    parts: list[str] = []
    try:
        async for chunk in llm.astream(prepared["messages"]):
            text = str(getattr(chunk, "content", chunk) or "")
            if text:
                parts.append(text)
                yield {"type": "token", "text": text}
    except Exception as exc:  # noqa: BLE001
        logger.error("draft section streaming failed | section=%r | %s", section.get("heading"), exc)
        if not parts:
            yield {"type": "result", "content": NO_SOURCE_SECTION, "sources": [], "status": "needs_source"}
            return

    result = _finish_section("".join(parts), prepared["sources"])
    yield {"type": "result", **result}


async def refine_section(
    section: dict[str, Any],
    instruction: str,
    user_client: Any,
    llm: Any,
) -> dict[str, Any]:
    """Revise one section by instruction, without adding new facts.

    Bounded rewrite of already-grounded text: no new retrieval, and citations
    are re-validated against the section's existing sources.
    """
    content = str(section.get("content") or "")
    sources = list(section.get("sources") or [])
    if not content.strip():
        raise ValueError("cannot refine an empty section")
    prompt = REFINE_SECTION_TEMPLATE.format(
        heading=section.get("heading") or "",
        instruction=instruction or "Improve clarity and precision.",
        content=content,
    )
    response = await llm.ainvoke([HumanMessage(content=prompt)])
    revised = getattr(response, "content", "") or ""
    revised = _normalize_citations(revised)
    if is_no_source_answer(revised):
        raise ValueError("refine refused: section has no grounding")
    revised = _strip_markdown(revised)
    revised, _bad = _strip_invalid_citations(revised, len(sources))
    return {"content": revised, "sources": sources, "status": "edited"}


# ------------------------------------------------------------------ playbook

VALID_FLAG_STATUSES = ("ok", "breach", "unclear")


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _format_rules(rules: list[dict[str, Any]]) -> str:
    lines = []
    for rule in rules:
        lines.append(
            f'- id={rule.get("id", "")} | clause_type={rule.get("clause_type", "")} '
            f'| position={rule.get("position", "")} | severity={rule.get("severity", "")} '
            f'| {rule.get("rule_text", "")}'
        )
    return "\n".join(lines)


def _format_sections(sections: list[dict[str, Any]]) -> str:
    blocks = []
    for section in sections:
        blocks.append(
            f'[section {section.get("order_index")}] {section.get("heading", "")} '
            f'({section.get("clause_type", "")})\n{section.get("content") or "(empty)"}'
        )
    return "\n\n".join(blocks) or "(no sections)"


def parse_verdicts(raw_text: str) -> list[dict[str, Any]]:
    """Extract a JSON array of verdict objects from an LLM reply (tolerant of
    prose and markdown fences); returns [] on anything unusable."""
    if not raw_text:
        return []
    start = raw_text.find("[")
    end = raw_text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        parsed = json.loads(raw_text[start : end + 1])
    except (ValueError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def map_rule_verdicts(
    rules: list[dict[str, Any]],
    verdicts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Deterministically attach rule metadata to LLM verdicts.

    Every rule yields exactly one flag, in rule order, so the output can never
    silently drop a firm position. Verdicts are matched by ``rule_id`` first,
    then positionally when a verdict carries no id. Missing/invalid verdicts
    fall back to ``unclear``.
    """
    by_id: dict[str, dict[str, Any]] = {}
    for verdict in verdicts:
        rid = verdict.get("rule_id")
        if rid is not None:
            by_id[str(rid)] = verdict

    flags: list[dict[str, Any]] = []
    for index, rule in enumerate(rules):
        rid = rule.get("id")
        verdict = by_id.get(str(rid)) if rid is not None else None
        if verdict is None and index < len(verdicts) and not verdicts[index].get("rule_id"):
            verdict = verdicts[index]
        verdict = verdict or {}
        status = str(verdict.get("status") or "unclear").strip().lower()
        if status not in VALID_FLAG_STATUSES:
            status = "unclear"
        flags.append({
            "rule_id": rid,
            "rule_text": str(rule.get("rule_text") or ""),
            "clause_type": str(rule.get("clause_type") or ""),
            "position": str(rule.get("position") or "advisory"),
            "severity": str(rule.get("severity") or "medium"),
            "order_index": _coerce_int(verdict.get("order_index")),
            "status": status,
            "note": str(verdict.get("note") or "").strip(),
        })
    return flags


def summarize_flags(flags: list[dict[str, Any]]) -> dict[str, int]:
    summary = {
        "total": len(flags),
        "ok": 0,
        "breach": 0,
        "unclear": 0,
        "breach_high": 0,
        "breach_medium": 0,
        "breach_low": 0,
    }
    for flag in flags:
        status = flag.get("status")
        if status in ("ok", "breach", "unclear"):
            summary[status] += 1
        if status == "breach":
            key = f"breach_{flag.get('severity')}"
            if key in summary:
                summary[key] += 1
    return summary


async def check_playbook(
    rules: list[dict[str, Any]],
    draft_sections: list[dict[str, Any]],
    llm: Any,
) -> list[dict[str, Any]]:
    """Check a draft against a playbook's rules in a single LLM call.

    Rule metadata (position/severity/text) is always mapped in deterministically
    — the model only supplies the per-rule verdict — so a prohibited position
    can still be flagged as a breach and every rule is always reported.
    """
    if not rules:
        return []
    prompt = PLAYBOOK_CHECK_TEMPLATE.format(
        rules=_format_rules(rules),
        sections=_format_sections(draft_sections),
    )
    try:
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        verdicts = parse_verdicts(getattr(response, "content", "") or "")
    except Exception as exc:  # noqa: BLE001
        logger.warning("playbook check failed | rules=%d | %s", len(rules), exc)
        verdicts = []
    return map_rule_verdicts(rules, verdicts)
