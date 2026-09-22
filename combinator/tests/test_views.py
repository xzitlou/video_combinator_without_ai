from unittest import mock

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from combinator import services
from combinator.models import Clip, Project


class ViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("lou", password="secret-pass-123")
        self.other = User.objects.create_user("ana", password="secret-pass-123")
        self.project = Project.objects.create(name="Mía", owner=self.user)
        self.client.force_login(self.user)

    def test_anonymous_is_redirected_to_login(self):
        self.client.logout()
        response = self.client.get(reverse("combinator:project_detail", args=[self.project.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_other_users_projects_are_hidden(self):
        theirs = Project.objects.create(name="Ajena", owner=self.other)
        self.assertEqual(self.client.get(reverse("combinator:project_detail", args=[theirs.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("combinator:project_list")), "Ajena")

    def test_create_and_open_project(self):
        response = self.client.post(reverse("combinator:project_create"), {"name": "Otoño"})
        project = Project.objects.get(name="Otoño")
        self.assertRedirects(response, reverse("combinator:project_detail", args=[project.pk]))
        self.assertContains(self.client.get(response.url), "Generar videos")

    def test_upload_rejects_unknown_formats(self):
        response = self.client.post(
            reverse("combinator:clip_upload", args=[self.project.pk]),
            {"type": "hook", "file": SimpleUploadedFile("notes.txt", b"hi")},
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Clip.objects.exists())

    def test_upload_creates_clip_and_queues_it(self):
        with mock.patch.object(services, "enqueue") as enqueue, self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("combinator:clip_upload", args=[self.project.pk]),
                {"type": "body", "file": SimpleUploadedFile("take 3.MOV", b"\x00" * 10)},
            )
        self.assertEqual(response.status_code, 201, response.content)
        clip = Clip.objects.get()
        self.assertEqual(response.json()["code"], "B01")
        self.assertEqual(clip.original_name, "take 3.MOV")
        enqueue.assert_called_once()

    def test_toggle_and_delete_only_while_draft(self):
        clip = Clip.objects.create(project=self.project, type="hook", order=1, original_name="h.mp4", status="ready")
        self.assertEqual(self.client.post(reverse("combinator:clip_toggle", args=[clip.pk]), {"enabled": "0"}).status_code, 200)
        clip.refresh_from_db()
        self.assertFalse(clip.enabled)

        Project.objects.filter(pk=self.project.pk).update(status=Project.Status.DONE)
        self.assertEqual(self.client.post(reverse("combinator:clip_delete", args=[clip.pk])).status_code, 409)
        self.assertTrue(Clip.objects.filter(pk=clip.pk).exists())

    def test_status_endpoint(self):
        Clip.objects.create(project=self.project, type="hook", order=1, original_name="h.mp4")
        data = self.client.get(reverse("combinator:project_status", args=[self.project.pk])).json()
        self.assertTrue(data["busy"])
        self.assertEqual(data["clips"][0]["status"], "pending")

    def test_download_all_streams_a_zip_of_ready_variants(self):
        import io
        import shutil
        import tempfile
        import zipfile

        from django.test import override_settings

        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        with override_settings(MEDIA_ROOT=media):
            clips = [
                Clip.objects.create(project=self.project, type=t, order=1, original_name=f"{t}.mp4", status="ready")
                for t in ("hook", "body", "closer")
            ]
            from combinator.models import Variant

            variant = Variant.objects.create(project=self.project, hook=clips[0], body=clips[1], closer=clips[2])
            variant.output_file.save("v.mp4", SimpleUploadedFile("v.mp4", b"video-bytes"), save=False)
            services.mark_variant_done(variant)

            response = self.client.get(reverse("combinator:download_all", args=[self.project.pk]))
            self.assertEqual(response.status_code, 200)
            archive = zipfile.ZipFile(io.BytesIO(b"".join(response.streaming_content)))
            self.assertEqual(archive.namelist(), [variant.output_name])
            self.assertEqual(archive.read(variant.output_name), b"video-bytes")
