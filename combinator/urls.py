from django.urls import path

from . import views

app_name = "combinator"

urlpatterns = [
    path("", views.project_list, name="project_list"),
    path("projects/new/", views.project_create, name="project_create"),
    path("projects/<int:pk>/", views.project_detail, name="project_detail"),
    path("projects/<int:pk>/status/", views.project_status, name="project_status"),
    path("projects/<int:pk>/upload/", views.clip_upload, name="clip_upload"),
    path("projects/<int:pk>/generate/", views.project_generate, name="project_generate"),
    path("projects/<int:pk>/download-all/", views.download_all, name="download_all"),
    path("clips/<int:pk>/toggle/", views.clip_toggle, name="clip_toggle"),
    path("clips/<int:pk>/delete/", views.clip_delete, name="clip_delete"),
    path("variants/<int:pk>/download/", views.download_variant, name="download_variant"),
]
