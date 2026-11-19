
import asyncio
import hashlib
import logging
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from asgiref.sync import sync_to_async
from bs4 import BeautifulSoup
from django.conf import settings
from django.db import transaction
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pgvector.django import CosineDistance

from ragapp.models import Chunk, IndexedDocument, Source
from ragapp.ollama import OllamaClient


logger = logging.getLogger(__name__)

text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=settings.RAG_CHUNK_SIZE,
    chunk_overlap=settings.RAG_CHUNK_OVERLAP,
    add_start_index=True,
)


@dataclass(frozen=True)
class ScrapedPage:
    title: str
    content: str


@dataclass(frozen=True)
class SearchContext:
    text: str
    sources: list[dict]


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def validate_source_url(url):
    parsed = urlsplit(url)

    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError(f"Invalid source URL: {url}")


async def scrape_url(client, url):
    validate_source_url(url)

    response = await client.get(url)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    for tag in soup.select("script, style, nav, footer, aside, noscript, form"):
        tag.decompose()

    content_node = soup.find("article") or soup.find("main") or soup.body or soup
    title_node = content_node.find("h1") or soup.find("h1") or soup.title

    title = title_node.get_text(" ", strip=True) if title_node else url

    lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in content_node.get_text(separator="\n", strip=True).splitlines()
    ]

    content = "\n".join(line for line in lines if line)

    if not content:
        raise ValueError(f"No readable content found: {url}")

    return ScrapedPage(title, content)


def load_index_snapshot(source_id):
    document = IndexedDocument.objects.filter(source_id=source_id).first()

    if not document:
        return None, {}

    embeddings = {
        chunk.content_sha256: list(chunk.embedding)
        for chunk in document.chunks.all()
    }

    return document.content_sha256, embeddings


def store_document(source_id, title, document_hash, chunks):
    with transaction.atomic():
        document, _ = IndexedDocument.objects.update_or_create(
            source_id=source_id,
            defaults={
                "title": title,
                "content_sha256": document_hash,
            },
        )

        document.chunks.all().delete()

        Chunk.objects.bulk_create([
            Chunk(
                document=document,
                position=position,
                content=content,
                content_sha256=chunk_hash,
                embedding=embedding,
            )
            for position, content, chunk_hash, embedding in chunks
        ])


async def ingest_source(source, client, ollama):
    page = await scrape_url(client, source.url)
    document_hash = sha256_text(page.content)

    previous_hash, existing_embeddings = await sync_to_async(
        load_index_snapshot,
        thread_sensitive=True,
    )(source.pk)

    if document_hash == previous_hash:
        logger.info("Unchanged source: %s", source.url)
        return False

    contents = text_splitter.split_text(page.content)

    if not contents:
        raise ValueError(f"No chunks generated for source: {source.url}")

    hashes = [sha256_text(content) for content in contents]

    missing_hashes = [
        chunk_hash
        for chunk_hash in dict.fromkeys(hashes)
        if chunk_hash not in existing_embeddings
    ]

    content_by_hash = dict(zip(hashes, contents))

    new_vectors = await ollama.embed_texts([
        content_by_hash[chunk_hash]
        for chunk_hash in missing_hashes
    ])

    if len(new_vectors) != len(missing_hashes):
        raise ValueError("Embedding count does not match requested chunks.")

    embeddings = dict(zip(missing_hashes, new_vectors))

    chunks = [
        (
            position,
            content,
            chunk_hash,
            existing_embeddings.get(chunk_hash, embeddings.get(chunk_hash)),
        )
        for position, (content, chunk_hash) in enumerate(zip(contents, hashes))
    ]

    await sync_to_async(
        store_document,
        thread_sensitive=True,
    )(
        source.pk,
        page.title,
        document_hash,
        chunks,
    )

    logger.info("Indexed source %s with %d chunks", source.url, len(chunks))

    return True


async def ingest_enabled_sources():
    sources = await sync_to_async(
        list,
        thread_sensitive=True,
    )(
        Source.objects.filter(enabled=True).order_by("pk")
    )

    return await _ingest_sources(sources)


async def ingest_source_by_id(source_id):
    source = await sync_to_async(
        lambda: Source.objects.filter(
            pk=source_id,
            enabled=True,
        ).first(),
        thread_sensitive=True,
    )()

    if not source:
        logger.info("Skipping missing or disabled source id=%d", source_id)
        return False

    _, updated = await _ingest_sources([source])

    return bool(updated)


async def _ingest_sources(sources):
    if not sources:
        return 0, 0

    timeout = httpx.Timeout(25, connect=5)
    limits = httpx.Limits(
        max_connections=5,
        max_keepalive_connections=5,
    )

    ollama = OllamaClient()

    async with httpx.AsyncClient(
        timeout=timeout,
        limits=limits,
        follow_redirects=True,
        headers={"User-Agent": "DjangoLocalRAG/1.0"},
    ) as client:

        semaphore = asyncio.Semaphore(5)

        async def ingest_one(source):
            async with semaphore:
                return await ingest_source(source, client, ollama)

        results = await asyncio.gather(
            *(ingest_one(source) for source in sources)
        )

    return len(sources), sum(results)


def retrieve_context(embedding):
    rows = list(
        Chunk.objects.filter(document__source__enabled=True)
        .annotate(distance=CosineDistance("embedding", embedding))
        .order_by("distance")
        .values(
            "content",
            "document__title",
            "document__source__url",
        )[:settings.RAG_TOP_K]
    )

    sources = []
    source_numbers = {}
    blocks = []

    for row in rows:
        url = row["document__source__url"]

        if url not in source_numbers:
            source_numbers[url] = len(sources) + 1
            sources.append({
                "title": row["document__title"],
                "url": url,
            })

        number = source_numbers[url]

        blocks.append(
            f"[Source {number}] {row['document__title']}\n"
            f"URL: {url}\n"
            f"{row['content']}"
        )

    return SearchContext(
        text="\n\n".join(blocks),
        sources=sources,
    )
