import zipfile
from itertools import groupby

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import services, variation
from .forms import ProjectForm
from .models import Clip, Project, Variant

ACCEPTED_EXTENSIONS = (".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi")

COLUMNS = [
    {
        "type": Clip.Type.HOOK,
        "label": "Ganchos",
        "add": "Añadir ganchos",
        "help": "Los primeros 3 a 5 segundos. Su trabajo es frenar el scroll.",
    },
    {
        "type": Clip.Type.BODY,
        "label": "Contenido",
        "add": "Añadir contenido",
        "help": "El cuerpo del video. Mantiene la atención y puede durar más.",
    },
    {
        "type": Clip.Type.CLOSER,
        "label": "Cierres",
        "add": "Añadir cierres",
        "help": "Clip corto que pide una acción: seguir, comentar, comprar. Opcional.",
        "optional": True,
    },
]


def _wants_json(request):
    return "application/json" in request.headers.get("Accept", "")


def _annotate_similarity(variants):
    """Attach to each variant how much it overlaps with its closest sibling (see variation.py)."""
    results = variation.similarity(
        [(v.hook, v.body, v.closer) for v in variants], lambda clip: clip.duration, key=lambda clip: clip.content_key
    )
    for variant, result in zip(variants, results):
        nearest = variants[result["nearest"]] if result["nearest"] is not None else None
        variant.similar_level = result["level"]
        variant.similar_label = ""
        variant.similar_note = ""
        if nearest is None:
            continue
        # Only problems get a label; a plain row means the video is fine to publish.
        if result["level"] == "identical":
            variant.similar_label = f"Idéntico a #{nearest.number}"
            variant.similar_note = f"Es el mismo video que #{nearest.number}. Publica solo uno de los dos."
        elif result["level"] == "high":
            variant.similar_label = f"Casi igual a #{nearest.number}"
            variant.similar_note = f"Solo cambia el cierre respecto a #{nearest.number}. Publícalos con días de diferencia."
    return variants


# --- Projects ------------------------------------------------------------------

def _project(request, uuid):
    return get_object_or_404(Project, uuid=uuid, owner=request.user)


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
        messages.error(request, "Ponle un nombre al proyecto.")
        return redirect("combinator:project_list")
    project = form.save(commit=False)
    project.owner = request.user
    project.save()
    return redirect("combinator:project_detail", uuid=project.uuid)


@login_required
def project_detail(request, uuid):
    project = _project(request, uuid)
    clips = list(project.clips.all())
    columns = [{**column, "clips": [c for c in clips if c.type == column["type"]]} for column in COLUMNS]
    variants = _annotate_similarity(list(project.variants.select_related("hook", "body", "closer")))
    for variant in variants:
        variant.total_duration = sum(c.duration or 0 for c in variant.clips)
    downloadable = [v for v in variants if v.is_downloadable]
    days = [
        {"number": day, "variants": list(group)}
        for day, group in groupby(variants, key=lambda v: v.publish_day)
    ]
    for day in days:
        day["downloadable"] = sum(1 for v in day["variants"] if v.is_downloadable)
    return render(request, "combinator/project_detail.html", {
        "project": project,
        "columns": columns,
        "variants": variants,
        "days": days,
        "per_day": max((len(d["variants"]) for d in days), default=0),
        "max_duration": max((v.total_duration for v in variants), default=0),
        "ready_total": len(downloadable),
        "expires_at": min((v.expires_at for v in downloadable), default=None),
        "identical": sum(1 for v in variants if v.similar_level == "identical"),
        "near_duplicates": sum(1 for v in variants if v.similar_level == "high"),
        "modes": Project.Mode,
        "clips_ready": sum(1 for c in clips if c.is_ready),
        "clips_used": sum(1 for c in clips if c.enabled),
        "count": services.variant_count(project),
        "max_variants": settings.MAX_VARIANTS_PER_RUN,
        "max_clip_mb": settings.MAX_CLIP_SIZE // (1024 * 1024),
        "accepted": ",".join(ACCEPTED_EXTENSIONS),
        "ttl_minutes": settings.OUTPUT_TTL_SECONDS // 60,
    })


@login_required
@require_POST
def project_generate(request, uuid):
    project = _project(request, uuid)
    try:
        services.generate_variants(project.pk, request.POST.get("mode", Project.Mode.DISTINCT))
    except services.GenerationError as exc:
        if _wants_json(request):
            return JsonResponse({"error": str(exc)}, status=409)
        messages.error(request, str(exc))
    url = reverse("combinator:project_detail", args=[project.uuid])
    if _wants_json(request):
        return JsonResponse({"redirect": url})
    return redirect(url)


