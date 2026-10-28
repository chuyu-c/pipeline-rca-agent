"""Structure-aware chunking of the markdown on-call docs in `oncall/` (DESIGN §7).

- Runbooks split on headings: each section is a self-contained procedure. The heading path is
  prefixed to the chunk text so a chunk like "Remediation > Backfill" still says which runbook.
- Postmortems stay whole when short (they lose meaning when split), else split by `##` section.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

DocType = Literal["runbook", "postmortem"]
POSTMORTEM_MAX_TOKENS = 800

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_DOC_TYPES: dict[str, DocType] = {"runbooks": "runbook", "postmortems": "postmortem"}


@dataclass(frozen=True)
class Document:
    doc_id: str  # path relative to oncall/, no suffix: 'runbooks/schema_drift'
    doc_type: DocType
    title: str
    body: str  # markdown without front-matter
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    doc_id: str
    doc_type: DocType
    title: str
    section: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


def estimate_tokens(text: str) -> int:
    """Rough BPE token count (~4 characters per token for English prose)."""
    return len(text) // 4


def split_front_matter(raw: str) -> tuple[dict[str, Any], str]:
    if not raw.startswith("---\n"):
        return {}, raw
    end = raw.find("\n---\n", 4)
    if end == -1:
        raise ValueError("unterminated YAML front-matter")
    meta = yaml.safe_load(raw[4:end]) or {}
    if not isinstance(meta, dict):
        raise ValueError("front-matter must be a mapping")
    return meta, raw[end + 5 :]


def parse_document(raw: str, doc_id: str, doc_type: DocType) -> Document:
    meta, body = split_front_matter(raw)
    h1 = next(
        (
            m.group(2)
            for line in body.splitlines()
            if (m := _HEADING.match(line)) and m.group(1) == "#"
        ),
        None,
    )
    title = str(meta.get("title") or h1 or doc_id)
    # Dates parse as datetime.date; keep metadata JSON-friendly.
    meta = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in meta.items()}
    return Document(doc_id, doc_type, title, body, meta)


def load_oncall(oncall_dir: Path) -> list[Document]:
    docs = []
    for sub, doc_type in _DOC_TYPES.items():
        for path in sorted((oncall_dir / sub).glob("*.md")):
            doc_id = f"{sub}/{path.stem}"
            docs.append(parse_document(path.read_text(encoding="utf-8"), doc_id, doc_type))
    return docs


def _sections(body: str, max_level: int) -> list[tuple[list[str], str]]:
    """(heading path, text) for each section, splitting on headings up to `max_level`.

    Headings inside fenced code blocks are content, not structure. The H1 is the document title,
    so it isn't part of the path; text before the first split heading is the 'Overview'.
    """
    sections: list[tuple[list[str], list[str]]] = [(["Overview"], [])]
    path: list[str] = []
    in_fence = False
    for line in body.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
        m = None if in_fence else _HEADING.match(line)
        if m and len(m.group(1)) == 1:
            continue
        if m and len(m.group(1)) <= max_level:
            level = len(m.group(1)) - 1  # '##' -> depth 1
            path = [*path[: level - 1], m.group(2)]
            sections.append((list(path), []))
        else:
            sections[-1][1].append(line)
    return [(p, "\n".join(lines).strip()) for p, lines in sections]


def chunk_document(doc: Document) -> list[Chunk]:
    if doc.doc_type == "postmortem" and estimate_tokens(doc.body) <= POSTMORTEM_MAX_TOKENS:
        body = "\n".join(
            line for line in doc.body.strip().splitlines() if not re.match(r"^#\s", line)
        )
        pieces = [(["Full postmortem"], body.strip())]
    else:
        max_level = 3 if doc.doc_type == "runbook" else 2
        pieces = [(p, t) for p, t in _sections(doc.body, max_level) if t]

    kind = "Runbook" if doc.doc_type == "runbook" else "Postmortem"
    chunks = []
    for i, (path, text) in enumerate(pieces):
        section = " > ".join(path)
        chunks.append(
            Chunk(
                chunk_id=f"{doc.doc_id}#{i:02d}",
                doc_id=doc.doc_id,
                doc_type=doc.doc_type,
                title=doc.title,
                section=section,
                text=f"{kind}: {doc.title} > {section}\n\n{text}",
                metadata=doc.metadata,
            )
        )
    return chunks
