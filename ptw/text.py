import base64
import html
import re

from ptw.constants import DIGIT_TRANSLATION


def html_to_text(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="ignore")
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "\n", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|tr|li|h[1-6]|table|section|article)>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def decode_bytes(raw: bytes, fallback: str = "utf-8") -> str:
    head = raw[:1000].decode("ascii", errors="ignore")
    declared = ""
    match = re.search(r"charset=[\"']?([a-zA-Z0-9_\-]+)", head, flags=re.I)
    if match:
        declared = match.group(1)
    for encoding in ("utf-8-sig", declared, fallback, "utf-8", "gb18030", "big5"):
        if not encoding:
            continue
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="ignore")


def decode_document_writes(source: str) -> str:
    chunks = [source]
    for encoded in re.findall(r"strdecode\([\"']([^\"']+)[\"']\)", source):
        try:
            chunks.append(base64.b64decode(encoded).decode("utf-8", errors="ignore"))
        except ValueError:
            continue
    decrypt_values = re.findall(
        r"decrypt\s*\(\s*[\"'][^\"']+[\"']\s*,\s*[\"']([^\"']+)[\"']\s*\)",
        source,
        flags=re.S,
    )
    decrypt_values.extend(re.findall(r"decrypt2\s*\(\s*[\"']([^\"']+)[\"']\s*\)", source, flags=re.S))
    for encoded in decrypt_values:
        try:
            chunks.append(decode_bytes(base64.b64decode(encoded), "gb18030"))
        except ValueError:
            continue
    return "\n".join(chunks)


def clean_text(value: object) -> str:
    return re.sub(r"@+", "", str(value or "")).strip()


def normalize_digits(value: str) -> str:
    return value.translate(DIGIT_TRANSLATION)


def normalize_search(value: str) -> str:
    return re.sub(r"[\s「」『』【】\[\]（）()《》<>·•\-_—:：↔①㊣®♠★☆◇◆💎]+", "", value)

