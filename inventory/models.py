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


MAX_WEEKS = 520  # ten years


class Settings(models.Model):
    """General settings. There is exactly one row, with id 1."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    default_weeks = models.PositiveSmallIntegerField(
        "Default shelf life", default=12, help_text="In weeks, for food types without their own"
    )
    color_window_weeks = models.PositiveSmallIntegerField(
        "Color change starts",
        default=4,
        help_text="Weeks before expiration that food turns from green towards red",
    )

    class Meta:
        verbose_name_plural = "settings"
        constraints = [
            models.CheckConstraint(condition=Q(id=1), name="settings_single_row"),
            models.CheckConstraint(
                condition=Q(default_weeks__gte=1, default_weeks__lte=MAX_WEEKS),
                name="settings_default_weeks_range",
            ),
            models.CheckConstraint(
                condition=Q(color_window_weeks__gte=1, color_window_weeks__lte=52),
                name="settings_color_window_range",
            ),
        ]

    def __str__(self) -> str:
        return "Settings"

    @classmethod
    def load(cls) -> "Settings":
        return cls.objects.get_or_create(pk=1)[0]


class FoodType(models.Model):
    name = models.CharField(max_length=MAX_NAME_LENGTH)
    weeks = models.PositiveSmallIntegerField(
        "Shelf life", null=True, blank=True, help_text="In weeks. Leave empty for the default."
    )

    class Meta:
        ordering = [Lower("name")]
        constraints = [
            models.UniqueConstraint(Lower("name"), name="food_type_name_unique_ci"),
            models.CheckConstraint(condition=~Q(name=""), name="food_type_name_not_empty"),
            models.CheckConstraint(
                condition=Q(weeks__isnull=True) | Q(weeks__gte=1, weeks__lte=MAX_WEEKS),
                name="food_type_weeks_range",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class FoodItem(models.Model):
    """One container in a freezer. Its id is the number written on the container."""

    content = models.CharField(max_length=80)
    food_type = models.ForeignKey(FoodType, on_delete=models.PROTECT, related_name="items")
    compartment = models.ForeignKey(Compartment, on_delete=models.PROTECT, related_name="items")
    frozen_on = models.DateField()
    # Copied from the food type or settings when saved, so later setting changes don't
    # move the expiration of food already frozen
    weeks = models.PositiveSmallIntegerField()
    portions = models.PositiveSmallIntegerField(default=1)
    expires_on = models.GeneratedField(
        expression=models.Func(
            F("frozen_on"),
            F("weeks"),
            template="(%(expressions)s)",
            arg_joiner=" + 7 * ",
            output_field=models.DateField(),
        ),
        output_field=models.DateField(),
        db_persist=True,
    )

    class Meta:
        ordering = ["expires_on", "pk"]
        constraints = [
            models.CheckConstraint(condition=~Q(content=""), name="food_item_content_not_empty"),
            models.CheckConstraint(
                condition=Q(portions__gte=1, portions__lte=MAX_CAPACITY),
                name="food_item_portions_range",
            ),
            models.CheckConstraint(
                condition=Q(weeks__gte=1, weeks__lte=MAX_WEEKS), name="food_item_weeks_range"
            ),
        ]
        indexes = [models.Index(fields=["expires_on"])]

    def __str__(self) -> str:
        return f"#{self.pk} {self.content}"

    def get_absolute_url(self) -> str:
        return reverse("inventory:item_detail", args=[self.pk])
