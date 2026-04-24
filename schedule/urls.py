from django.urls import path

from . import views

urlpatterns = [
    path('', views.index, name='home'),
    path('about/', views.about, name='about'),
    path('schedule/', views.schedule, name='schedule'),
    path('schedule/api/events/', views.events_api, name='events_api'),
    path('schedule/api/events/<int:event_id>/', views.event_detail_api, name='event_detail_api'),
]
