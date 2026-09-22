from django.contrib import admin

from .models import Clip, Project, Variant


class ClipInline(admin.TabularInline):
    model = Clip
    extra = 0
    fields = ("type", "order", "original_name", "enabled", "status", "duration")
    readonly_fields = ("status", "duration")


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "status", "created_at", "uploads_purged_at")
    list_filter = ("status",)
    inlines = [ClipInline]


@admin.register(Variant)
class VariantAdmin(admin.ModelAdmin):
    list_display = ("__str__", "project", "status", "completed_at", "expires_at")
    list_filter = ("status",)
    list_select_related = ("project", "hook", "body", "closer")
