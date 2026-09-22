from django.urls import path

from . import views

app_name = "combinator"

# Public URLs only ever carry UUIDs; integer primary keys stay internal.
urlpatterns = [
    path("", views.project_list, name="project_list"),
    path("projects/new/", views.project_create, name="project_create"),
    path("projects/<uuid:uuid>/", views.project_detail, name="project_detail"),
    path("projects/<uuid:uuid>/status/", views.project_status, name="project_status"),
    path("projects/<uuid:uuid>/upload/", views.clip_upload, name="clip_upload"),
    path("projects/<uuid:uuid>/generate/", views.project_generate, name="project_generate"),
    path("projects/<uuid:uuid>/download-all/", views.download_all, name="download_all"),
    path("clips/<uuid:uuid>/toggle/", views.clip_toggle, name="clip_toggle"),
    path("clips/<uuid:uuid>/delete/", views.clip_delete, name="clip_delete"),
    path("variants/<uuid:uuid>/download/", views.download_variant, name="download_variant"),
]
