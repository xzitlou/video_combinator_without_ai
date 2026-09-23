from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from combinator import services
from combinator.models import Clip, Project


class ViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("lou@example.com", password="secret-pass-123")
        self.other = get_user_model().objects.create_user("ana@example.com", password="secret-pass-123")
        self.project = Project.objects.create(name="Mía", owner=self.user)
        self.client.force_login(self.user)

    def test_anonymous_is_redirected_to_login(self):
        self.client.logout()
        response = self.client.get(reverse("combinator:project_detail", args=[self.project.uuid]))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_login_with_email_is_case_insensitive(self):
        self.client.logout()
        response = self.client.post(reverse("login"), {"username": "LOU@Example.com", "password": "secret-pass-123"})
        self.assertRedirects(response, reverse("combinator:project_list"))

    def test_signup_with_email(self):
        self.client.logout()
        response = self.client.post(
            reverse("signup"),
            {"email": "Nueva@Example.com", "password1": "otra-clave-larga-9", "password2": "otra-clave-larga-9"},
        )
        self.assertRedirects(response, reverse("combinator:project_list"))
        self.assertTrue(get_user_model().objects.filter(email="nueva@example.com").exists())

        self.client.logout()
        dup = self.client.post(
            reverse("signup"),
            {"email": "NUEVA@example.com", "password1": "otra-clave-larga-9", "password2": "otra-clave-larga-9"},
        )
        self.assertContains(dup, "Ya existe una cuenta con este correo.")

    def test_other_users_projects_are_hidden(self):
        theirs = Project.objects.create(name="Ajena", owner=self.other)
        self.assertEqual(self.client.get(reverse("combinator:project_detail", args=[theirs.uuid])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("combinator:project_list")), "Ajena")

    def test_create_and_open_project(self):
        response = self.client.post(reverse("combinator:project_create"), {"name": "Otoño"})
        project = Project.objects.get(name="Otoño")
        self.assertRedirects(response, reverse("combinator:project_detail", args=[project.uuid]))
        page = self.client.get(response.url)
        self.assertContains(page, "Ganchos")
        self.assertContains(page, "Los primeros 3 a 5 segundos")

    def test_upload_rejects_unknown_formats(self):
        response = self.client.post(
            reverse("combinator:clip_upload", args=[self.project.uuid]),
            {"type": "hook", "file": SimpleUploadedFile("notes.txt", b"hi")},
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Clip.objects.exists())

    def test_upload_creates_clip_and_queues_it(self):
        with mock.patch.object(services, "enqueue") as enqueue, self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("combinator:clip_upload", args=[self.project.uuid]),
                {"type": "body", "file": SimpleUploadedFile("take 3.MOV", b"\x00" * 10)},
            )
        self.assertEqual(response.status_code, 201, response.content)
        clip = Clip.objects.get()
        self.assertEqual(response.json()["code"], "CO01")
        self.assertEqual(clip.original_name, "take 3.MOV")
        enqueue.assert_called_once()

    def test_toggle_and_delete_only_while_draft(self):
        clip = Clip.objects.create(project=self.project, type="hook", order=1, original_name="h.mp4", status="ready")
        self.assertEqual(self.client.post(reverse("combinator:clip_toggle", args=[clip.uuid]), {"enabled": "0"}).status_code, 200)
        clip.refresh_from_db()
        self.assertFalse(clip.enabled)

        Project.objects.filter(pk=self.project.pk).update(status=Project.Status.DONE)
        self.assertEqual(self.client.post(reverse("combinator:clip_delete", args=[clip.uuid])).status_code, 409)
        self.assertTrue(Clip.objects.filter(pk=clip.pk).exists())

    def test_generated_project_and_owner_can_be_deleted(self):
        from combinator.models import Variant

        clips = [
            Clip.objects.create(project=self.project, type=t, order=1, original_name=f"{t}.mp4", status="ready")
            for t in ("hook", "body")
        ]
        Variant.objects.create(project=self.project, hook=clips[0], body=clips[1])
        self.user.delete()
        self.assertFalse(Project.objects.exists())
        self.assertFalse(Variant.objects.exists())

    def test_urls_use_uuids_not_ids(self):
        page = self.client.get(reverse("combinator:project_list")).content.decode()
        self.assertIn(str(self.project.uuid), page)
        self.assertNotIn(f"/projects/{self.project.pk}/", page)
        self.assertEqual(self.client.get(f"/projects/{self.project.pk}/").status_code, 404)

    def test_generate_returns_json_for_the_upload_flow(self):
        for t in ("hook", "body"):
            Clip.objects.create(project=self.project, type=t, order=1, original_name=f"{t}.mp4")
        with mock.patch.object(services, "enqueue"):
            response = self.client.post(
                reverse("combinator:project_generate", args=[self.project.uuid]), HTTP_ACCEPT="application/json"
            )
        self.assertEqual(response.json(), {"redirect": reverse("combinator:project_detail", args=[self.project.uuid])})

        again = self.client.post(
            reverse("combinator:project_generate", args=[self.project.uuid]), HTTP_ACCEPT="application/json"
        )
        self.assertEqual(again.status_code, 409)
        self.assertIn("error", again.json())

    def test_status_endpoint(self):
        Clip.objects.create(project=self.project, type="hook", order=1, original_name="h.mp4")
        data = self.client.get(reverse("combinator:project_status", args=[self.project.uuid])).json()
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

            variant.publish_day = 2
            variant.save(update_fields=["publish_day"])
            url = reverse("combinator:download_all", args=[self.project.uuid])
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            archive = zipfile.ZipFile(io.BytesIO(b"".join(response.streaming_content)))
            self.assertEqual(archive.namelist(), [f"dia-02/{variant.output_name}"])
            self.assertEqual(archive.read(f"dia-02/{variant.output_name}"), b"video-bytes")

            self.assertEqual(self.client.get(url + "?day=2").status_code, 200)
            self.assertEqual(self.client.get(url + "?day=1").status_code, 404)
            self.assertEqual(self.client.get(url + "?day=x").status_code, 404)


