"""Read-only views of the inventory for pages."""

from dataclasses import dataclass, field
from datetime import date, timedelta

from django.db.models import Count, Sum
from django.utils import timezone
from django.utils.formats import date_format

from .models import Compartment, FoodItem, FoodType, Freezer, Settings

# Above this many portions a drawer shows one bar instead of a cell per portion
MAX_CELLS = 24
GREEN, RED = 145, 25  # OKLCH hues, see "Expiration colors" in PLAN.md


@dataclass(frozen=True)
class Expiry:
    days_left: int
    hue: int
    icon: str  # check, clock or alert
    text: str

    @property
    def is_expired(self) -> bool:
        return self.days_left < 0

    @property
    def is_urgent(self) -> bool:
        return self.days_left <= 0


def expiry(expires_on: date, color_window_weeks: int, today: date | None = None) -> Expiry:
    today = today or timezone.localdate()
    days = (expires_on - today).days
    window = color_window_weeks * 7
    share = min(max(days, 0), window) / window
    hue = round(RED + (GREEN - RED) * share)
    if days < 0:
        ago = -days
        return Expiry(days, hue, "alert", f"Expired {ago} day{'s' if ago != 1 else ''} ago")
    if days == 0:
        return Expiry(days, hue, "alert", "Expires today")
    if days < window:
        return Expiry(days, hue, "clock", f"{days} day{'s' if days != 1 else ''} left")
    return Expiry(days, hue, "check", f"Use by {date_format(expires_on)}")


def location_label(compartment: Compartment, freezer_count: int) -> str:
    """Drawer always, freezer name only when there is more than one freezer."""
    parts = []
    if freezer_count > 1:
        parts.append(compartment.freezer.name)
    if compartment.name:
        parts.append(compartment.name)
    return " / ".join(parts)


@dataclass(frozen=True)
class ItemView:
    item: FoodItem
    expiry: Expiry
    location: str = ""


@dataclass(frozen=True)
class CompartmentView:
    pk: int
    label: str
    capacity: int
    used: int
    hues: list[int] = field(default_factory=list)  # one per used portion, soonest first
    expired: int = 0  # expired portions

    @property
    def free(self) -> int:
        return self.capacity - self.used

    @property
    def cells(self) -> list[int | None] | None:
        """A hue per used portion, then None per free one. None above MAX_CELLS."""
        if self.capacity > MAX_CELLS:
            return None
        return self.hues + [None] * self.free

    @property
    def fill_percent(self) -> int:
        return round(100 * self.used / self.capacity)

    @property
    def bar_hue(self) -> int | None:
        return self.hues[0] if self.hues else None


@dataclass(frozen=True)
class Totals:
    capacity: int
    used: int

    @property
    def free(self) -> int:
        return self.capacity - self.used


def item_views(items, window: int, freezer_count: int = 1) -> list[ItemView]:
    return [
        ItemView(i, expiry(i.expires_on, window), location_label(i.compartment, freezer_count))
        for i in items
    ]


def compartment_view(compartment: Compartment, items: list[FoodItem], window: int):
    hues, expired = [], 0
    for item in sorted(items, key=lambda i: (i.expires_on, i.pk)):
        state = expiry(item.expires_on, window)
        hues += [state.hue] * item.portions
        expired += item.portions if state.is_expired else 0
    return CompartmentView(
        pk=compartment.pk,
        label=compartment.name or compartment.freezer.name,
        capacity=compartment.capacity,
        used=len(hues),
        hues=hues,
        expired=expired,
    )


def freezer_overview(freezer: Freezer, selected_id: int | None = None) -> dict:
    window = Settings.load().color_window_weeks
    compartments = list(freezer.compartments.select_related("freezer"))
    items = list(
        FoodItem.objects.filter(compartment__freezer=freezer).select_related(
            "food_type", "compartment__freezer"
        )
    )
    by_compartment: dict[int, list[FoodItem]] = {c.pk: [] for c in compartments}
    for item in items:
        by_compartment[item.compartment_id].append(item)
    views = [compartment_view(c, by_compartment[c.pk], window) for c in compartments]
    totals = Totals(capacity=sum(c.capacity for c in views), used=sum(c.used for c in views))

    selected = next((c for c in compartments if c.pk == selected_id), None)
    if selected is None and compartments:
        selected = compartments[0]
    selected_view = next((v for v in views if selected and v.pk == selected.pk), None)
    selected_items = item_views(by_compartment[selected.pk], window) if selected else []
    return {
        "compartments": views,
        "totals": totals,
        "selected": selected,
        "selected_view": selected_view,
        "selected_items": selected_items,
    }


def compartment_state(compartment: Compartment) -> dict:
    """What the freezer page needs to redraw one compartment after a change."""
    window = Settings.load().color_window_weeks
    view = compartment_view(compartment, list(compartment.items.all()), window)
    totals = Compartment.objects.filter(freezer=compartment.freezer_id).aggregate(
        capacity=Sum("capacity")
    )
    used = (
        FoodItem.objects.filter(compartment__freezer=compartment.freezer_id).aggregate(
            used=Sum("portions")
        )["used"]
        or 0
    )
    return {
        "compartment": {
            "id": view.pk,
            "capacity": view.capacity,
            "used": view.used,
            "free": view.free,
            "hues": view.hues,
            "expired": view.expired,
        },
        "totals": {"capacity": totals["capacity"], "used": used, "free": totals["capacity"] - used},
    }


def stock_summary() -> dict:
    return FoodItem.objects.aggregate(portions=Sum("portions"), containers=Count("pk"))


def stock_type_counts() -> list:
    """Food types that are in stock, with their number of containers."""
    return [
        (t, t.containers)
        for t in FoodType.objects.annotate(containers=Count("items")).filter(containers__gt=0)
    ]


def filter_stock(
    *,
    types: list[int] | None = None,
    expires: str = "",
    expires_from: date | None = None,
    expires_to: date | None = None,
    today: date | None = None,
):
    """All food, soonest expiration first, narrowed by the stock list filters.

    expired:    expires before today
    7, 14, 30:  not expired yet, and expires on or before today + that many days
    range:      expires between the two dates, both included; either may be empty
    """
    today = today or timezone.localdate()
    items = FoodItem.objects.select_related("food_type", "compartment__freezer").order_by(
        "expires_on", "pk"
    )
    if types:
        items = items.filter(food_type__in=types)
    if expires == "expired":
        items = items.filter(expires_on__lt=today)
    elif expires in ("7", "14", "30"):
        items = items.filter(
            expires_on__gte=today, expires_on__lte=today + timedelta(days=int(expires))
        )
    elif expires == "range":
        if expires_from:
            items = items.filter(expires_on__gte=expires_from)
        if expires_to:
            items = items.filter(expires_on__lte=expires_to)
    return items
