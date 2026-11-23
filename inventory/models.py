from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Lower
from django.urls import reverse

MAX_NAME_LENGTH = 40
MAX_CAPACITY = 999


class Freezer(models.Model):
    class Kind(models.TextChoices):
        DRAWERS = "drawers", "Drawers"
        TOPLOADER = "toploader", "Toploader"

    name = models.CharField(max_length=MAX_NAME_LENGTH)
    kind = models.CharField(max_length=10, choices=Kind.choices)

    class Meta:
        ordering = [Lower("name")]
        constraints = [
            models.UniqueConstraint(Lower("name"), name="freezer_name_unique_ci"),
            models.CheckConstraint(condition=~Q(name=""), name="freezer_name_not_empty"),
            models.CheckConstraint(
                condition=Q(kind__in=["drawers", "toploader"]), name="freezer_kind_valid"
            ),
        ]

    def __str__(self) -> str:
        return self.name

    def get_absolute_url(self) -> str:
        return reverse("inventory:freezer_detail", args=[self.pk])

    @property
    def has_drawers(self) -> bool:
        return self.kind == self.Kind.DRAWERS


class Compartment(models.Model):
    """A drawer, or the single unnamed space of a toploader."""

    freezer = models.ForeignKey(Freezer, on_delete=models.CASCADE, related_name="compartments")
    # NULL marks a toploader's unnamed space, so the partial unique constraint below can
    # allow exactly one per freezer
    name = models.CharField(max_length=MAX_NAME_LENGTH, null=True, blank=True)  # noqa: DJ001
    capacity = models.PositiveSmallIntegerField(help_text="In portions")
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "pk"]
        constraints = [
            models.UniqueConstraint(
                F("freezer"), Lower("name"), name="compartment_name_unique_ci_per_freezer"
            ),
            models.UniqueConstraint(
                fields=["freezer"],
                condition=Q(name__isnull=True),
                name="compartment_one_unnamed_per_freezer",
            ),
            models.CheckConstraint(condition=~Q(name=""), name="compartment_name_not_empty"),
            models.CheckConstraint(
                condition=Q(capacity__gte=1, capacity__lte=MAX_CAPACITY),
                name="compartment_capacity_range",
            ),
        ]

    def __str__(self) -> str:
        return self.name or self.freezer.name
