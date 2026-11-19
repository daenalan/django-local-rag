import asyncio

from celery import shared_task

from ragapp.services import ingest_source_by_id


@shared_task
def ingest_source_task(source_id: int) -> bool:
    return asyncio.run(ingest_source_by_id(source_id))
