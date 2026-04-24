import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import CalendarEvent


User = get_user_model()


class CalendarPermissionsTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="teacher",
            password="secret123",
            email="teacher@example.com",
            is_staff=True,
        )
        self.student = User.objects.create_user(
            username="student",
            password="secret123",
            email="student@example.com",
        )
        self.other_student = User.objects.create_user(
            username="other_student",
            password="secret123",
            email="other@example.com",
        )

    def _payload(self, participants="student"):
        start_at = timezone.now().replace(second=0, microsecond=0)
        return json.dumps(
            {
                "title": "Consultation",
                "start": start_at.isoformat(),
                "end": (start_at + timedelta(hours=1)).isoformat(),
                "color": "teal",
                "participants": participants,
                "conferencing_url": "https://example.com/meet",
                "description": "Weekly planning",
            }
        )

    def test_schedule_requires_login(self):
        response = self.client.get(reverse("schedule"))
        self.assertEqual(response.status_code, 302)

    def test_teacher_can_create_event_for_student(self):
        self.client.login(username="teacher", password="secret123")

        response = self.client.post(
            reverse("events_api"),
            data=self._payload(),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        event = CalendarEvent.objects.get(owner=self.teacher)
        self.assertEqual(event.students.get().username, "student")

    def test_student_cannot_create_event(self):
        self.client.login(username="student", password="secret123")

        response = self.client.post(
            reverse("events_api"),
            data=self._payload(),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)

    def test_student_sees_only_assigned_events(self):
        start_at = timezone.now().replace(second=0, microsecond=0)
        visible_event = CalendarEvent.objects.create(
            owner=self.teacher,
            title="Visible",
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
        )
        visible_event.students.add(self.student)

        hidden_event = CalendarEvent.objects.create(
            owner=self.teacher,
            title="Hidden",
            start_at=start_at + timedelta(days=1),
            end_at=start_at + timedelta(days=1, hours=1),
        )
        hidden_event.students.add(self.other_student)

        self.client.login(username="student", password="secret123")
        response = self.client.get(reverse("events_api"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual([item["title"] for item in payload["events"]], ["Visible"])

    def test_teacher_sees_only_own_events(self):
        other_teacher = User.objects.create_user(
            username="other_teacher",
            password="secret123",
            email="other_teacher@example.com",
            is_staff=True,
        )
        start_at = timezone.now().replace(second=0, microsecond=0)
        CalendarEvent.objects.create(
            owner=self.teacher,
            title="Mine",
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
        )
        CalendarEvent.objects.create(
            owner=other_teacher,
            title="Not mine",
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
        )

        self.client.login(username="teacher", password="secret123")
        response = self.client.get(reverse("events_api"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual([item["title"] for item in payload["events"]], ["Mine"])

    def test_teacher_can_delete_own_event(self):
        start_at = timezone.now().replace(second=0, microsecond=0)
        event = CalendarEvent.objects.create(
            owner=self.teacher,
            title="Delete me",
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
        )
        event.students.add(self.student)

        self.client.login(username="teacher", password="secret123")
        response = self.client.delete(reverse("event_detail_api", args=[event.id]))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(CalendarEvent.objects.filter(id=event.id).exists())

    def test_student_cannot_delete_assigned_event(self):
        start_at = timezone.now().replace(second=0, microsecond=0)
        event = CalendarEvent.objects.create(
            owner=self.teacher,
            title="Protected",
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
        )
        event.students.add(self.student)

        self.client.login(username="student", password="secret123")
        response = self.client.delete(reverse("event_detail_api", args=[event.id]))

        self.assertEqual(response.status_code, 403)
        self.assertTrue(CalendarEvent.objects.filter(id=event.id).exists())
