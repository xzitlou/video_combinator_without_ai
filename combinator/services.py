"""Domain operations. Views and jobs call these; they never run ffmpeg themselves."""

import hashlib
from datetime import timedelta
import django_rq
from django.conf import settings
from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from . import variation
from .models import Clip, Project, Variant


class GenerationError(Exception):
    pass


def enqueue(func, *args):
    django_rq.get_queue("video").enqueue(func, *args)


def file_sha256(uploaded_file):
    digest = hashlib.sha256()
    for chunk in uploaded_file.chunks():
        digest.update(chunk)
    uploaded_file.seek(0)
    return digest.hexdigest()


def add_clip(project, clip_type, uploaded_file):
    # Hash outside the lock: it reads the whole file.
    sha256 = file_sha256(uploaded_file)
    # Uploads arrive in parallel; lock the project so each clip gets its own number
    # (GA01, GA02…). The file is written after the lock is released.
    with transaction.atomic():
        project = Project.objects.select_for_update().get(pk=project.pk)
        if project.status != Project.Status.DRAFT:
            raise GenerationError("Este proyecto ya fue generado; crea uno nuevo para añadir clips.")
        # The same footage twice would silently produce byte-identical videos.
        twin = project.clips.filter(sha256=sha256).first()
        if twin:
            raise GenerationError(f"{uploaded_file.name} es el mismo video que {twin.code} ({twin.original_name}).")
        last = project.clips.filter(type=clip_type).aggregate(m=Max("order"))["m"] or 0
        clip = Clip.objects.create(
            project=project, type=clip_type, original_name=uploaded_file.name, order=last + 1, sha256=sha256
        )
    clip.file.save(f"{clip_type}_{clip.order:02d}{_ext(uploaded_file.name)}", uploaded_file)

    from .tasks import normalize_clip

    transaction.on_commit(lambda: enqueue(normalize_clip, clip.pk))
    return clip


def _ext(name):
    return "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""


def set_clip_enabled(clip, enabled):
    if clip.project.status != Project.Status.DRAFT:
        raise GenerationError("Este proyecto ya fue generado.")
    clip.enabled = enabled
    clip.save(update_fields=["enabled"])


def delete_clip(clip):
    if clip.project.status != Project.Status.DRAFT:
        raise GenerationError("Este proyecto ya fue generado.")
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


def combinations(hooks, bodies, closers, mode):
    if mode == Project.Mode.DISTINCT:
        return variation.distinct_combinations(hooks, bodies, closers)
    return variation.all_combinations(hooks, bodies, closers)


def variant_count(project, mode=None):
    hooks, bodies, closers = enabled_clips(project)
    return variation.combination_count(mode or project.mode, len(hooks), len(bodies), len(closers))


def generate_variants(project_id, mode=Project.Mode.DISTINCT):
    """Create one Variant per hook × body (× closer) combination.

    Clips may still be normalizing: each variant is queued as soon as its clips are ready,
    here or from normalize_clip (see enqueue_ready_variants).
    """
    with transaction.atomic():
        project = Project.objects.select_for_update().get(pk=project_id)
        if project.status != Project.Status.DRAFT or project.uploads_purged_at:
            raise GenerationError("Este proyecto ya fue generado.")

        if mode not in Project.Mode.values:
            raise GenerationError("Modo de combinación no válido.")
        hooks, bodies, closers = enabled_clips(project)
        if not hooks or not bodies:
            raise GenerationError("Necesitas al menos un gancho y un contenido activos.")
        total = variation.combination_count(mode, len(hooks), len(bodies), len(closers))
        if total > settings.MAX_VARIANTS_PER_RUN:
            raise GenerationError(
                f"{total} videos superan el máximo de {settings.MAX_VARIANTS_PER_RUN} por ejecución."
            )
        failed = [c.original_name for c in hooks + bodies + closers if c.status == Clip.Status.FAILED]
        if failed:
            raise GenerationError(f"Quita los clips que no se pudieron procesar: {', '.join(failed)}.")

        # Order by footage, not by row: two rows with the same bytes count as the same clip.
        footage = lambda clip: clip.content_key  # noqa: E731
        ordered = variation.publication_order(combinations(hooks, bodies, closers, mode), key=footage)
        days = variation.publishing_plan(ordered, settings.PUBLISH_MAX_PER_DAY, key=footage)
        # Number videos day by day, so #001… is also the order to post them in.
        planned = [(day, ordered[i]) for day, indexes in enumerate(days, start=1) for i in indexes]
        variants = Variant.objects.bulk_create(
            Variant(project=project, hook=h, body=b, closer=c, position=position, publish_day=day)
            for position, (day, (h, b, c)) in enumerate(planned, start=1)
        )
        project.status = Project.Status.PROCESSING
        project.mode = mode
        project.save(update_fields=["status", "mode"])
        enqueue_ready_variants(project)
    return variants


def enqueue_ready_variants(project):
    """Queue pending variants whose clips are all normalized.

    Must run inside a transaction holding the project row lock (generate_variants and
    clip_normalized both take it), so a clip finishing while the variants are being
    created can't be missed. Enqueuing a variant twice is harmless: render_variant claims
    it atomically.
    """
    from .tasks import render_variant

    not_ready = Clip.objects.filter(project=project).exclude(status=Clip.Status.READY)
    ids = list(
        project.variants.filter(status=Variant.Status.PENDING)
        .exclude(hook__in=not_ready)
        .exclude(body__in=not_ready)
        .exclude(closer__in=not_ready)
        .values_list("pk", flat=True)
    )
    transaction.on_commit(lambda: [enqueue(render_variant, pk) for pk in ids])


def clip_normalized(clip):
    """Persist a normalize result and move the project's pipeline forward."""
    with transaction.atomic():
        project = Project.objects.select_for_update().get(pk=clip.project_id)
        clip.save(update_fields=["file", "normalized_file", "duration", "status", "error"])
        if project.status != Project.Status.PROCESSING:
            return
        if clip.status == Clip.Status.READY:
            enqueue_ready_variants(project)
            return
        project.variants.filter(
            Q(hook=clip) | Q(body=clip) | Q(closer=clip), status=Variant.Status.PENDING
        ).update(status=Variant.Status.FAILED, error=f"No se pudo procesar el clip {clip.code}.")
    finalize_project_if_done(project.pk)


def claim_variant(variant_id):
    """Atomically move a variant from pending to processing; False if someone else did."""
    return bool(
        Variant.objects.filter(pk=variant_id, status=Variant.Status.PENDING).update(
            status=Variant.Status.PROCESSING
        )
    )


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
