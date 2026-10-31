"""Select the explicit current version with a bounded legacy compatibility fallback."""

from django.db.models import F, OuterRef, Q, Subquery

from resource_registry.models import DatasetVersion


def current_version_filter(*, version_prefix: str = "") -> Q:
    """Filter version rows or related records to their dataset's selected version.

    Args:
        version_prefix: ORM traversal to DatasetVersion, including the trailing separator.

    Returns:
        A predicate retaining usable legacy rows only when no version has been assigned.
    """
    dataset_prefix = f"{version_prefix}dataset__"
    return Q(
        **{
            f"{dataset_prefix}current_version__isnull": True,
            f"{version_prefix}id": Subquery(
                DatasetVersion.objects.filter(
                    dataset_id=OuterRef(f"{version_prefix}dataset_id"),
                    status="READY",
                    artifacts__lifecycle_status="ACTIVE",
                )
                .order_by("-version_number")
                .values("id")[:1]
            ),
        }
    ) | Q(**{f"{version_prefix}id": F(f"{dataset_prefix}current_version_id")})
