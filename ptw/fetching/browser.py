import threading
import urllib.parse
from datetime import datetime, timezone

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from ptw.domain.models import FetchDocument
from ptw.fetching.documents import serialize_documents
from ptw.fetching.http import (
    TRUSTED_EXTERNAL_SCRIPT_SUFFIXES,
    _append_json_documents,
    _text_document,
    fetch_text,
)
from ptw.text import decode_bytes


class BrowserFetcher:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=True)

    def close(self) -> None:
        with self._lock:
            self._browser.close()
            self._playwright.stop()

    def fetch(self, url: str, timeout: int = 20) -> str:
        if "/article/manager/" in url:
            return fetch_text(url, timeout)
        with self._lock:
            page = self._browser.new_page()
            responses = []
            page.on("response", lambda response: responses.append(response))
            try:
                response = None
                try:
                    response = page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=max(timeout, 30) * 1000,
                    )
                    page.wait_for_timeout(1500)
                except PlaywrightTimeoutError:
                    page.wait_for_timeout(5000)
                    response = next(
                        (
                            item
                            for item in responses
                            if item.request.resource_type == "document"
                        ),
                        None,
                    )
                if response is None or response.status >= 400:
                    raise RuntimeError(f"页面抓取失败：{url}")
                documents = self._documents(url, responses)
                return serialize_documents(documents)
            finally:
                page.close()

    def _documents(self, url: str, responses) -> list[FetchDocument]:
        page_netloc = urllib.parse.urlparse(url).netloc
        page_host = urllib.parse.urlparse(url).hostname or ""
        main = next(
            (
                response
                for response in responses
                if response.request.resource_type == "document"
                and response.url.split("#", 1)[0] == url
            ),
            None,
        )
        if main is None:
            main = next(
                response for response in responses if response.request.resource_type == "document"
            )
        fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        source = decode_bytes(main.body())
        documents = [
            _text_document(url, "html", "main", source, 0, fetched_at)
        ]
        seen_json: set[str] = set()
        _append_json_documents(documents, url, source, "main", fetched_at, seen_json)
        for response in responses:
            if response is main:
                continue
            request = response.request
            if request.resource_type == "document":
                iframe_url = response.url
                if urllib.parse.urlparse(iframe_url).netloc != page_netloc:
                    continue
                iframe_source = decode_bytes(response.body())
                documents.append(
                    _text_document(
                        iframe_url,
                        "iframe",
                        f"iframe-{len(documents)}",
                        iframe_source,
                        len(documents),
                        fetched_at,
                        "main",
                    )
                )
                _append_json_documents(
                    documents,
                    iframe_url,
                    iframe_source,
                    documents[-1].document_id,
                    fetched_at,
                    seen_json,
                )
                continue
            if request.resource_type != "script":
                continue
            script_url = response.url
            script_host = urllib.parse.urlparse(script_url).hostname or ""
            if script_host != page_host:
                if "/upload/script/" not in urllib.parse.urlparse(script_url).path.lower():
                    continue
                if not any(
                    script_host == suffix.lstrip(".") or script_host.endswith(suffix)
                    for suffix in TRUSTED_EXTERNAL_SCRIPT_SUFFIXES
                ):
                    raise RuntimeError(f"脚本来源不在白名单：{script_url}")
            if response.status >= 400:
                raise RuntimeError(f"脚本抓取失败：{script_url}: HTTP {response.status}")
            script_source = decode_bytes(response.body())
            script_id = f"script-{len(documents)}"
            documents.append(
                _text_document(
                    script_url,
                    "script",
                    script_id,
                    script_source,
                    len(documents),
                    fetched_at,
                    "main",
                )
            )
            _append_json_documents(
                documents,
                script_url,
                script_source,
                script_id,
                fetched_at,
                seen_json,
            )
        return documents
