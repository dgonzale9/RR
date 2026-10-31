"""Link maintenance proposals to their existing intake destination publication model."""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Add a nullable relation compatible with existing proposals and workers."""

    dependencies = [("resource_registry", "0007_preserve_explicit_updates_on_rollback")]

    operations = [
        migrations.AddField(
            model_name="datasetchange",
            name="publication",
            field=models.ForeignKey(
                blank=True,
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="dataset_changes",
                to="resource_registry.stagedresourcepublication",
            ),
        ),
    ]
