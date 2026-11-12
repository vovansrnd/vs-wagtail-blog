from django.db import migrations


def set_historical_flag(apps, schema_editor):
    Comment = apps.get_model('blog', 'Comment')
    # Помечаем все существующие комментарии как исторические
    Comment.objects.all().update(is_historical=True)


class Migration(migrations.Migration):
    dependencies = [
        ('blog', '0013_comment_is_historical_alter_comment_author_email_and_more'),
    ]

    operations = [
        migrations.RunPython(set_historical_flag),
    ]
