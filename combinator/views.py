from django.http import FileResponse, Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.utils import timezone

from .models import Variant


def download_variant(request, pk):
    """Only path to a rendered file; refuses once the download window has closed."""
    variant = get_object_or_404(Variant.objects.select_related("project", "hook", "body", "closer"), pk=pk)
    if variant.status != Variant.Status.DONE or not variant.output_file or variant.expires_at <= timezone.now():
        raise Http404("Este video ya no está disponible.")

    storage = variant.output_file.storage
    if hasattr(storage, "bucket_name"):
        # S3: short-lived signed URL (querystring_expire), so the link can't outlive the window.
        return HttpResponseRedirect(
            storage.url(
                variant.output_file.name,
                parameters={"ResponseContentDisposition": f'attachment; filename="{variant.output_name}"'},
            )
        )
    return FileResponse(variant.output_file.open("rb"), as_attachment=True, filename=variant.output_name)
