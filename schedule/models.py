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


class LessonPractice(models.Model):
    event = models.OneToOneField(
        CalendarEvent,
        on_delete=models.CASCADE,
        related_name="practice",
    )
    title = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    fill_blanks_payload = models.TextField(blank=True, default="[]")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["event__start_at", "id"]

    def __str__(self):
        return self.title or f"Practice for {self.event.title}"


class PracticeWord(models.Model):
    practice = models.ForeignKey(
        LessonPractice,
        on_delete=models.CASCADE,
        related_name="words",
    )
    term = models.CharField(max_length=255)
    translation = models.CharField(max_length=255, blank=True)
    note = models.TextField(blank=True)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return self.term


class PracticeCard(models.Model):
    practice = models.ForeignKey(
        LessonPractice,
        on_delete=models.CASCADE,
        related_name="cards",
    )
    front_text = models.TextField()
    back_text = models.TextField()
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return self.front_text[:50]
