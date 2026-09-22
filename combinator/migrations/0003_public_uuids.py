import uuid

from django.db import migrations, models

MODELS = ("project", "clip", "variant")


def fill_uuids(apps, schema_editor):
    for name in MODELS:
        Model = apps.get_model("combinator", name)
        for obj in Model.objects.filter(uuid__isnull=True).only("pk"):
            obj.uuid = uuid.uuid4()
            obj.save(update_fields=["uuid"])


class Migration(migrations.Migration):
    """Add public UUIDs in three steps so existing rows each get a distinct value."""

    dependencies = [
        ("combinator", "0002_unique_clip_order"),
    ]

    operations = [
        *(migrations.AddField(name, "uuid", models.UUIDField(null=True, editable=False)) for name in MODELS),
        migrations.RunPython(fill_uuids, migrations.RunPython.noop),
        *(
            migrations.AlterField(name, "uuid", models.UUIDField(default=uuid.uuid4, unique=True, editable=False))
            for name in MODELS
        ),
    ]
