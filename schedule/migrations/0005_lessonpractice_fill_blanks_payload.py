from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("schedule", "0004_lessonpractice_practiceword_practicecard"),
    ]

    operations = [
        migrations.AddField(
            model_name="lessonpractice",
            name="fill_blanks_payload",
            field=models.TextField(blank=True, default="[]"),
        ),
    ]
