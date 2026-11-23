from django.urls import path

from . import views

app_name = "inventory"

urlpatterns = [
    path("", views.home, name="home"),
    path("health/", views.health, name="health"),
    path("freezers/", views.freezer_list, name="freezer_list"),
    path("freezers/new/", views.freezer_create, name="freezer_create"),
    path("freezers/<int:pk>/", views.freezer_detail, name="freezer_detail"),
    path("freezers/<int:pk>/edit/", views.freezer_edit, name="freezer_edit"),
    path("freezers/<int:pk>/delete/", views.freezer_delete, name="freezer_delete"),
]
