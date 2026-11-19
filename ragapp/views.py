import asyncio
import json
import logging

import httpx
from asgiref.sync import sync_to_async
from django.db import DatabaseError
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from ragapp.ollama import OllamaClient, OllamaError
from ragapp.services import retrieve_context

logger = logging.getLogger(__name__)


def index(request):
    return render(request, "ragapp/index.html")


def sse_event(event, data):
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@require_GET
async def chat(request):
    question = request.GET.get("q", "").strip()

    if not question:
        return JsonResponse({"error": "Question is required."}, status=400)

    if len(question) > 2000:
        return JsonResponse({"error": "Question is too long."}, status=400)

    async def stream():
        try:
            ollama = OllamaClient()

            # Generate query embedding.
            embedding = (await ollama.embed_texts([question]))[0]

            # Retrieve relevant documents.
            context = await sync_to_async(retrieve_context)(embedding)

            yield sse_event("sources", context.sources)

            if not context.text:
                yield sse_event("token", {
                    "text": "I don't know based on the configured sources."
                })
                yield sse_event("done", {})
                return

            # Generate answer.
            prompt = (
                f"Question:\n{question}\n\n"
                f"Context:\n{context.text}\n\n"
                "Answer:"
            )

            async for token in ollama.chat_stream(prompt):
                yield sse_event("token", {"text": token})

            yield sse_event("done", {})

        except asyncio.CancelledError:
            raise

        except (httpx.HTTPError, OllamaError, DatabaseError, ValueError):
            logger.exception("RAG request failed")

            yield sse_event("error", {
                "message": "Something went wrong. Check the server logs."
            })

        except Exception:
            logger.exception("Unexpected RAG error")

            yield sse_event("error", {
                "message": "An unexpected error occurred."
            })

    response = StreamingHttpResponse(
        stream(),
        content_type="text/event-stream"
    )

    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"

    return response