class ParallelUploadTests(TransactionTestCase):
    """Real transactions and threads: the browser uploads several files at once."""

    def test_parallel_uploads_get_distinct_numbers(self):
        import shutil
        import tempfile
        import threading

        from django.db import connection
        from django.test import override_settings

        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        user = get_user_model().objects.create_user("lou@example.com", password="x")
        project = Project.objects.create(name="Lote", owner=user)
        barrier = threading.Barrier(6)
        errors = []

        def upload(i):
            try:
                barrier.wait()
                services.add_clip(project, "hook", SimpleUploadedFile(f"g{i}.mp4", f"take {i}".encode()))
            except Exception as exc:  # surfaced below
                errors.append(exc)
            finally:
                connection.close()

        with override_settings(MEDIA_ROOT=media), mock.patch.object(services, "enqueue"):
            threads = [threading.Thread(target=upload, args=(i,)) for i in range(6)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        self.assertEqual(errors, [])
        codes = sorted(c.code for c in project.clips.all())
        self.assertEqual(codes, ["GA01", "GA02", "GA03", "GA04", "GA05", "GA06"])


class DuplicateFootageTests(TestCase):
    def setUp(self):
        import shutil
        import tempfile

        from django.test import override_settings

        self.user = get_user_model().objects.create_user("lou@example.com", password="x")
        self.project = Project.objects.create(name="Lote", owner=self.user)
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=media)
        override.enable()
        self.addCleanup(override.disable)
        patcher = mock.patch.object(services, "enqueue")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_same_file_twice_is_rejected(self):
        services.add_clip(self.project, "body", SimpleUploadedFile("cuerpo1.mp4", b"same bytes"))
        with self.assertRaisesMessage(services.GenerationError, "cuerpo4.mp4 es el mismo video que CO01"):
            services.add_clip(self.project, "body", SimpleUploadedFile("cuerpo4.mp4", b"same bytes"))
        # Also across columns: a hook that is a body's footage.
        with self.assertRaises(services.GenerationError):
            services.add_clip(self.project, "hook", SimpleUploadedFile("otro.mp4", b"same bytes"))
        self.assertEqual(self.project.clips.count(), 1)

    def test_identical_variants_are_flagged(self):
        from combinator.models import Variant
        from combinator.views import _annotate_similarity

        hook = Clip.objects.create(project=self.project, type="hook", order=1, original_name="g", sha256="g")
        body1 = Clip.objects.create(project=self.project, type="body", order=1, original_name="b1", sha256="same")
        body4 = Clip.objects.create(project=self.project, type="body", order=2, original_name="b4", sha256="same")
        a = Variant.objects.create(project=self.project, hook=hook, body=body1, position=1)
        b = Variant.objects.create(project=self.project, hook=hook, body=body4, position=2)
        annotated = _annotate_similarity([a, b])
        self.assertEqual(annotated[0].similar_level, "identical")
        self.assertEqual(annotated[0].similar_label, "Idéntico a #002")
