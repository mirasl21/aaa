from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("schedule", "0003_calendarevent_students"),
    ]

    operations = [
        migrations.CreateModel(
            name="LessonPractice",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(blank=True, max_length=255)),
                ("description", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("event", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="practice", to="schedule.calendarevent")),
            ],
            options={"ordering": ["event__start_at", "id"]},
        ),
        migrations.CreateModel(
            name="PracticeCard",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("front_text", models.TextField()),
                ("back_text", models.TextField()),
                ("position", models.PositiveIntegerField(default=0)),
                ("practice", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="cards", to="schedule.lessonpractice")),
            ],
            options={"ordering": ["position", "id"]},
        ),
        migrations.CreateModel(
            name="PracticeWord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("term", models.CharField(max_length=255)),
                ("translation", models.CharField(blank=True, max_length=255)),
                ("note", models.TextField(blank=True)),
                ("position", models.PositiveIntegerField(default=0)),
                ("practice", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="words", to="schedule.lessonpractice")),
            ],
            options={"ordering": ["position", "id"]},
        ),
    ]
