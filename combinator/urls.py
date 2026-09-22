from django.urls import path

from . import views

app_name = "combinator"

urlpatterns = [
    path("variants/<int:pk>/download/", views.download_variant, name="download_variant"),
]
