from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from ragapp.models import Source
from ragapp.tasks import ingest_source_task


@receiver(post_save, sender=Source, dispatch_uid="ragapp.queue_source_ingestion")
def queue_source_ingestion(sender, instance: Source, **kwargs) -> None:
    if not instance.enabled:
        return

    source_id = instance.pk
    transaction.on_commit(lambda: ingest_source_task.delay(source_id))
