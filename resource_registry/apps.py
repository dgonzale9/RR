from django.apps import AppConfig


class ResourceRegistryConfig(AppConfig):
    verbose_name = "Resource Registry"
    default_auto_field = "django.db.models.AutoField"
    name = "resource_registry"

    def ready(self) -> None:
        """Register Resource Registry lifecycle signal receivers."""
        from resource_registry import signals  # noqa: F401
