from django.contrib import admin

from .models import CalendarEvent, Lesson, LessonPractice, PracticeCard, PracticeWord


class PracticeWordInline(admin.TabularInline):
    model = PracticeWord
    extra = 0


class PracticeCardInline(admin.TabularInline):
    model = PracticeCard
    extra = 0


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


@admin.register(LessonPractice)
class LessonPracticeAdmin(admin.ModelAdmin):
    list_display = ("title", "event", "updated_at")
    search_fields = ("title", "event__title", "event__owner__username")
    inlines = (PracticeWordInline, PracticeCardInline)
