
import html
import json
import re

from ptw.constants import DOCUMENT_BEGIN, DOCUMENT_END
from ptw.domain.models import FetchDocument


def _json_script_payloads(text: str) -> list[str]:
    payloads: list[str] = []
    pattern = re.compile(
        r"<script\b(?P<attrs>[^>]*)>(?P<body>.*?)</script>",
        flags=re.I | re.S,
    )
    for match in pattern.finditer(text):
        attrs = match.group("attrs")
        if not re.search(r"\btype\s*=\s*[\"']?application/json", attrs, flags=re.I):
            continue
        body = html.unescape(match.group("body")).strip()
        if body:
            payloads.append(body)
    return payloads


def _append_structured_source(source: str, payloads: list[str]) -> None:
    for body in _json_script_payloads(source):
        try:
            json.loads(body)
        except (TypeError, ValueError):
            continue
        payloads.append(body)


def serialize_documents(documents: list[FetchDocument]) -> str:
    blocks: list[str] = []
    for document in sorted(documents, key=lambda item: item.original_order):
        payload = {
            "source_url": document.source_url,
            "document_type": document.document_type,
            "document_id": document.document_id,
            "parent_document_id": document.parent_document_id,
            "record_id": document.record_id,
            "record_path": document.record_path,
            "original_order": document.original_order,
            "fetched_at": document.fetched_at,
            "raw_content": document.raw_content,
        }
        blocks.append(
            f"{DOCUMENT_BEGIN}\n"
            f"{json.dumps(payload, ensure_ascii=False)}\n"
            f"{DOCUMENT_END}"
        )
    return "\n".join(blocks)


def split_documents(text: str) -> list[FetchDocument]:
    pattern = re.compile(
        rf"{re.escape(DOCUMENT_BEGIN)}\s*\n(.*?){re.escape(DOCUMENT_END)}",
        flags=re.S,
    )
    documents: list[FetchDocument] = []
    for match in pattern.finditer(text):
        try:
            payload = json.loads(match.group(1).strip())
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict) or not isinstance(payload.get("raw_content"), str):
            continue
        documents.append(
            FetchDocument(
                source_url=str(payload.get("source_url", "")),
                document_type=str(payload.get("document_type", "html")),
                document_id=str(payload.get("document_id", "")),
                raw_content=payload["raw_content"],
                parent_document_id=str(payload.get("parent_document_id", "")),
                record_id=str(payload.get("record_id", "")),
                record_path=str(payload.get("record_path", "")),
                original_order=int(payload.get("original_order", len(documents))),
                fetched_at=str(payload.get("fetched_at", "")),
            )
        )
    if documents:
        return sorted(documents, key=lambda item: item.original_order)
    return [FetchDocument("", "inline", "inline", text)]
