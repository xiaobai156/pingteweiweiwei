import unittest

from ptw.fetching.documents import serialize_documents, split_documents
from ptw.fetching.http import fetch_documents


class FakeResponse:
    def __init__(self, content: str):
        self.content = content.encode("utf-8")
        self.apparent_encoding = "utf-8"
        self.encoding = "utf-8"

    def raise_for_status(self):
        return None


class FakeSession:
    def __init__(self, responses: dict[str, str]):
        self.responses = responses

    def get(self, url: str, **_kwargs):
        return FakeResponse(self.responses[url])


class RecordingSession(FakeSession):
    def __init__(self, responses: dict[str, str]):
        super().__init__(responses)
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        return super().get(url, **kwargs)


class FetchDocumentTests(unittest.TestCase):
    def test_http_fetch_keeps_main_and_iframe_as_separate_documents(self):
        url = "https://example.test/topic/1"
        frame_url = "https://example.test/frame/1"
        session = FakeSession(
            {
                url: '<div>专属模块</div><iframe src="/frame/1"></iframe>',
                frame_url: "<div>207期 平特一尾【4】</div>",
            }
        )
        documents = fetch_documents(url, session=session)
        self.assertEqual(["html", "iframe"], [item.document_type for item in documents])
        self.assertEqual("main", documents[1].parent_document_id)
        self.assertNotIn("207期", documents[0].raw_content)
        self.assertIn("207期", documents[1].raw_content)
        round_trip = split_documents(serialize_documents(documents))
        self.assertEqual(documents, round_trip)

    def test_http_fetch_deduplicates_script_urls_and_verifies_tls(self):
        url = "https://example.test/topic/1"
        script_url = "https://example.test/upload/script/a.js"
        session = RecordingSession(
            {
                url: '<script src="/upload/script/a.js"></script><script src="/upload/script/a.js"></script>',
                script_url: "目标模块 211期 平特一尾【4】",
            }
        )
        fetch_documents(url, session=session)
        self.assertEqual(1, sum(call[0] == script_url for call in session.calls))
        self.assertTrue(all(call[1].get("verify") is True for call in session.calls))

    def test_http_fetch_fails_closed_when_attached_script_fails(self):
        url = "https://example.test/topic/1"
        session = RecordingSession({url: '<script src="/upload/script/missing.js"></script>'})
        with self.assertRaisesRegex(RuntimeError, "脚本抓取失败"):
            fetch_documents(url, session=session)

    def test_http_allows_trusted_external_data_script_host(self):
        url = "https://example.test/topic/1"
        script_url = "https://xia01.cosds.ahsccn.com/upload/script/a.js"
        session = RecordingSession(
            {
                url: f'<script src="{script_url}"></script>',
                script_url: "目标模块 211期 平特一尾【4】",
            }
        )
        documents = fetch_documents(url, session=session)
        self.assertEqual("script", documents[1].document_type)
        self.assertIn(script_url, [call[0] for call in session.calls])

    def test_http_allows_verified_cdn_script_host(self):
        url = "https://example.test/topic/1"
        script_url = "https://xia01.cosds.aohjifv.com/upload/script/a.js"
        session = FakeSession(
            {
                url: f'<script src="{script_url}"></script>',
                script_url: "目标模块 211期 平特一尾【4】",
            }
        )
        documents = fetch_documents(url, session=session)
        self.assertEqual("script", documents[1].document_type)

    def test_http_rejects_untrusted_external_data_script_host(self):

        url = "https://example.test/topic/1"
        script_url = "https://evil.example/upload/script/foreign.js"
        session = RecordingSession({url: f'<script src="{script_url}"></script>'})
        with self.assertRaisesRegex(RuntimeError, "脚本来源不在白名单"):
            fetch_documents(url, session=session)


if __name__ == "__main__":
    unittest.main()
