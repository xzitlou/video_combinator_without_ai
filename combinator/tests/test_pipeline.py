import shutil
import subprocess
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock, skipUnless

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from combinator import ffmpeg, services
from combinator.models import Clip, Project, Variant

HAS_FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")


def run_inline(func, *args):
    func(*args)


def make_clip(path, size, fps, seconds, audio=True):
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc=size={size}:rate={fps}:duration={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100:duration={seconds}"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", str(path)]
    subprocess.run(cmd, check=True)
    return path.read_bytes()


class MediaTestCase(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        patcher = mock.patch.object(services, "enqueue", run_inline)
        patcher.start()
        self.addCleanup(patcher.stop)

    def stored_files(self):
        return sorted(str(p.relative_to(self.media)) for p in Path(self.media).rglob("*") if p.is_file())


class GenerationRulesTests(MediaTestCase):
    def ready_clip(self, project, clip_type, order, **kwargs):
        return Clip.objects.create(
            project=project, type=clip_type, order=order, original_name=f"{clip_type}{order}.mp4",
            status=Clip.Status.READY, **kwargs,
        )

    def test_only_enabled_clips_are_combined(self):
        project = Project.objects.create(name="Campaña")
        for i in (1, 2):
            self.ready_clip(project, Clip.Type.HOOK, i)
        self.ready_clip(project, Clip.Type.HOOK, 3, enabled=False)
        for i in (1, 2, 3):
            self.ready_clip(project, Clip.Type.BODY, i)
        self.ready_clip(project, Clip.Type.CLOSER, 1)

        self.assertEqual(services.variant_count(project), 6)
        with mock.patch("combinator.tasks.render_variant"):
            variants = services.generate_variants(project.pk)
        self.assertEqual(len(variants), 6)
        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.PROCESSING)

    @override_settings(MAX_VARIANTS_PER_RUN=4)
    def test_limit_is_enforced(self):
        project = Project.objects.create(name="x")
        for t in Clip.Type.values:
            for i in (1, 2):
                self.ready_clip(project, t, i)
        with self.assertRaises(services.GenerationError):
            services.generate_variants(project.pk)
        self.assertFalse(Variant.objects.exists())

    def test_requires_one_of_each_type(self):
        project = Project.objects.create(name="x")
        self.ready_clip(project, Clip.Type.HOOK, 1)
        with self.assertRaises(services.GenerationError):
            services.generate_variants(project.pk)

    def test_expired_outputs_are_deleted(self):
        project = Project.objects.create(name="x", status=Project.Status.DONE)
        clips = [self.ready_clip(project, t, 1) for t in Clip.Type.values]
        variant = Variant.objects.create(project=project, hook=clips[0], body=clips[1], closer=clips[2])
        variant.output_file.save("out.mp4", SimpleUploadedFile("out.mp4", b"data"), save=False)
        services.mark_variant_done(variant)

        url = reverse("combinator:download_variant", args=[variant.pk])
        self.assertEqual(self.client.get(url).status_code, 200)

        later = timezone.now() + timedelta(hours=1, seconds=1)
        with mock.patch("django.utils.timezone.now", return_value=later):
            self.assertEqual(self.client.get(url).status_code, 404)
            self.assertEqual(services.purge_expired(), (1, 0))
        variant.refresh_from_db()
        self.assertEqual(variant.status, Variant.Status.EXPIRED)
        self.assertEqual(self.stored_files(), [])

    def test_abandoned_draft_uploads_are_deleted(self):
        project = Project.objects.create(name="x")
        clip = self.ready_clip(project, Clip.Type.HOOK, 1)
        clip.normalized_file.save("h.mp4", SimpleUploadedFile("h.mp4", b"data"))
        Project.objects.filter(pk=project.pk).update(created_at=timezone.now() - timedelta(days=2))

        self.assertEqual(services.purge_expired(), (0, 1))
        self.assertEqual(self.stored_files(), [])
        with self.assertRaises(services.GenerationError):
            services.generate_variants(project.pk)


@skipUnless(HAS_FFMPEG, "ffmpeg not installed")
class FullPipelineTests(MediaTestCase):
    def test_upload_generate_and_purge_uploads(self):
        tmp = Path(self.media) / "_src"
        tmp.mkdir()
        project = Project.objects.create(name="Campaña Septiembre")
        sources = [
            (Clip.Type.HOOK, make_clip(tmp / "h.mp4", "1920x1080", 25, 1)),          # landscape
            (Clip.Type.HOOK, make_clip(tmp / "h2.mp4", "720x1280", 60, 1, audio=False)),  # no audio
            (Clip.Type.BODY, make_clip(tmp / "b.mp4", "1080x1920", 30, 2)),
            (Clip.Type.CLOSER, make_clip(tmp / "c.mp4", "1080x1080", 24, 1)),
        ]
        shutil.rmtree(tmp)

        with self.captureOnCommitCallbacks(execute=True):
            for clip_type, data in sources:
                services.add_clip(project, clip_type, SimpleUploadedFile("clip.mov", data))
        self.assertTrue(all(c.status == Clip.Status.READY for c in project.clips.all()))
        # Originals are dropped as soon as the normalized copy exists.
        self.assertFalse(any(c.file for c in project.clips.all()))

        with self.captureOnCommitCallbacks(execute=True):
            services.generate_variants(project.pk)

        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.DONE)
        self.assertIsNotNone(project.uploads_purged_at)
        variants = list(project.variants.all())
        self.assertEqual(len(variants), 2)
        for v in variants:
            self.assertEqual(v.status, Variant.Status.DONE, v.error)
            self.assertAlmostEqual((v.expires_at - v.completed_at).total_seconds(), 3600)
            duration, has_audio = ffmpeg.probe(v.output_file.path)
            self.assertAlmostEqual(duration, 4, delta=0.2)
            self.assertTrue(has_audio)

        # Only the rendered outputs remain on disk.
        self.assertEqual(
            self.stored_files(),
            sorted(f"projects/{project.pk}/outputs/{v.output_name}" for v in variants),
        )
        self.assertEqual(variants[0].output_name, "campana-septiembre_h01_b01_c01.mp4")
