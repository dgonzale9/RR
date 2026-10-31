"""Filter the authorized Dataset Admin catalog by inclusive calendar dates."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import date, datetime, time, timedelta
from typing import cast

from django import forms
from django.conf import settings
from django.contrib.admin import ModelAdmin
from django.contrib.admin.filters import ListFilter
from django.contrib.admin.views.main import PAGE_VAR, ChangeList
from django.db.models import Model, QuerySet
from django.http import HttpRequest
from django.utils import timezone

from resource_registry.models import Dataset

DATE_FIELDS = {
    "updated": ("current_version__created_at", "Last successful update"),
    "created": ("created_at", "Created date"),
}


class DatasetDateRangeForm(forms.Form):
    """Validate an optional, inclusive date range for a known catalog date field."""

    date_field = forms.ChoiceField(
        label="Date field",
        choices=[(key, label) for key, (_, label) in DATE_FIELDS.items()],
        error_messages={"invalid_choice": "Select a valid date field."},
    )
    date_from = forms.DateField(
        label="From",
        required=False,
        input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        error_messages={"invalid": "Enter a valid date (YYYY-MM-DD)."},
    )
    date_to = forms.DateField(
        label="To",
        required=False,
        input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        error_messages={"invalid": "Enter a valid date (YYYY-MM-DD)."},
    )

    def clean(self) -> dict[str, object]:
        """Validate date order and the exclusive upper boundary.

        Returns:
            Cleaned fields, adding form errors for invalid ranges.
        """
        cleaned = cast(dict[str, object], super().clean())
        date_from = cast(date | None, cleaned.get("date_from"))
        date_to = cast(date | None, cleaned.get("date_to"))
        if date_to == date.max:
            self.add_error("date_to", "To must be earlier than 9999-12-31.")
        if date_from and date_to and date_from > date_to:
            self.add_error("date_from", "From must be on or before To.")
        return cleaned


class DatasetDateRangeFilter(ListFilter):
    """Apply validated dates to the scoped queryset without loading datasets."""

    title = "Date range"
    template = "admin/resource_registry/dataset/date_range_filter.html"
    parameter_names = ("date_field", "date_from", "date_to")
    form: DatasetDateRangeForm

    def __init__(
        self,
        request: HttpRequest,
        params: dict[str, list[str]],
        model: type[Model],
        model_admin: ModelAdmin[Dataset],
    ) -> None:
        """Consume date query parameters and bind the sidebar form.

        Args:
            request: Current staff request, including all catalog filters.
            params: Mutable remaining query parameters; date keys are removed.
            model: Dataset model registered with the Admin.
            model_admin: Admin providing the already authorized queryset.
        """
        super().__init__(request, params, model, model_admin)
        values = {name: params.pop(name)[-1] for name in self.parameter_names if name in params}
        self.used_parameters.update(values)
        self.form = DatasetDateRangeForm({"date_field": "updated", **values})

    def has_output(self) -> bool:
        """Keep the date controls visible even when the catalog is empty."""
        return True

    def expected_parameters(self) -> list[str | None]:
        """Return the date parameters consumed by the filter."""
        return list(self.parameter_names)

    def queryset(self, request: HttpRequest, queryset: QuerySet[Dataset]) -> QuerySet[Dataset]:
        """Restrict authorized rows to local dates, returning no rows on invalid input.

        Args:
            request: Current staff request.
            queryset: Catalog queryset already limited to authorized destinations.

        Returns:
            A lazy queryset bounded by local midnights, including the entire To date.
        """
        if not self.form.is_valid():
            return queryset.none()
        field_name = DATE_FIELDS[str(self.form.cleaned_data["date_field"])][0]
        for parameter, lookup, offset in (("date_from", "gte", 0), ("date_to", "lt", 1)):
            selected_date = cast(date | None, self.form.cleaned_data[parameter])
            if selected_date is None:
                continue
            boundary = datetime.combine(selected_date + timedelta(days=offset), time.min)
            if settings.USE_TZ:
                boundary = timezone.make_aware(boundary, timezone.get_current_timezone())
            queryset = queryset.filter(**{f"{field_name}__{lookup}": boundary})
        return queryset

    def choices(self, changelist: ChangeList) -> Iterator[Mapping[str, object]]:
        """Render the form while retaining search, sort and other filters.

        Args:
            changelist: Native Admin changelist containing navigation state.

        Yields:
            Form context and a clear link that both reset pagination.
        """
        removed_parameters = [*self.parameter_names, PAGE_VAR]
        yield {
            "form": self.form,
            "preserved_parameters": [
                (name, value)
                for name, values in self.request.GET.lists()
                if name not in removed_parameters
                for value in values
            ],
            "clear_url": changelist.get_query_string(remove=removed_parameters),
            "timezone": timezone.get_current_timezone_name(),
        }
