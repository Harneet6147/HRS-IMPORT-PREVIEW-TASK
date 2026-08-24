from django.urls import path

from . import views

urlpatterns = [
    path("", views.upload_preview, name="upload_preview"),
]
