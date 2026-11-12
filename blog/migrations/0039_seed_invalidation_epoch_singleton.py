"""Сидирование singleton-строки InvalidationEpoch (pk=1) + constraint singleton.

Таблица создана миграцией 0037 без начальной строки; get_epoch() ранее
возвращал "initial" при её отсутствии, что могло вернуть запросы в старый
кэш-namespace. Здесь:
  1) удаляются посторонние строки (pk != 1) — коррупт-состояние;
  2) создаётся строка pk=1 со случайной эпохой, если её нет (идемпотентно);
  3) добавляется CHECK (id = 1) — запрет второй строки на уровне БД.

Reverse: constraint снимается; строка НЕ удаляется (её удаление вернуло бы
page cache к устаревшему namespace и не имеет практического смысла).
"""
from django.db import migrations
from django.db.models import CheckConstraint, Q
from uuid import uuid4


def seed_epoch(apps, schema_editor):
    InvalidationEpoch = apps.get_model("blog", "InvalidationEpoch")
    # Коррупт-состояние: строки с pk != 1 несовместимы с constraint.
    InvalidationEpoch.objects.exclude(pk=1).delete()
    if not InvalidationEpoch.objects.filter(pk=1).exists():
        InvalidationEpoch.objects.create(pk=1, epoch=uuid4().hex)


def seed_epoch_reverse(apps, schema_editor):
    # Намеренно пусто: см. docstring — строка остаётся.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("blog", "0038_used_form_token"),
    ]

    operations = [
        migrations.RunPython(seed_epoch, seed_epoch_reverse),
        migrations.AddConstraint(
            model_name="invalidationepoch",
            constraint=CheckConstraint(condition=Q(id=1), name="invalidation_epoch_singleton"),
        ),
    ]
