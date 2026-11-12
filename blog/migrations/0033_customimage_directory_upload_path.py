# Store new CustomImage uploads in images/<year>/<slug>/ from this point on.
# Existing file paths are not moved by this schema migration.
from django.db import migrations
import wagtail.images.models
import wagtail_image_directories.models


class Migration(migrations.Migration):
    dependencies = [
        ("blog", "0032_remove_comment_blog_commen_page_id_d916c6_idx_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="customimage",
            name="file",
            field=wagtail.images.models.WagtailImageField(
                height_field="height",
                max_length=100,
                upload_to=wagtail_image_directories.models.directory_image_upload_to,
                verbose_name="file",
                width_field="width",
            ),
        ),
    ]
