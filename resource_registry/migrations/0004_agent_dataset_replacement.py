"""Record exact replacement targets and allow reversion to historical file bytes."""

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    """Add nullable replacement targets without changing existing submissions."""

    dependencies = [("resource_registry", "0003_datasetartifact_jsonl_format")]

    operations = [
        migrations.AddField(
            model_name="registrationsubmission",
            name="replacement_dataset",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="replacement_submissions",
                to="resource_registry.dataset",
            ),
        ),
        migrations.AddField(
            model_name="registrationsubmission",
            name="replacement_workbook_group",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="replacement_submissions",
                to="resource_registry.workbookingestiongroup",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="datasetversion",
            name="dataset_version_hash_uniq",
        ),
    ]