def _clip_json(clip):
    return {
        "uuid": str(clip.uuid),
        "type": clip.type,
        "code": clip.code,
        "name": clip.original_name,
        "status": clip.status,
        "duration": clip.duration,
        "enabled": clip.enabled,
        "error": clip.error,
        "toggle_url": reverse("combinator:clip_toggle", args=[clip.uuid]),
        "delete_url": reverse("combinator:clip_delete", args=[clip.uuid]),
    }


def _variant_json(variant):
    downloadable = variant.is_downloadable
    return {
        "uuid": str(variant.uuid),
        "status": variant.status,
        "error": variant.error,
        "download_url": reverse("combinator:download_variant", args=[variant.uuid]) if downloadable else None,
        "expires_at": variant.expires_at.isoformat() if downloadable else None,
        "publish_day": variant.publish_day,
        # Durations are only known once clips are normalized; the page redraws its strips from these.
        "segments": [clip.duration or 0 for clip in variant.clips],
        "similar_level": variant.similar_level,
        "similar_label": variant.similar_label,
        "similar_note": variant.similar_note,
    }


@login_required
def project_status(request, uuid):
    """Polled by the project page while clips normalize or variants render."""
    project = _project(request, uuid)
    clips = list(project.clips.all())
    variants = _annotate_similarity(list(project.variants.select_related("hook", "body", "closer")))
    return JsonResponse({
        "status": project.status,
        "count": services.variant_count(project),
        "clips": [_clip_json(c) for c in clips],
        "variants": [_variant_json(v) for v in variants],
        "busy": any(c.status in (Clip.Status.PENDING, Clip.Status.NORMALIZING) for c in clips)
        or any(v.status not in Variant.FINISHED_STATUSES for v in variants),
    })


# --- Clips ---------------------------------------------------------------------

@login_required
@require_POST
def clip_upload(request, uuid):
    project = _project(request, uuid)
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


def _clip(request, uuid):
    return get_object_or_404(Clip.objects.select_related("project"), uuid=uuid, project__owner=request.user)


@login_required
@require_POST
def clip_toggle(request, uuid):
    clip = _clip(request, uuid)
    try:
        services.set_clip_enabled(clip, request.POST.get("enabled") == "1")
    except services.GenerationError as exc:
        return JsonResponse({"error": str(exc)}, status=409)
    return JsonResponse({"enabled": clip.enabled, "count": services.variant_count(clip.project)})


@login_required
@require_POST
def clip_delete(request, uuid):
    clip = _clip(request, uuid)
    project = clip.project
    try:
        services.delete_clip(clip)
    except services.GenerationError as exc:
        return JsonResponse({"error": str(exc)}, status=409)
    return JsonResponse({"count": services.variant_count(project)})


# --- Downloads -----------------------------------------------------------------

@login_required
def download_variant(request, uuid):
    """Only path to a rendered file; refuses once the download window has closed."""
    variant = get_object_or_404(
        Variant.objects.select_related("project", "hook", "body", "closer"), uuid=uuid, project__owner=request.user
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


def _zip_name(variant):
    """One folder per publishing day, so the ZIP reads as the plan."""
    if variant.publish_day:
        return f"dia-{variant.publish_day:02d}/{variant.output_name}"
    return variant.output_name


def _zip_variants(variants):
    stream = _ZipStream()
    # Outputs are already compressed video, so store them; no CPU spent on deflate.
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_STORED) as zf:
        for variant in variants:
            info = zipfile.ZipInfo(_zip_name(variant), date_time=timezone.localtime(variant.completed_at).timetuple()[:6])
            with zf.open(info, "w", force_zip64=True) as dest, variant.output_file.open("rb") as src:
                for chunk in iter(lambda: src.read(1024 * 1024), b""):
                    dest.write(chunk)
                    yield stream.drain()
    yield stream.drain()


@login_required
def download_all(request, uuid):
    """ZIP of every downloadable video, or of one publishing day with ?day=N."""
    project = _project(request, uuid)
    variants = project.variants.select_related("project", "hook", "body", "closer")
    day = request.GET.get("day")
    if day:
        if not day.isdigit():
            raise Http404("Día no válido.")
        variants = variants.filter(publish_day=int(day))
    variants = [v for v in variants if v.is_downloadable]
    if not variants:
        raise Http404("No hay videos disponibles para descargar.")
    suffix = f"dia-{int(day):02d}" if day else "videos"
    response = StreamingHttpResponse(_zip_variants(variants), content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="{project.slug}_{suffix}.zip"'
    return response
