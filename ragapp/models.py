from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from pgvector.django import VectorField


class Source(models.Model):
    url = models.URLField(unique=True, max_length=2048)
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["url"]

    def clean(self) -> None:
        super().clean()
        if self.url:
            try:
                parsed = urlsplit(self.url)
            except ValueError as exc:
                raise ValidationError({"url": "Enter a valid HTTP(S) URL."}) from exc
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
            ):
                raise ValidationError(
                    {"url": "Enter an HTTP(S) URL without embedded credentials."}
                )

    def __str__(self) -> str:
        return self.url


class IndexedDocument(models.Model):
    source = models.OneToOneField(
        Source, related_name="document", on_delete=models.CASCADE
    )
    title = models.CharField(max_length=500)
    content_sha256 = models.CharField(max_length=64, db_index=True)
    indexed_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.title


class Chunk(models.Model):
    document = models.ForeignKey(
        IndexedDocument, related_name="chunks", on_delete=models.CASCADE
    )
    position = models.PositiveIntegerField()
    content = models.TextField()
    content_sha256 = models.CharField(max_length=64, db_index=True)
    embedding = VectorField(dimensions=settings.EMBEDDING_DIMENSIONS)

    class Meta:
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(
                fields=["document", "position"], name="unique_document_chunk_position"
            )
        ]

    def __str__(self) -> str:
        return f"{self.document_id}:{self.position}"
