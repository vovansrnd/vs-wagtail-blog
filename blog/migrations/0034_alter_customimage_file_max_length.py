# Widen the database path field for long descriptive filenames and folder slugs.
# Existing media files are not moved by this schema-only migration.
from django.db import migrations
import wagtail.images.models
import wagtail_image_directories.models


class Migration(migrations.Migration):
    dependencies = [
        ("blog", "0033_customimage_directory_upload_path"),
    ]

    operations = [
        migrations.AlterField(
            model_name="customimage",
            name="file",
            field=wagtail.images.models.WagtailImageField(
                height_field="height",
                max_length=512,
                upload_to=wagtail_image_directories.models.directory_image_upload_to,
                verbose_name="file",
                width_field="width",
            ),
        ),
    ]
