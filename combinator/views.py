import zipfile

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.http import (
    FileResponse,
    Http404,
    JsonResponse,
    StreamingHttpResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import services
from .forms import ProjectForm, SignupForm
from .models import Clip, Project, Variant

ACCEPTED_EXTENSIONS = (".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi")


# --- Auth ----------------------------------------------------------------------

def signup(request):
    if request.user.is_authenticated:
        return redirect("combinator:project_list")
    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        login(request, form.save())
        return redirect("combinator:project_list")
    return render(request, "registration/signup.html", {"form": form})


# --- Projects ------------------------------------------------------------------

def _project(request, pk, **filters):
    return get_object_or_404(Project, pk=pk, owner=request.user, **filters)


@login_required
def project_list(request):
    projects = list(request.user.projects.all())
    for project in projects:
        project.clip_total = project.clips.count()
        project.variant_total = project.variants.count()
        project.ready_total = project.variants.filter(
            status=Variant.Status.DONE, expires_at__gt=timezone.now()
        ).count()
    return render(request, "combinator/project_list.html", {"projects": projects, "form": ProjectForm()})


@login_required
@require_POST
def project_create(request):
    form = ProjectForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Ponle un nombre a la campaña.")
        return redirect("combinator:project_list")
    project = form.save(commit=False)
    project.owner = request.user
    project.save()
    return redirect("combinator:project_detail", pk=project.pk)


@login_required
def project_detail(request, pk):
    project = _project(request, pk)
    clips = list(project.clips.all())
    columns = [
        {"type": t, "label": label, "clips": [c for c in clips if c.type == t]}
        for t, label in (
            (Clip.Type.HOOK, "Hooks"),
            (Clip.Type.BODY, "Bodies"),
            (Clip.Type.CLOSER, "Closers"),
        )
    ]
    variants = list(project.variants.select_related("hook", "body", "closer"))
    now = timezone.now()
    for variant in variants:
        variant.total_duration = sum(c.duration or 0 for c in variant.clips)
        variant.minutes_left = int((variant.expires_at - now).total_seconds() // 60) if variant.is_downloadable else 0
    max_duration = max((v.total_duration for v in variants), default=0)
    return render(request, "combinator/project_detail.html", {
        "project": project,
        "columns": columns,
        "variants": variants,
        "max_duration": max_duration,
        "ready_total": sum(1 for v in variants if v.is_downloadable),
        "count": services.variant_count(project),
        "max_variants": settings.MAX_VARIANTS_PER_RUN,
        "max_clip_mb": settings.MAX_CLIP_SIZE // (1024 * 1024),
        "accepted": ",".join(ACCEPTED_EXTENSIONS),
        "ttl_minutes": settings.OUTPUT_TTL_SECONDS // 60,
    })


@login_required
@require_POST
def project_generate(request, pk):
    project = _project(request, pk)
    try:
        services.generate_variants(project.pk)
    except services.GenerationError as exc:
        messages.error(request, str(exc))
    return redirect("combinator:project_detail", pk=pk)


def _clip_json(clip):
    return {
        "id": clip.pk,
        "type": clip.type,
        "code": clip.code,
        "name": clip.original_name,
        "status": clip.status,
        "duration": clip.duration,
        "enabled": clip.enabled,
        "error": clip.error,
    }


def _variant_json(variant, now):
    downloadable = variant.is_downloadable
    return {
        "id": variant.pk,
        "status": variant.status,
        "error": variant.error,
        "download_url": reverse("combinator:download_variant", args=[variant.pk]) if downloadable else None,
        "seconds_left": int((variant.expires_at - now).total_seconds()) if downloadable else None,
    }


@login_required
def project_status(request, pk):
    """Polled by the project page while clips normalize or variants render."""
    project = _project(request, pk)
    now = timezone.now()
    clips = list(project.clips.all())
    variants = list(project.variants.all())
    return JsonResponse({
        "status": project.status,
        "count": services.variant_count(project),
        "clips": [_clip_json(c) for c in clips],
        "variants": [_variant_json(v, now) for v in variants],
        "busy": any(c.status in (Clip.Status.PENDING, Clip.Status.NORMALIZING) for c in clips)
        or any(v.status not in Variant.FINISHED_STATUSES for v in variants),
    })


# --- Clips ---------------------------------------------------------------------

@login_required
@require_POST
def clip_upload(request, pk):
    project = _project(request, pk)
    clip_type = request.POST.get("type")
    upload = request.FILES.get("file")
    if clip_type not in Clip.Type.values or upload is None:
        return JsonResponse({"error": "Falta el archivo o el tipo de clip."}, status=400)
    if not upload.name.lower().endswith(ACCEPTED_EXTENSIONS):
        return JsonResponse({"error": "Formato no admitido. Sube MP4, MOV, WEBM, MKV o AVI."}, status=400)
    if upload.size > settings.MAX_CLIP_SIZE:
        mb = settings.MAX_CLIP_SIZE // (1024 * 1024)
        return JsonResponse({"error": f"El archivo supera los {mb} MB."}, status=400)
    try:
        clip = services.add_clip(project, clip_type, upload)
    except services.GenerationError as exc:
        return JsonResponse({"error": str(exc)}, status=409)
    return JsonResponse(_clip_json(clip), status=201)


def _clip(request, pk):
    return get_object_or_404(Clip.objects.select_related("project"), pk=pk, project__owner=request.user)


@login_required
@require_POST
def clip_toggle(request, pk):
    clip = _clip(request, pk)
    try:
        services.set_clip_enabled(clip, request.POST.get("enabled") == "1")
    except services.GenerationError as exc:
        return JsonResponse({"error": str(exc)}, status=409)
    return JsonResponse({"enabled": clip.enabled, "count": services.variant_count(clip.project)})


@login_required
@require_POST
def clip_delete(request, pk):
    clip = _clip(request, pk)
    project = clip.project
    try:
        services.delete_clip(clip)
    except services.GenerationError as exc:
        return JsonResponse({"error": str(exc)}, status=409)
    return JsonResponse({"count": services.variant_count(project)})


# --- Downloads -----------------------------------------------------------------

@login_required
def download_variant(request, pk):
    """Only path to a rendered file; refuses once the download window has closed."""
    variant = get_object_or_404(
        Variant.objects.select_related("project", "hook", "body", "closer"), pk=pk, project__owner=request.user
    )
    if not variant.is_downloadable:
        raise Http404("Este video ya no está disponible.")
    return FileResponse(variant.output_file.open("rb"), as_attachment=True, filename=variant.output_name)


class _ZipStream:
    """Write-only buffer that zipfile can target; the view yields whatever was written so far."""

    def __init__(self):
        self.chunks = []

    def write(self, data):
        self.chunks.append(bytes(data))
        return len(data)

    def flush(self):
        pass

    def drain(self):
        out = b"".join(self.chunks)
        self.chunks = []
        return out


def _zip_variants(variants):
    stream = _ZipStream()
    # Outputs are already compressed video, so store them; no CPU spent on deflate.
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_STORED) as zf:
        for variant in variants:
            info = zipfile.ZipInfo(variant.output_name, date_time=timezone.localtime(variant.completed_at).timetuple()[:6])
            with zf.open(info, "w", force_zip64=True) as dest, variant.output_file.open("rb") as src:
                for chunk in iter(lambda: src.read(1024 * 1024), b""):
                    dest.write(chunk)
                    yield stream.drain()
    yield stream.drain()


@login_required
def download_all(request, pk):
    project = _project(request, pk)
    variants = [
        v for v in project.variants.select_related("project", "hook", "body", "closer") if v.is_downloadable
    ]
    if not variants:
        raise Http404("No hay videos disponibles para descargar.")
    response = StreamingHttpResponse(_zip_variants(variants), content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="{project.slug}_variantes.zip"'
    return response
