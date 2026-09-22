"""Domain operations. Views and jobs call these; they never run ffmpeg themselves."""

from datetime import timedelta
from itertools import product

import django_rq
from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from .models import Clip, Project, Variant


class GenerationError(Exception):
    pass


def enqueue(func, *args):
    django_rq.get_queue("video").enqueue(func, *args)


def add_clip(project, clip_type, uploaded_file):
    # Uploads arrive in parallel; lock the project so each clip gets its own number
    # (GA01, GA02…). The file is written after the lock is released.
    with transaction.atomic():
        project = Project.objects.select_for_update().get(pk=project.pk)
        if project.status != Project.Status.DRAFT:
            raise GenerationError("Este lote ya fue generado; crea uno nuevo para añadir clips.")
        last = project.clips.filter(type=clip_type).aggregate(m=Max("order"))["m"] or 0
        clip = Clip.objects.create(
            project=project, type=clip_type, original_name=uploaded_file.name, order=last + 1
        )
    clip.file.save(f"{clip_type}_{clip.order:02d}{_ext(uploaded_file.name)}", uploaded_file)

    from .tasks import normalize_clip

    transaction.on_commit(lambda: enqueue(normalize_clip, clip.pk))
    return clip


def _ext(name):
    return "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""


def set_clip_enabled(clip, enabled):
    if clip.project.status != Project.Status.DRAFT:
        raise GenerationError("Este lote ya fue generado.")
    clip.enabled = enabled
    clip.save(update_fields=["enabled"])


def delete_clip(clip):
    if clip.project.status != Project.Status.DRAFT:
        raise GenerationError("Este lote ya fue generado.")
    for field in (clip.file, clip.normalized_file):
        if field:
            field.delete(save=False)
    clip.delete()


def enabled_clips(project):
    clips = project.clips.filter(enabled=True)
    return (
        list(clips.filter(type=Clip.Type.HOOK)),
        list(clips.filter(type=Clip.Type.BODY)),
        list(clips.filter(type=Clip.Type.CLOSER)),
    )


def combinations(hooks, bodies, closers):
    """hook × body × closer; with no closers every variant is just hook + body."""
    return product(hooks, bodies, closers or [None])


def variant_count(project):
    hooks, bodies, closers = enabled_clips(project)
    return len(hooks) * len(bodies) * max(len(closers), 1)


def generate_variants(project_id):
    """Create one Variant per hook × body (× closer) combination and queue the renders."""
    from .tasks import render_variant

    with transaction.atomic():
        project = Project.objects.select_for_update().get(pk=project_id)
        if project.status != Project.Status.DRAFT or project.uploads_purged_at:
            raise GenerationError("Este lote ya fue generado.")

        hooks, bodies, closers = enabled_clips(project)
        if not hooks or not bodies:
            raise GenerationError("Necesitas al menos un gancho y un contenido activos.")
        total = len(hooks) * len(bodies) * max(len(closers), 1)
        if total > settings.MAX_VARIANTS_PER_RUN:
            raise GenerationError(
                f"{total} videos superan el máximo de {settings.MAX_VARIANTS_PER_RUN} por ejecución."
            )
        if any(c.status != Clip.Status.READY for c in hooks + bodies + closers):
            raise GenerationError("Espera a que todos los clips activos terminen de prepararse.")

        variants = Variant.objects.bulk_create(
            Variant(project=project, hook=h, body=b, closer=c)
            for h, b, c in combinations(hooks, bodies, closers)
        )
        project.status = Project.Status.PROCESSING
        project.save(update_fields=["status"])

        ids = [v.pk for v in variants]
        transaction.on_commit(lambda: [enqueue(render_variant, pk) for pk in ids])
    return variants


def mark_variant_done(variant):
    now = timezone.now()
    variant.status = Variant.Status.DONE
    variant.completed_at = now
    variant.expires_at = now + timedelta(seconds=settings.OUTPUT_TTL_SECONDS)
    variant.save(update_fields=["output_file", "status", "completed_at", "expires_at"])


def purge_clip_files(project):
    """Delete every uploaded/normalized file of the project. Clip rows are kept."""
    for clip in project.clips.all():
        for field in (clip.file, clip.normalized_file):
            if field:
                field.delete(save=False)
        clip.save(update_fields=["file", "normalized_file"])
    project.uploads_purged_at = timezone.now()


def finalize_project_if_done(project_id):
    """Once every variant has finished, delete the project's uploads. Safe to call concurrently."""
    with transaction.atomic():
        project = Project.objects.select_for_update().get(pk=project_id)
        if project.status != Project.Status.PROCESSING:
            return False
        if project.variants.exclude(status__in=Variant.FINISHED_STATUSES).exists():
            return False
        purge_clip_files(project)
        project.status = Project.Status.DONE
        project.save(update_fields=["status", "uploads_purged_at"])
        return True


def purge_expired(now=None):
    """Delete outputs past their download window and uploads of abandoned drafts."""
    now = now or timezone.now()

    expired = 0
    for variant in Variant.objects.filter(status=Variant.Status.DONE, expires_at__lte=now):
        if variant.output_file:
            variant.output_file.delete(save=False)
        variant.status = Variant.Status.EXPIRED
        variant.save(update_fields=["output_file", "status"])
        expired += 1

    abandoned = 0
    cutoff = now - timedelta(seconds=settings.ABANDONED_UPLOAD_TTL_SECONDS)
    stale = Project.objects.filter(
        status=Project.Status.DRAFT, uploads_purged_at__isnull=True, created_at__lte=cutoff
    )
    for project in stale:
        with transaction.atomic():
            purge_clip_files(project)
            project.save(update_fields=["uploads_purged_at"])
        abandoned += 1

    return expired, abandoned
