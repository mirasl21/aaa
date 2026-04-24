from django.conf import settings
from django.db import models


class Lesson(models.Model):
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    date = models.DateField()
    topic_for_student = models.TextField()
    topic_for_teacher = models.TextField()

    def __str__(self):
        return f"{self.student} - {self.date}"


class CalendarEvent(models.Model):
    COLOR_CHOICES = [
        ("blue", "Blue"),
        ("red", "Red"),
        ("teal", "Teal"),
        ("purple", "Purple"),
        ("green", "Green"),
    ]

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="calendar_events",
    )
    students = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="available_calendar_events",
        blank=True,
    )
    title = models.CharField(max_length=255, default="Untitled")
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    color = models.CharField(max_length=20, choices=COLOR_CHOICES, default="blue")
    participants = models.CharField(max_length=255, blank=True)
    conferencing_url = models.URLField(blank=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["start_at", "id"]

    def __str__(self):
        return f"{self.title} ({self.start_at:%Y-%m-%d %H:%M})"
