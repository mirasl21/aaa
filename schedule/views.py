import json
import logging
import os
import random
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
logger = logging.getLogger(__name__)

FILL_BLANK_FALLBACK_TEMPLATES = [
    "By the time the meeting began, everyone had already read the ____ twice.",
    "She tried to sound confident, but her voice made the ____ obvious.",
    "The article raised an unexpected ____ that nobody in class had mentioned before.",
    "Even after a long discussion, they still could not agree on the best ____.",
    "His final answer was clear, but the ____ behind it was even more interesting.",
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
        logger.warning("Practice AI skipped: OPENAI_API_KEY is not set.")
        return None

    model_name = (os.environ.get("OPENAI_PRACTICE_MODEL") or "gpt-5-mini").strip()
    if not model_name:
        model_name = "gpt-5-mini"
    model_name = model_name.lower().replace(" ", "-")

    payload = {
        "model": model_name,
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
    except HTTPError as error:
        try:
            error_body = error.read().decode("utf-8", errors="replace")
        except Exception:
            error_body = "<no body>"
        logger.warning(
            "Practice AI request failed with HTTP %s for model '%s': %s",
            error.code,
            model_name,
            error_body,
        )
        return None
    except (URLError, TimeoutError) as error:
        logger.warning("Practice AI request failed for model '%s': %s", model_name, error)
        return None
    except json.JSONDecodeError as error:
        logger.warning("Practice AI returned invalid JSON for model '%s': %s", model_name, error)
        return None

    raw_text = _extract_response_text(response_payload)
    if not raw_text:
        logger.warning("Practice AI returned no text output for model '%s'.", model_name)
        return None

    try:
        return json.loads(raw_text)
    except json.JSONDecodeError as error:
        logger.warning(
            "Practice AI returned non-JSON output for model '%s': %s | payload=%s",
            model_name,
            error,
            raw_text,
        )
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
            "Return JSON only. For each word, provide a concise translation into Russian and a short English-only meaning or usage note. "
            "The note must be entirely in English and must not contain Russian text, translations, or bilingual examples."
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
            "Return JSON only. For each word, write one natural, slightly challenging, and interesting English sentence with exactly one blank written as ____ . "
            "Aim for variety in context and sentence structure, roughly CEFR A2-B2 depending on the word, and avoid overly simple textbook phrasing. "
            "The sentence should still be clear enough for a learner to solve from context, and the missing word must be exactly equal to the target word."
        ),
        {
            "task": "Create one slightly more advanced and engaging fill-in-the-blank sentence for each vocabulary word.",
            "words": source_words,
            "response_format": {
                "exercises": [
                    {"sentence": "I ____ every morning.", "answer": "run"}
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
        if not sentence or "____" not in sentence or not answer:
            continue
        exercises.append(
            {
                "sentence": sentence,
                "answer": answer,
            }
        )

    return exercises or _fallback_fill_blanks(source_words)


def _parse_fill_blanks_editor(raw_value):
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
        answer = str(item.get("answer", "")).strip()
        sentence = str(item.get("sentence", "")).strip()
        selected = bool(item.get("selected"))
        if not answer:
            continue
        cleaned.append(
            {
                "answer": answer,
                "sentence": sentence,
                "selected": selected,
            }
        )
    return cleaned


def _build_fill_blanks(words, requested_items):
    word_lookup = {}
    for item in words:
        term = (item.get("term") or "").strip()
        if term and term not in word_lookup:
            word_lookup[term] = item

    selected_terms = []
    manual_sentences = {}
    for item in requested_items:
        term = item["answer"]
        if term not in word_lookup or not item.get("selected"):
            continue
        if term not in selected_terms:
            selected_terms.append(term)
        if item.get("sentence"):
            manual_sentences[term] = item["sentence"]

    generated_map = {}
    terms_to_generate = [term for term in selected_terms if term not in manual_sentences]
    if terms_to_generate:
        generated_items = _generate_fill_blanks([word_lookup[term] for term in terms_to_generate])
        for item in generated_items:
            term = str(item.get("answer", "")).strip()
            sentence = str(item.get("sentence", "")).strip()
            if term and sentence and term not in generated_map:
                generated_map[term] = sentence

    result = []
    for term in selected_terms:
        sentence = manual_sentences.get(term) or generated_map.get(term, "")
        if not sentence:
            continue
        result.append({"answer": term, "sentence": sentence})
    return result


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
        if not sentence or not answer:
            continue
        cleaned.append(
            {
                "id": index,
                "sentence": sentence,
                "answer": answer,
                "selected": True,
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
        fill_blanks_editor = _parse_fill_blanks_editor(request.POST.get("fill_blanks_json"))
        if words is None or cards is None or fill_blanks_editor is None:
            return HttpResponseForbidden("Invalid practice payload.")

        words = _enrich_words_with_openai(words)
        fill_blanks_items = _build_fill_blanks(words, fill_blanks_editor)
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
    fill_blanks_word_bank = [word.term for word in words if word.term]
    if not can_edit:
        if fill_blanks_items:
            random.shuffle(fill_blanks_items)
        student_bank = list(dict.fromkeys(item["answer"] for item in fill_blanks_items if item.get("answer")))
        fill_blanks_word_bank = student_bank or fill_blanks_word_bank
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


