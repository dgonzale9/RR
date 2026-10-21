"""Add JSON Lines to the supported source artifact formats."""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Extend artifact choices without rewriting existing artifacts."""

    dependencies = [
        ("resource_registry", "0002_staged_resource_intake_management_permission"),
    ]

    operations = [
        migrations.AlterField(
            model_name="datasetartifact",
            name="format",
            field=models.CharField(
                choices=[
                    ("CSV", "CSV"),
                    ("PARQUET", "Parquet"),
                    ("JSON", "JSON"),
                    ("JSONL", "JSON Lines"),
                    ("OTHER", "Other"),
                ],
                max_length=20,
            ),
        ),
    ]
