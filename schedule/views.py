import json
import os
from datetime import datetime
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseForbidden, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .models import CalendarEvent, LessonPractice, PracticeCard, PracticeWord


TEACHER_GROUP_NAMES = ("teacher", "teachers", "Teacher", "Teachers")
User = get_user_model()


FILL_BLANK_FALLBACK_TEMPLATES = [
    "Please write ____ in the blank.",
    "Today we are practicing the word ____.",
    "Can you complete this sentence with ____?",
    "The correct word here is ____.",
    "Use ____ to complete the sentence.",
]


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
    practice_related = ("practice__words", "practice__cards")
    if is_teacher(user):
        return CalendarEvent.objects.filter(owner=user).prefetch_related("students", *practice_related)
    return CalendarEvent.objects.filter(students=user).prefetch_related("students", *practice_related)


@login_required
def schedule(request):
    return render(
        request,
        "schedule/calendar2.html",
        {
            "can_edit_calendar": is_teacher(request.user),
            "practice_url_template": reverse("event_practice", args=[0]),
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


def _student_display_name(student):
    full_name = " ".join(
        part for part in [student.first_name.strip(), student.last_name.strip()] if part
    ).strip()
    return full_name or student.email or student.username


def _serialize_student(student):
    return {
        "id": student.id,
        "username": student.username,
        "email": student.email,
        "first_name": student.first_name,
        "last_name": student.last_name,
        "display_name": _student_display_name(student),
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
        "has_practice": hasattr(event, "practice"),
    }


def _find_student_matches(token):
    token = token.strip()
    if not token:
        return User.objects.none()

    query = Q(username__iexact=token) | Q(email__iexact=token)

    name_parts = [part for part in token.split() if part]
    if len(name_parts) == 1:
        query |= Q(first_name__iexact=token) | Q(last_name__iexact=token)
    else:
        first_name = name_parts[0]
        last_name = " ".join(name_parts[1:])
        reversed_first = name_parts[-1]
        reversed_last = " ".join(name_parts[:-1])
        query |= Q(first_name__iexact=first_name, last_name__iexact=last_name)
        query |= Q(first_name__iexact=reversed_first, last_name__iexact=reversed_last)

    return User.objects.filter(query).distinct().order_by("id")


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
        matches = list(_find_student_matches(token)[:2])
        if not matches:
            return None, None, JsonResponse(
                {
                    "error": (
                        f"Could not find a student for '{token}'. "
                        "Use username, email, or the student's first and last name."
                    )
                },
                status=400,
            )

        if len(matches) > 1:
            return None, None, JsonResponse(
                {
                    "error": (
                        f"Several students matched '{token}'. "
                        "Please use a more specific name, username, or email."
                    )
                },
                status=400,
            )

        user = matches[0]
        matched_users.append(user)
        normalized.append(_student_display_name(user))

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
        return None, JsonResponse({"error": "End time must be after start time."}, status=400)

    if start_at.date() != end_at.date():
        return None, JsonResponse({"error": "Events must start and end on the same day."}, status=400)

    color = payload.get("color") or "blue"
    valid_colors = {choice[0] for choice in CalendarEvent.COLOR_CHOICES}
    if color not in valid_colors:
        return None, JsonResponse({"error": "Invalid color."}, status=400)

    students, participants_text, students_error = _resolve_students(payload.get("participants") or "")
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


def _parse_practice_items(raw_value, field_names):
    try:
        payload = json.loads(raw_value or "[]")
    except json.JSONDecodeError:
        return None

    if not isinstance(payload, list):
        return None

    cleaned = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        cleaned_item = {field: str(item.get(field, "")).strip() for field in field_names}
        if any(cleaned_item.values()):
            cleaned.append(cleaned_item)
    return cleaned


def _extract_response_text(payload):
    if payload.get("output_text"):
        return payload["output_text"]

    for item in payload.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text" and content.get("text"):
                return content["text"]
    return ""


def _openai_json_response(system_prompt, user_payload):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None

    payload = {
        "model": os.environ.get("OPENAI_PRACTICE_MODEL", "gpt-5-mini"),
        "input": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
        "text": {"format": {"type": "json_object"}},
    }

    req = urlrequest.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with urlrequest.urlopen(req, timeout=25) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
        return None

    raw_text = _extract_response_text(response_payload)
    if not raw_text:
        return None

    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        return None


def _enrich_words_with_openai(words):
    pending = [
        {"index": index, "term": item.get("term", "")}
        for index, item in enumerate(words)
        if item.get("term") and (not item.get("translation") or not item.get("note"))
    ]
    if not pending:
        return words

    response_payload = _openai_json_response(
        (
            "You help a language teacher prepare lesson vocabulary. "
            "Return JSON only. For each word, provide a concise translation into Russian and a short meaning or usage note."
        ),
        {
            "task": "Fill missing translation and note fields for lesson words.",
            "words": pending,
            "response_format": {
                "words": [{"index": 0, "translation": "", "note": ""}]
            },
        },
    )
    if not response_payload:
        return words

    enriched = response_payload.get("words", [])
    by_index = {
        item.get("index"): item
        for item in enriched
        if isinstance(item, dict) and isinstance(item.get("index"), int)
    }

    for index, item in enumerate(words):
        suggestion = by_index.get(index)
        if not suggestion:
            continue
        if not item.get("translation"):
            item["translation"] = str(suggestion.get("translation", "")).strip()
        if not item.get("note"):
            item["note"] = str(suggestion.get("note", "")).strip()
    return words


def _fallback_fill_blanks(words):
    exercises = []
    for index, item in enumerate(words[:8]):
        term = (item.get("term") or "").strip()
        if not term:
            continue
        template = FILL_BLANK_FALLBACK_TEMPLATES[index % len(FILL_BLANK_FALLBACK_TEMPLATES)]
        exercises.append(
            {
                "sentence": template,
                "answer": term,
                "translation_hint": (item.get("translation") or "").strip(),
            }
        )
    return exercises


def _generate_fill_blanks(words):
    source_words = [
        {
            "term": item.get("term", "").strip(),
            "translation": item.get("translation", "").strip(),
            "note": item.get("note", "").strip(),
        }
        for item in words
        if (item.get("term") or "").strip()
    ]
    if not source_words:
        return []

    response_payload = _openai_json_response(
        (
            "You create fill-in-the-blank exercises for an English lesson. "
            "Return JSON only. For each word, write one short natural English sentence with exactly one blank written as ____ . "
            "Keep the sentence understandable for learners and make the missing word equal to the target word."
        ),
        {
            "task": "Create one fill-in-the-blank sentence for each vocabulary word.",
            "words": source_words,
            "response_format": {
                "exercises": [
                    {"sentence": "I ____ every morning.", "answer": "run", "translation_hint": "бегать"}
                ]
            },
        },
    )

    if not response_payload:
        return _fallback_fill_blanks(source_words)

    exercises = []
    for item in response_payload.get("exercises", []):
        if not isinstance(item, dict):
            continue
        sentence = str(item.get("sentence", "")).strip()
        answer = str(item.get("answer", "")).strip()
        translation_hint = str(item.get("translation_hint", "")).strip()
        if not sentence or "____" not in sentence or not answer:
            continue
        exercises.append(
            {
                "sentence": sentence,
                "answer": answer,
                "translation_hint": translation_hint,
            }
        )

    return exercises or _fallback_fill_blanks(source_words)


def _load_fill_blanks(raw_payload):
    try:
        items = json.loads(raw_payload or "[]")
    except json.JSONDecodeError:
        return []

    if not isinstance(items, list):
        return []

    cleaned = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        sentence = str(item.get("sentence", "")).strip()
        answer = str(item.get("answer", "")).strip()
        translation_hint = str(item.get("translation_hint", "")).strip()
        if not sentence or not answer:
            continue
        cleaned.append(
            {
                "id": index,
                "sentence": sentence,
                "answer": answer,
                "translation_hint": translation_hint,
            }
        )
    return cleaned


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


@login_required
def event_practice(request, event_id):
    event = get_object_or_404(_user_event_queryset(request.user), id=event_id)
    can_edit = is_teacher(request.user) and event.owner_id == request.user.id
    practice, _ = LessonPractice.objects.get_or_create(event=event, defaults={"title": event.title})

    if request.method == "POST":
        if not can_edit:
            return HttpResponseForbidden("Only the lesson owner can edit practice.")

        words = _parse_practice_items(request.POST.get("words_json"), ["term", "translation", "note"])
        cards = _parse_practice_items(request.POST.get("cards_json"), ["front_text", "back_text"])
        if words is None or cards is None:
            return HttpResponseForbidden("Invalid practice payload.")

        words = _enrich_words_with_openai(words)
        fill_blanks_items = _generate_fill_blanks(words)
        practice.title = (request.POST.get("practice_title") or event.title).strip()[:255]
        practice.description = (request.POST.get("practice_description") or "").strip()
        practice.fill_blanks_payload = json.dumps(fill_blanks_items, ensure_ascii=False)

        with transaction.atomic():
            practice.save()
            practice.words.all().delete()
            practice.cards.all().delete()
            PracticeWord.objects.bulk_create(
                [
                    PracticeWord(
                        practice=practice,
                        position=index,
                        term=item["term"],
                        translation=item["translation"],
                        note=item["note"],
                    )
                    for index, item in enumerate(words)
                ]
            )
            PracticeCard.objects.bulk_create(
                [
                    PracticeCard(
                        practice=practice,
                        position=index,
                        front_text=item["front_text"],
                        back_text=item["back_text"],
                    )
                    for index, item in enumerate(cards)
                ]
            )

        return redirect(f"{reverse('event_practice', args=[event.id])}?saved=1")

    words = list(practice.words.all())
    fill_blanks_items = _load_fill_blanks(practice.fill_blanks_payload)
    if fill_blanks_items:
        random.shuffle(fill_blanks_items)
    fill_blanks_word_bank = list(dict.fromkeys(item["answer"] for item in fill_blanks_items if item.get("answer")))
    if not fill_blanks_word_bank:
        fill_blanks_word_bank = [word.term for word in words if word.term]
    if fill_blanks_word_bank:
        random.shuffle(fill_blanks_word_bank)

    return render(
        request,
        "schedule/practice.html",
        {
            "event": event,
            "practice": practice,
            "words": words,
            "cards": list(practice.cards.all()),
            "fill_blanks_items": fill_blanks_items,
            "fill_blanks_word_bank": fill_blanks_word_bank,
            "can_edit_practice": can_edit,
            "saved": request.GET.get("saved") == "1",
            "ai_enrichment_enabled": bool(os.environ.get("OPENAI_API_KEY")),
        },
    )


