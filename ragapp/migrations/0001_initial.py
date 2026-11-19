import pgvector.django
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        pgvector.django.VectorExtension(),
        migrations.CreateModel(
            name="Source",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("url", models.URLField(max_length=2048, unique=True)),
                ("enabled", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["url"]},
        ),
        migrations.CreateModel(
            name="IndexedDocument",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=500)),
                ("content_sha256", models.CharField(db_index=True, max_length=64)),
                ("indexed_at", models.DateTimeField(auto_now=True)),
                ("source", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="document", to="ragapp.source")),
            ],
        ),
        migrations.CreateModel(
            name="Chunk",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("position", models.PositiveIntegerField()),
                ("content", models.TextField()),
                ("content_sha256", models.CharField(db_index=True, max_length=64)),
                ("embedding", pgvector.django.vector.VectorField(dimensions=768)),
                ("document", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="chunks", to="ragapp.indexeddocument")),
            ],
            options={"ordering": ["position"]},
        ),
        migrations.AddConstraint(
            model_name="chunk",
            constraint=models.UniqueConstraint(fields=("document", "position"), name="unique_document_chunk_position"),
        ),
    ]
