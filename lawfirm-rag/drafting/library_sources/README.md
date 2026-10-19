# Drafting library sources

Drop the firm's real templates, clauses and playbook positions **here**, then
import them. Nothing in this folder is seeded automatically, and no firm
content needs to be sent anywhere for it to work.

## 1. Put your documents in this folder

Any mix of `.docx`, `.pdf`, `.txt` and `.md`, in subfolders if you like:

```
library_sources/
  clauses/
    force-majeure.docx
    governing-law.docx
    indemnity-seller-side.docx
  templates/
    share-purchase-agreement.docx
```

## 2. Generate a manifest

```bash
cd lawfirm-rag
..\.venv\Scripts\python.exe scripts/import_drafting_library.py --scaffold drafting/library_sources
```

This lists every document it found in `library.json` and changes nothing else.
Open it and fill in the blanks.

## 3. Check what will be imported

```bash
..\.venv\Scripts\python.exe scripts/import_drafting_library.py drafting/library_sources --apply --dry-run
```

Writes the assembled seed to `drafting/imported_seed_data.json` so you can read
exactly what would be written. Nothing is uploaded.

## 4. Apply it

```bash
..\.venv\Scripts\python.exe scripts/import_drafting_library.py drafting/library_sources --apply
```

Add `--ingest` to also index each source document into the firm corpus and
record its `file_id` on the clause, so a clause used in a draft can be traced
back to the document it came from:

```bash
..\.venv\Scripts\python.exe scripts/import_drafting_library.py drafting/library_sources --apply --ingest
```

Re-running is safe: rows with the same title (clauses/templates) or name
(playbooks) are replaced, not duplicated.

## Manifest fields

| Field | Applies to | Notes |
| --- | --- | --- |
| `title` | clauses, templates | Natural key — re-importing replaces by this. |
| `body` | clauses | Inline text. Wins over `file`. |
| `file` | clauses | Path relative to this folder. |
| `clause_type` | clauses, playbook rules | e.g. `force_majeure`, `indemnity`. |
| `doc_type` | all | e.g. `share_purchase`. Empty means "any document type". |
| `practice_area` | clauses, templates | Optional grouping. |
| `access_level` | all | Classification ceiling the *drafter* must have (1 = every level). |
| `matter_id` | all | Leave `""` for firm-wide. |
| `skeleton` | templates | Ordered `{heading, clause_type, guidance, required}` sections. |
| `rules` | playbooks | `{clause_type, position, rule_text, severity}` — `position` is `preferred` / `advisory` / `must_reject` / etc. |

`defaults` at the top level is merged into every entry that does not set the
field itself, so you only write `access_level` once.

## Where the content is used

Templates drive draft structure, clauses are retrieved as grounded source
material for each section, and playbook rules are what the Draft Studio check
(`POST /drafts/{id}/check`) flags deviations against. A section that cannot be
grounded in the corpus or the clause bank is returned as `needs_source` — the
model is never asked to fill a gap from general knowledge.
