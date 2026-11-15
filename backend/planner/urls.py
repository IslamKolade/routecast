from django.urls import path

from . import views

urlpatterns = [
    path("health", views.health, name="health"),
    path("geocode", views.geocode, name="geocode"),
    path("plan", views.plan, name="plan"),
    path("plan/<str:plan_id>/weather", views.plan_weather, name="plan-weather"),
]
