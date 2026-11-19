import hashlib
import json
from unittest.mock import patch

import httpx
from asgiref.sync import async_to_sync
from django.test import SimpleTestCase, TestCase

from ragapp.models import Chunk, IndexedDocument, Source
from ragapp.ollama import OllamaClient
from ragapp.services import ingest_source, sha256_text


class FakeOllama:
    def __init__(self) -> None:
        self.calls = 0

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return [[1.0] + [0.0] * 767 for _ in texts]


class HashTests(SimpleTestCase):
    def test_sha256_is_stable_and_uses_utf8(self) -> None:
        text = "RAG café"
        self.assertEqual(
            sha256_text(text),
            hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(sha256_text(text), sha256_text(text))


class OllamaChatTests(SimpleTestCase):
    def test_chat_disables_thinking_and_streams_answer_content(self) -> None:
        requests = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            event = json.dumps(
                {"message": {"role": "assistant", "content": "Grounded answer"}}
            )
            return httpx.Response(200, content=f"{event}\n".encode())

        transport = httpx.MockTransport(respond)
        async_client_class = httpx.AsyncClient

        async def run_chat() -> list[str]:
            with patch(
                "ragapp.ollama.httpx.AsyncClient",
                side_effect=lambda **kwargs: async_client_class(
                    transport=transport, **kwargs
                ),
            ):
                return [
                    token
                    async for token in OllamaClient().chat_stream("Test question")
                ]

        tokens = async_to_sync(run_chat)()

        self.assertFalse(requests[0]["think"])
        self.assertEqual(tokens, ["Grounded answer"])


class SourceLifecycleTests(TestCase):
    def test_ingestion_skips_unchanged_document_and_replaces_stale_chunks(self) -> None:
        source = Source.objects.create(url="https://example.test/article")
        ollama = FakeOllama()

        async def run_ingestion(body: str) -> bool:
            transport = httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    text=f"<article><h1>Example</h1><p>{body}</p></article>",
                )
            )
            async with httpx.AsyncClient(transport=transport) as client:
                return await ingest_source(source, client, ollama)

        self.assertTrue(async_to_sync(run_ingestion)("original article text"))
        first_hash = Chunk.objects.get().content_sha256
        self.assertFalse(async_to_sync(run_ingestion)("original article text"))
        self.assertEqual(ollama.calls, 1)

        self.assertTrue(async_to_sync(run_ingestion)("updated article text"))

        self.assertEqual(ollama.calls, 2)
        self.assertEqual(Chunk.objects.count(), 1)
        self.assertNotEqual(Chunk.objects.get().content_sha256, first_hash)
        self.assertIn("updated article text", Chunk.objects.get().content)

    def test_deleting_a_source_removes_its_document_and_chunks(self) -> None:
        source = Source.objects.create(url="https://example.test/article")
        document = IndexedDocument.objects.create(
            source=source,
            title="Example",
            content_sha256=sha256_text("article"),
        )
        Chunk.objects.create(
            document=document,
            position=0,
            content="article",
            content_sha256=sha256_text("article"),
            embedding=[0.0] * 768,
        )

        source.delete()

        self.assertFalse(IndexedDocument.objects.exists())
        self.assertFalse(Chunk.objects.exists())


class SourceIngestionSchedulingTests(TestCase):
    @patch("ragapp.signals.ingest_source_task.delay")
    def test_saving_an_enabled_source_queues_ingestion_after_commit(
        self, enqueue
    ) -> None:
        with self.captureOnCommitCallbacks(execute=True):
            source = Source.objects.create(url="https://example.test/queued")

        enqueue.assert_called_once_with(source.pk)

    @patch("ragapp.signals.ingest_source_task.delay")
    def test_saving_a_disabled_source_does_not_queue_ingestion(
        self, enqueue
    ) -> None:
        with self.captureOnCommitCallbacks(execute=True):
            Source.objects.create(
                url="https://example.test/disabled",
                enabled=False,
            )

        enqueue.assert_not_called()
