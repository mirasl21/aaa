from django.urls import path
from django.views.generic import RedirectView

from . import views

urlpatterns = [
    path('', RedirectView.as_view(pattern_name='schedule', permanent=False), name='home'),
    path('about/', views.about, name='about'),
    path('schedule/', views.schedule, name='schedule'),
    path('schedule/api/events/', views.events_api, name='events_api'),
    path('schedule/api/events/<int:event_id>/', views.event_detail_api, name='event_detail_api'),
]
