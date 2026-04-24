from django.contrib import admin

from .models import CalendarEvent, Lesson


@admin.register(Lesson)
class LessonAdmin(admin.ModelAdmin):
    list_display = ("student", "date")
    search_fields = ("student__username", "student__email")


@admin.register(CalendarEvent)
class CalendarEventAdmin(admin.ModelAdmin):
    list_display = ("title", "owner", "start_at", "end_at", "color")
    list_filter = ("color", "start_at", "owner")
    search_fields = ("title", "participants", "owner__username", "owner__email")
    filter_horizontal = ("students",)
