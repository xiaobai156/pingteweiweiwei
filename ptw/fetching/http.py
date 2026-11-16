import json
import re
import ssl
import time
import urllib.parse
import urllib.request
from datetime import datetime

from ptw.domain.models import FetchDocument
from ptw.fetching.documents import (
    _json_script_payloads,
    serialize_documents,
)
from ptw.fetching.dynamic_api import (
    _fetch_manager_article_payload,
    record_id_from_url,
)
from ptw.text import decode_bytes, decode_document_writes, html_to_text


USER_AGENT = "Mozilla/5.0 tail-fetcher/2.0"
TRANSIENT_FETCH_ATTEMPTS = 3
TRUSTED_EXTERNAL_SCRIPT_SUFFIXES = (".cosds.ahsccn.com", ".cosds.aohjifv.com")


def _is_trusted_external_script_host(host: str) -> bool:
    return any(
        host == suffix.lstrip(".") or host.endswith(suffix)
        for suffix in TRUSTED_EXTERNAL_SCRIPT_SUFFIXES
    )


def _decoded_response(response) -> str:
    response.raise_for_status()
    encoding = response.apparent_encoding or response.encoding or "utf-8"
    return decode_bytes(response.content, encoding)


def _text_document(
    source_url: str,
    document_type: str,
    document_id: str,
    source: str,
    order: int,
    fetched_at: str,
    parent_document_id: str = "",
) -> FetchDocument:
    decoded = decode_document_writes(source)
    return FetchDocument(
        source_url=source_url,
        document_type=document_type,
        document_id=document_id,
        raw_content=html_to_text(decoded.encode("utf-8")),
        parent_document_id=parent_document_id,
        original_order=order,
        fetched_at=fetched_at,
    )


def _append_json_documents(
    documents: list[FetchDocument],
    source_url: str,
    source: str,
    parent_document_id: str,
    fetched_at: str,
    seen: set[str],
) -> None:
    for payload in _json_script_payloads(source):
        try:
            normalized = json.dumps(
                json.loads(payload),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError):
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        documents.append(
            FetchDocument(
                source_url=source_url,
                document_type="json",
                document_id=f"json-{len(documents)}",
                raw_content=payload,
                parent_document_id=parent_document_id,
                original_order=len(documents),
                fetched_at=fetched_at,
            )
        )


def _get_with_retry(session, url: str, timeout: int, headers: dict):
    """对间歇性 TLS/连接中断（如 SSLEOFError）做有限重试，其它错误直接抛出。"""
    last_exc = None
    for attempt in range(TRANSIENT_FETCH_ATTEMPTS):
        try:
            return session.get(url, timeout=timeout, verify=True, headers=headers)
        except (ssl.SSLError, OSError) as exc:
            last_exc = exc
        except Exception as exc:
            # requests 的连接类异常（ConnectionError 等）同样属于瞬时故障
            if not _is_transient_connection_error(exc):
                raise
            last_exc = exc
        if attempt + 1 < TRANSIENT_FETCH_ATTEMPTS:
            time.sleep(1 + attempt)
    raise last_exc


def _is_transient_connection_error(exc: Exception) -> bool:
    name = type(exc).__name__
    if name in {"ConnectionError", "Timeout", "ChunkedEncodingError", "SSLError"}:
        return True
    text = str(exc)
    return "SSLEOFError" in text or "Max retries exceeded" in text


