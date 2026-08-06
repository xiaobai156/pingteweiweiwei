
import base64
import json
import re
import urllib.parse

from ptw.text import normalize_digits


def _canonical_record_id(value: object) -> str:
    normalized = normalize_digits(str(value or "")).strip()
    if re.fullmatch(r"\d+", normalized):
        return normalized
    if re.fullmatch(r"[A-Za-z0-9_-]+", normalized):
        return normalized
    return ""


def record_id_from_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    query_ids: list[str] = []
    for key in ("id", "topic_id", "article_id", "record_id", "content_id"):
        for value in query.get(key, []):
            record_id = _canonical_record_id(value)
            if record_id:
                query_ids.append(record_id)
    if query_ids:
        return query_ids[0] if len(set(query_ids)) == 1 else ""

    path = urllib.parse.unquote(parsed.path.rstrip("/"))
    route_match = re.search(
        r"/(?:topic|article(?:/(?:admin|manager))?)/([A-Za-z0-9_-]+)(?:\.html?)?$",
        path,
        flags=re.I,
    )
    if route_match:
        return _canonical_record_id(route_match.group(1))
    final_match = re.search(r"/(\d+)(?:\.html?)?$", path, flags=re.I)
    return _canonical_record_id(final_match.group(1)) if final_match else ""


def _decode_api_base64(value: object) -> str:
    if not isinstance(value, str):
        return str(value or "")
    raw = value.strip().replace("-", "+").replace("_", "/")
    if len(raw) < 8 or not re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", raw):
        return value
    raw += "=" * ((4 - len(raw) % 4) % 4)
    try:
        decoded = base64.b64decode(raw).decode("utf-8", errors="ignore")
    except (ValueError, UnicodeDecodeError):
        return value
    return decoded if decoded else value


def _fetch_manager_article_payload(url: str, session: object, page_source: str) -> str:
    parsed = urllib.parse.urlparse(url)
    route_match = re.search(
        r"/article/(manager|admin)/([A-Za-z0-9_-]+)(?:/)?$",
        parsed.path.rstrip("/"),
        flags=re.I,
    )
    if not route_match or route_match.group(1).lower() != "manager":
        return ""
    record_id = _canonical_record_id(route_match.group(2))
    if not record_id:
        return ""
    api_url = urllib.parse.urlunparse(
        (parsed.scheme, parsed.netloc, f"/api/proxy/manager-articles/{urllib.parse.quote(record_id, safe='')}", "", "", "")
    )
    try:
        response = session.get(
            api_url,
            timeout=20,
            verify=True,
            headers={
                "User-Agent": "Mozilla/5.0 tail-fetcher/1.0",
                "Accept": "application/json",
                "Referer": url,
            },
        )
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return ""
    if not isinstance(payload, dict) or _canonical_record_id(payload.get("id")) != record_id:
        return ""
    normalized = dict(payload)
    for field in ("title", "html"):
        if field in normalized:
            normalized[field] = _decode_api_base64(normalized[field])
    normalized["record_kind"] = "manager_article"
    return json.dumps(normalized, ensure_ascii=False)
