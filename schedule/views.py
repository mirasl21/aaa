import json
from datetime import datetime

from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.http import HttpResponseForbidden, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from .models import CalendarEvent


TEACHER_GROUP_NAMES = ("teacher", "teachers", "Teacher", "Teachers")
User = get_user_model()


def index(request):
    return render(request, "schedule/index.html")


def about(request):
    return render(request, "schedule/about.html")


def is_teacher(user):
    if not user.is_authenticated:
        return False
    return (
        user.is_staff
        or user.is_superuser
        or user.groups.filter(name__in=TEACHER_GROUP_NAMES).exists()
    )


def _user_event_queryset(user):
    if is_teacher(user):
        return CalendarEvent.objects.filter(owner=user).prefetch_related("students")
    return CalendarEvent.objects.filter(students=user).prefetch_related("students")


@login_required
def schedule(request):
    return render(
        request,
        "schedule/calendar2.html",
        {
            "can_edit_calendar": is_teacher(request.user),
        },
    )


def _parse_payload(request):
    try:
        return json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return None


def _parse_datetime(value):
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None

    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _serialize_student(student):
    return {
        "id": student.id,
        "username": student.username,
        "email": student.email,
    }


def _event_to_dict(event):
    students = list(event.students.all())
    return {
        "id": event.id,
        "title": event.title,
        "start": event.start_at.isoformat(),
        "end": event.end_at.isoformat(),
        "color": event.color,
        "participants": event.participants,
        "conferencing_url": event.conferencing_url,
        "description": event.description,
        "students": [_serialize_student(student) for student in students],
    }


def _resolve_students(raw_participants):
    tokens = [
        token.strip()
        for token in raw_participants.replace(";", ",").replace("\n", ",").split(",")
        if token.strip()
    ]

    if not tokens:
        return [], "", None

    matched_users = []
    normalized = []

    for token in tokens:
        user = User.objects.filter(
            Q(username__iexact=token) | Q(email__iexact=token)
        ).first()
        if not user:
            return None, None, JsonResponse(
                {
                    "error": (
                        "Не найден пользователь для участника "
                        f"'{token}'. Укажите username или email существующего ученика."
                    )
                },
                status=400,
            )
        matched_users.append(user)
        normalized.append(user.email or user.username)

    unique_users = []
    seen_ids = set()
    for user in matched_users:
        if user.id not in seen_ids:
            unique_users.append(user)
            seen_ids.add(user.id)

    return unique_users, ", ".join(dict.fromkeys(normalized)), None


def _validate_event_payload(payload):
    required_fields = ("start", "end")
    if payload is None:
        return None, JsonResponse({"error": "Invalid JSON payload."}, status=400)

    missing = [field for field in required_fields if field not in payload]
    if missing:
        return None, JsonResponse(
            {"error": f"Missing required fields: {', '.join(missing)}."},
            status=400,
        )

    start_at = _parse_datetime(payload.get("start"))
    end_at = _parse_datetime(payload.get("end"))
    if not start_at or not end_at:
        return None, JsonResponse({"error": "Invalid datetime format."}, status=400)

    if end_at <= start_at:
        return None, JsonResponse(
            {"error": "End time must be after start time."},
            status=400,
        )

    if start_at.date() != end_at.date():
        return None, JsonResponse(
            {"error": "Events must start and end on the same day."},
            status=400,
        )

    color = payload.get("color") or "blue"
    valid_colors = {choice[0] for choice in CalendarEvent.COLOR_CHOICES}
    if color not in valid_colors:
        return None, JsonResponse({"error": "Invalid color."}, status=400)

    students, participants_text, students_error = _resolve_students(
        payload.get("participants") or ""
    )
    if students_error:
        return None, students_error

    data = {
        "title": (payload.get("title") or "Untitled").strip()[:255] or "Untitled",
        "start_at": start_at,
        "end_at": end_at,
        "color": color,
        "participants": participants_text,
        "conferencing_url": (payload.get("conferencing_url") or "").strip(),
        "description": (payload.get("description") or "").strip(),
        "students": students or [],
    }
    return data, None


def _teacher_required(request):
    if is_teacher(request.user):
        return None
    return HttpResponseForbidden("Only teachers can modify the calendar.")


@login_required
def events_api(request):
    if request.method == "GET":
        events = _user_event_queryset(request.user)
        return JsonResponse({"events": [_event_to_dict(event) for event in events]})

    if request.method == "POST":
        teacher_error = _teacher_required(request)
        if teacher_error:
            return teacher_error

        payload = _parse_payload(request)
        data, error_response = _validate_event_payload(payload)
        if error_response:
            return error_response

        students = data.pop("students")
        event = CalendarEvent.objects.create(owner=request.user, **data)
        event.students.set(students)
        event.refresh_from_db()
        return JsonResponse(_event_to_dict(event), status=201)

    return HttpResponseNotAllowed(["GET", "POST"])


@login_required
def event_detail_api(request, event_id):
    event = get_object_or_404(_user_event_queryset(request.user), id=event_id)

    if request.method == "PATCH":
        teacher_error = _teacher_required(request)
        if teacher_error:
            return teacher_error

        if event.owner_id != request.user.id:
            return HttpResponseForbidden("Teachers can edit only their own lessons.")

        payload = _parse_payload(request)
        data, error_response = _validate_event_payload(payload)
        if error_response:
            return error_response

        students = data.pop("students")
        for field, value in data.items():
            setattr(event, field, value)
        event.save(update_fields=[*data.keys(), "updated_at"])
        event.students.set(students)
        event.refresh_from_db()
        return JsonResponse(_event_to_dict(event))

    if request.method in ("DELETE", "POST"):
        teacher_error = _teacher_required(request)
        if teacher_error:
            return teacher_error

        if event.owner_id != request.user.id:
            return HttpResponseForbidden("Teachers can delete only their own lessons.")

        event.delete()
        return JsonResponse({"deleted": True})

    return HttpResponseNotAllowed(["PATCH", "DELETE", "POST"])
