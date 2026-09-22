from django.db import models
from django.utils.text import slugify


class Project(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Borrador"
        PROCESSING = "processing", "Procesando"
        DONE = "done", "Terminado"

    name = models.CharField(max_length=200)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    created_at = models.DateTimeField(auto_now_add=True)
    # Set once every uploaded file of the project has been deleted.
    uploads_purged_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    @property
    def slug(self):
        return slugify(self.name) or f"project-{self.pk}"


def clip_upload_to(clip, filename):
    return f"projects/{clip.project_id}/clips/original/{filename}"


def clip_normalized_to(clip, filename):
    return f"projects/{clip.project_id}/clips/normalized/{filename}"


class Clip(models.Model):
    class Type(models.TextChoices):
        HOOK = "hook", "Hook"
        BODY = "body", "Body"
        CLOSER = "closer", "Closer"

    class Status(models.TextChoices):
        PENDING = "pending", "Pendiente"
        NORMALIZING = "normalizing", "Normalizando"
        READY = "ready", "Listo"
        FAILED = "failed", "Error"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="clips")
    type = models.CharField(max_length=10, choices=Type.choices)
    original_name = models.CharField(max_length=255)
    file = models.FileField(upload_to=clip_upload_to, max_length=500, blank=True)
    normalized_file = models.FileField(upload_to=clip_normalized_to, max_length=500, blank=True)
    duration = models.FloatField(null=True, blank=True)
    order = models.PositiveIntegerField(default=0)
    enabled = models.BooleanField(default=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["type", "order", "pk"]

    def __str__(self):
        return f"{self.get_type_display()} {self.order}: {self.original_name}"

    @property
    def code(self):
        """Short label used in variant names, e.g. H02 / B04 / C01."""
        return f"{self.type[0].upper()}{self.order:02d}"


def variant_output_to(variant, filename):
    return f"projects/{variant.project_id}/outputs/{filename}"


class Variant(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pendiente"
        PROCESSING = "processing", "Procesando"
        DONE = "done", "Listo"
        FAILED = "failed", "Error"
        EXPIRED = "expired", "Expirado"

    FINISHED_STATUSES = (Status.DONE, Status.FAILED, Status.EXPIRED)

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="variants")
    # Clip rows are kept after their files are purged, so variants stay traceable
    # (needed later to aggregate performance per hook/body/closer).
    hook = models.ForeignKey(Clip, on_delete=models.PROTECT, related_name="+")
    body = models.ForeignKey(Clip, on_delete=models.PROTECT, related_name="+")
    closer = models.ForeignKey(Clip, on_delete=models.PROTECT, related_name="+")
    output_file = models.FileField(upload_to=variant_output_to, max_length=500, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.UniqueConstraint(fields=["project", "hook", "body", "closer"], name="unique_variant_combo"),
        ]

    def __str__(self):
        return self.label

    @property
    def label(self):
        return f"{self.hook.code} + {self.body.code} + {self.closer.code}"

    @property
    def output_name(self):
        return f"{self.project.slug}_{self.hook.code}_{self.body.code}_{self.closer.code}.mp4".lower()