def _fetch_with_session(url: str, timeout: int, session) -> list[FetchDocument]:
    headers = {"User-Agent": USER_AGENT}
    response = _get_with_retry(session, url, timeout, headers)
    source = _decoded_response(response)
    fetched_at = datetime.now().isoformat(timespec="seconds")
    documents = [_text_document(url, "html", "main", source, 0, fetched_at)]
    raw_sources = [source]
    seen_json: set[str] = set()
    _append_json_documents(documents, url, source, "main", fetched_at, seen_json)

    manager_payload = _fetch_manager_article_payload(url, session, source)
    if "/article/manager/" in url and not manager_payload:
        raise RuntimeError(f"Manager接口抓取失败：{url}")
    if manager_payload:
        documents.append(
            FetchDocument(
                source_url=url,
                document_type="json",
                document_id=f"manager-{record_id_from_url(url)}",
                raw_content=manager_payload,
                parent_document_id="main",
                record_id=record_id_from_url(url),
                original_order=len(documents),
                fetched_at=fetched_at,
            )
        )

    page_host = urllib.parse.urlparse(url).hostname or ""
    script_urls = re.findall(r"<script[^>]+src=[\"']([^\"']+)[\"']", source, flags=re.I)
    seen_scripts: set[str] = set()
    for script_url in script_urls:
        full_url = urllib.parse.urljoin(url, script_url)
        if full_url in seen_scripts:
            continue
        seen_scripts.add(full_url)
        script_parts = urllib.parse.urlparse(full_url)
        script_host = script_parts.hostname or ""
        if script_host != page_host:
            if "/upload/script/" not in script_parts.path.lower():
                continue
            if not _is_trusted_external_script_host(script_host):
                raise RuntimeError(f"脚本来源不在白名单：{full_url}")
        try:
            script_response = _get_with_retry(session, full_url, timeout, headers)
            if getattr(script_response, "status_code", 200) == 404:
                # 脚本 404 表示该文件已被站点删除，属资源缺失而非抓取异常：跳过即可，
                # 主文档照常解析；若数据本在脚本内，解析会以无结果失败上报，不会被静默放过。
                continue
            script_source = _decoded_response(script_response)
        except Exception as exc:
            raise RuntimeError(f"脚本抓取失败：{full_url}: {exc}") from exc
        document_id = f"script-{len(documents)}"
        documents.append(
            _text_document(
                full_url,
                "script",
                document_id,
                script_source,
                len(documents),
                fetched_at,
                "main",
            )
        )
        raw_sources.append(script_source)
        _append_json_documents(
            documents,
            full_url,
            script_source,
            document_id,
            fetched_at,
            seen_json,
        )

    iframe_urls: list[str] = []
    for raw_source in raw_sources:
        iframe_urls.extend(
            re.findall(r"<iframe[^>]+src=\\?[\"']([^\"'\\]+)", raw_source, flags=re.I)
        )
    seen_iframes: set[str] = set()
    for iframe_url in iframe_urls:
        full_url = urllib.parse.urljoin(url, iframe_url.strip("\\"))
        if full_url in seen_iframes or urllib.parse.urlparse(full_url).netloc != page_host:
            continue
        seen_iframes.add(full_url)
        try:
            iframe_source = _decoded_response(
                session.get(full_url, timeout=timeout, verify=True, headers=headers)
            )
        except Exception as exc:
            raise RuntimeError(f"iframe抓取失败：{full_url}: {exc}") from exc
        document_id = f"iframe-{len(documents)}"
        documents.append(
            _text_document(
                full_url,
                "iframe",
                document_id,
                iframe_source,
                len(documents),
                fetched_at,
                "main",
            )
        )
        _append_json_documents(
            documents,
            full_url,
            iframe_source,
            document_id,
            fetched_at,
            seen_json,
        )
    return documents


def fetch_documents(url: str, timeout: int = 20, session=None) -> list[FetchDocument]:
    if session is not None:
        return _fetch_with_session(url, timeout, session)
    try:
        import requests
        return _fetch_with_session(url, timeout, requests)
    except ImportError:
        pass

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    context = ssl.create_default_context()
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        source = decode_bytes(response.read())
    fetched_at = datetime.now().isoformat(timespec="seconds")
    return [_text_document(url, "html", "main", source, 0, fetched_at)]


def fetch_text(url: str, timeout: int = 20) -> str:
    return serialize_documents(fetch_documents(url, timeout))
