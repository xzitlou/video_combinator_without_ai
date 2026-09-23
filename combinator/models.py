import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.text import slugify


class Project(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Borrador"
        PROCESSING = "processing", "Procesando"
        DONE = "done", "Terminado"

    class Mode(models.TextChoices):
        # See variation.py: "distinct" never repeats a hook+body pair.
        DISTINCT = "distinct", "Variantes distintas"
        ALL = "all", "Todas las combinaciones"

    # Public identifier used in URLs and JSON; the integer pk never leaves the server.
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="projects")
    name = models.CharField(max_length=200)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    mode = models.CharField(max_length=10, choices=Mode.choices, default=Mode.DISTINCT)
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
        HOOK = "hook", "Gancho"
        BODY = "body", "Contenido"
        CLOSER = "closer", "Cierre"

    # Contenido and Cierre share an initial, so codes use two letters: GA01, CO02, CI01.
    CODE_PREFIX = {Type.HOOK: "GA", Type.BODY: "CO", Type.CLOSER: "CI"}

    class Status(models.TextChoices):
        PENDING = "pending", "Pendiente"
        NORMALIZING = "normalizing", "Normalizando"
        READY = "ready", "Listo"
        FAILED = "failed", "Error"

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="clips")
    type = models.CharField(max_length=10, choices=Type.choices)
    original_name = models.CharField(max_length=255)
    file = models.FileField(upload_to=clip_upload_to, max_length=500, blank=True)
    normalized_file = models.FileField(upload_to=clip_normalized_to, max_length=500, blank=True)
    duration = models.FloatField(null=True, blank=True)
    # Fingerprint of the uploaded bytes: catches the same file uploaded twice. Kept after the
    # files are purged (it can't be turned back into the video).
    sha256 = models.CharField(max_length=64, blank=True, db_index=True)
    order = models.PositiveIntegerField(default=0)
    enabled = models.BooleanField(default=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["type", "order", "pk"]
        constraints = [
            # The order is the visible code (GA01) and part of every output file name.
            models.UniqueConstraint(fields=["project", "type", "order"], name="unique_clip_order"),
        ]

    def __str__(self):
        return f"{self.get_type_display()} {self.order}: {self.original_name}"

    @property
    def content_key(self):
        """Identity of the footage: same bytes ⇒ same key, even across two Clip rows."""
        return self.sha256 or f"pk:{self.pk}"

    @property
    def is_ready(self):
        return self.status == self.Status.READY

    @property
    def code(self):
        """Short label used in variant names, e.g. GA02 / CO04 / CI01."""
        return f"{self.CODE_PREFIX[self.type]}{self.order:02d}"


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

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="variants")
    # Clip rows are kept after their files are purged, so variants stay traceable
    # (needed later to aggregate performance per hook/body/closer).
    # CASCADE: clips can only be deleted while the project is a draft (no variants yet), so
    # in practice this only fires when the whole project or its owner is deleted.
    hook = models.ForeignKey(Clip, on_delete=models.CASCADE, related_name="+")
    body = models.ForeignKey(Clip, on_delete=models.CASCADE, related_name="+")
    # Closers are optional: without enabled closers each variant is hook + body.
    closer = models.ForeignKey(Clip, on_delete=models.CASCADE, related_name="+", null=True, blank=True)
    # Suggested publishing order (1-based) and day (1-based) from variation.publishing_plan.
    position = models.PositiveIntegerField(default=0)
    publish_day = models.PositiveIntegerField(null=True, blank=True)
    output_file = models.FileField(upload_to=variant_output_to, max_length=500, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ["publish_day", "position", "pk"]
        constraints = [
            # closer may be NULL, and Postgres 14 treats NULLs as distinct, so hook+body-only
            # variants aren't covered; generate_variants runs once per project, which suffices.
            models.UniqueConstraint(fields=["project", "hook", "body", "closer"], name="unique_variant_combo"),
        ]

    def __str__(self):
        return self.label

    @property
    def clips(self):
        """Segments in playback order (hook, body and, if any, closer)."""
        return tuple(c for c in (self.hook, self.body, self.closer) if c is not None)

    @property
    def is_downloadable(self):
        return self.status == self.Status.DONE and bool(self.output_file) and self.expires_at > timezone.now()

    @property
    def label(self):
        return " + ".join(c.code for c in self.clips)

    @property
    def number(self):
        return f"{self.position:03d}"

    @property
    def output_name(self):
        # Leading position so a folder or ZIP listing is already in publishing order.
        return "_".join([self.number, self.project.slug, *(c.code for c in self.clips)]).lower() + ".mp4"
