from django.contrib import admin

from ragapp.models import Chunk, IndexedDocument, Source


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = ("url", "enabled", "updated_at")
    list_filter = ("enabled",)
    search_fields = ("url",)


class ChunkInline(admin.TabularInline):
    model = Chunk
    extra = 0
    can_delete = False
    readonly_fields = ("position", "content_sha256", "content")
    fields = ("position", "content_sha256", "content")


@admin.register(IndexedDocument)
class IndexedDocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "source", "content_sha256", "indexed_at")
    search_fields = ("title", "source__url")
    readonly_fields = ("source", "title", "content_sha256", "indexed_at")
    inlines = (ChunkInline,)

    def has_add_permission(self, request) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False
