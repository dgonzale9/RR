"""Add the global staged-resource intake operations permission."""

from django.db import migrations


class Migration(migrations.Migration):
    """Create the permission used by Django Admin intake operators."""

    dependencies = [
        ("resource_registry", "0001_initial"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="stagedresourceintake",
            options={
                "permissions": (
                    ("manage_stagedresourceintake", "Can manage staged resource intakes"),
                ),
            },
        ),
    ]
