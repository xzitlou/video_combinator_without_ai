"""RQ job entry points. They run in `rqworker video`, never inside a request."""

import tempfile
from pathlib import Path

from django.core.files import File

from . import ffmpeg, services
from .models import Clip, Variant


def normalize_clip(clip_id):
    clip = Clip.objects.get(pk=clip_id)
    if not clip.file:
        return
    clip.status = Clip.Status.NORMALIZING
    clip.save(update_fields=["status"])

    try:
        with tempfile.TemporaryDirectory() as tmp:
            dst = Path(tmp) / "normalized.mp4"
            clip.duration = ffmpeg.normalize(clip.file.path, dst)
            with open(dst, "rb") as f:
                clip.normalized_file.save(f"{clip.type}_{clip.order:02d}.mp4", File(f), save=False)
    except Exception as exc:
        clip.status = Clip.Status.FAILED
        clip.error = str(exc)
        services.clip_normalized(clip)
        if isinstance(exc, ffmpeg.FFmpegError):
            return  # bad input file: reported to the user, not a job failure
        raise

    # Only the normalized copy is needed from here on; don't keep the upload around.
    clip.file.delete(save=False)
    clip.status = Clip.Status.READY
    clip.error = ""
    services.clip_normalized(clip)


def render_variant(variant_id):
    if not services.claim_variant(variant_id):
        return  # already rendered or being rendered by another job
    variant = Variant.objects.select_related("project", "hook", "body", "closer").get(pk=variant_id)

    try:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "output.mp4"
            ffmpeg.concat([clip.normalized_file.path for clip in variant.clips], out, tmp)
            with open(out, "rb") as f:
                variant.output_file.save(variant.output_name, File(f), save=False)
        services.mark_variant_done(variant)
    except Exception as exc:
        variant.status = Variant.Status.FAILED
        variant.error = str(exc)
        variant.save(update_fields=["status", "error"])
        raise
    finally:
        services.finalize_project_if_done(variant.project_id)


def purge_expired():
    return services.purge_expired()
