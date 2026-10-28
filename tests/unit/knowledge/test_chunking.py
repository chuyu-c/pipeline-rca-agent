import pytest

from rca.knowledge.chunking import (
    POSTMORTEM_MAX_TOKENS,
    chunk_document,
    parse_document,
    split_front_matter,
)

RUNBOOK = """---
title: Schema drift
category: schema_drift
---
# Runbook: Schema drift

Intro paragraph about drift.

## Triage

### Confirm it is drift
Compare columns:

```sql
-- ## not a heading inside a code fence
SELECT column_name FROM information_schema.columns;
```

### Scope the blast radius
Downstream marts go stale.

## Remediation
Escalate to the source team.

## Empty section

## Prevention
Contract tests.
"""


def runbook_chunks():
    return chunk_document(parse_document(RUNBOOK, "runbooks/schema_drift", "runbook"))


def test_front_matter_is_parsed_and_stripped() -> None:
    meta, body = split_front_matter(RUNBOOK)
    assert meta == {"title": "Schema drift", "category": "schema_drift"}
    assert body.startswith("# Runbook: Schema drift")
    assert split_front_matter("# no front matter") == ({}, "# no front matter")
    with pytest.raises(ValueError):
        split_front_matter("---\ntitle: x\n# never closed")


def test_runbook_splits_on_headings_with_heading_path() -> None:
    chunks = runbook_chunks()
    assert [c.section for c in chunks] == [
        "Overview",
        "Triage > Confirm it is drift",
        "Triage > Scope the blast radius",
        "Remediation",
        "Prevention",
    ]  # "Triage" itself and "Empty section" have no body → no chunk
    assert [c.chunk_id for c in chunks] == [f"runbooks/schema_drift#{i:02d}" for i in range(5)]


def test_chunk_text_is_prefixed_with_doc_and_section() -> None:
    chunk = runbook_chunks()[2]
    assert chunk.text.startswith("Runbook: Schema drift > Triage > Scope the blast radius\n\n")
    assert "Downstream marts go stale." in chunk.text
    assert chunk.metadata["category"] == "schema_drift"
    assert chunk.doc_type == "runbook"


def test_headings_inside_code_fences_do_not_split() -> None:
    confirm = runbook_chunks()[1]
    assert "-- ## not a heading inside a code fence" in confirm.text
    assert "SELECT column_name" in confirm.text


def postmortem(sections: int, words_per_section: int) -> str:
    body = "\n\n".join(
        f"## Section {i}\n" + " ".join(["word"] * words_per_section) for i in range(sections)
    )
    return (
        f"---\ncategory: resource\ntables: [raw.orders]\ndate: 2025-10-28\n---\n# Title\n\n{body}\n"
    )


def test_short_postmortem_stays_whole() -> None:
    doc = parse_document(postmortem(4, 50), "postmortems/short", "postmortem")
    chunks = chunk_document(doc)
    assert len(chunks) == 1
    assert chunks[0].section == "Full postmortem"
    assert all(f"## Section {i}" in chunks[0].text for i in range(4))
    assert chunks[0].metadata["date"] == "2025-10-28"  # dates serialised for JSON


def test_long_postmortem_splits_by_section() -> None:
    # 4 sections x 300 words x 5 chars ≈ 1500 tokens > POSTMORTEM_MAX_TOKENS
    doc = parse_document(postmortem(4, 300), "postmortems/long", "postmortem")
    chunks = chunk_document(doc)
    assert POSTMORTEM_MAX_TOKENS < 4 * 300 * 5 // 4
    assert [c.section for c in chunks] == [f"Section {i}" for i in range(4)]
    assert chunks[0].text.startswith("Postmortem: Title > Section 0")


def test_title_falls_back_to_h1() -> None:
    doc = parse_document("# The H1 title\n\nBody", "runbooks/x", "runbook")
    assert doc.title == "The H1 title"
