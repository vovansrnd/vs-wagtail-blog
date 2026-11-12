# Widen image paths for the full year/slug/filename prefix and permit Unicode slugs consistently.
# This migration changes database field metadata only; it does not move existing media files.
from django.db import migrations, models
import wagtail.images.models
import wagtail_image_directories.models


class Migration(migrations.Migration):
    dependencies = [
        ("blog", "0034_alter_customimage_file_max_length"),
        ("wagtail_image_directories", "0004_allow_unicode_directory_slug"),
    ]

    operations = [
        migrations.AlterField(
            model_name="customimage",
            name="file",
            field=wagtail.images.models.WagtailImageField(
                height_field="height",
                max_length=1024,
                upload_to=wagtail_image_directories.models.directory_image_upload_to,
                verbose_name="file",
                width_field="width",
            ),
        ),
        migrations.AlterField(
            model_name="customimage",
            name="slug",
            field=models.SlugField(
                allow_unicode=True,
                blank=True,
                db_index=True,
                max_length=255,
                verbose_name="Sub-directory (slug)",
            ),
        ),
    ]
