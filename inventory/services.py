"""Changes to the inventory. Views call these instead of changing models directly.

Every change that affects capacity locks the compartments involved first, so two people
saving at the same time cannot overfill a drawer together.
"""

from dataclasses import dataclass
from datetime import date

from django.core import signing
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import Compartment, FoodItem, FoodType, Freezer, Settings

UNDO_SALT = "fooddate.undo"
UNDO_MAX_AGE = 120  # seconds; the page offers Undo for 5


class RuleError(Exception):
    """A change that the rules in PLAN.md do not allow."""


class StaleError(RuleError):
    """The page showed an older state than the database has."""


# Freezers and drawers


@transaction.atomic
def create_freezer(
    *,
    name: str,
    kind: str,
    capacity: int | None = None,
    drawer_count: int | None = None,
    drawer_capacity: int | None = None,
) -> Freezer:
    freezer = Freezer.objects.create(name=name, kind=kind)
    if kind == Freezer.Kind.TOPLOADER:
        Compartment.objects.create(freezer=freezer, name=None, capacity=capacity, position=0)
    else:
        Compartment.objects.bulk_create(
            Compartment(freezer=freezer, name=f"Drawer {i}", capacity=drawer_capacity, position=i)
            for i in range(1, drawer_count + 1)
        )
    return freezer


def lock_freezer(freezer: Freezer) -> list[Compartment]:
    """Lock the freezer and its compartments for the rest of the transaction."""
    Freezer.objects.select_for_update().get(pk=freezer.pk)
    return list(
        Compartment.objects.select_for_update().filter(freezer=freezer).order_by("position", "pk")
    )


def _renumber(compartments: list[Compartment]) -> None:
    for position, compartment in enumerate(compartments, start=1):
        compartment.position = position
    Compartment.objects.bulk_update(compartments, ["position"])


def _drawer(freezer: Freezer, compartments: list[Compartment], compartment_id: int):
    if not freezer.has_drawers:
        raise RuleError("A toploader has no drawers.")
    for index, compartment in enumerate(compartments):
        if compartment.pk == compartment_id:
            return index, compartment
    raise RuleError("That drawer no longer exists.")


@transaction.atomic
def add_drawer(freezer: Freezer, *, name: str, capacity: int) -> Compartment:
    compartments = lock_freezer(freezer)
    if not freezer.has_drawers:
        raise RuleError("A toploader has no drawers.")
    position = max((c.position for c in compartments), default=0) + 1
    return Compartment.objects.create(
        freezer=freezer, name=name, capacity=capacity, position=position
    )


@transaction.atomic
def move_drawer(freezer: Freezer, compartment_id: int, *, up: bool) -> None:
    compartments = lock_freezer(freezer)
    index, _ = _drawer(freezer, compartments, compartment_id)
    other = index - 1 if up else index + 1
    if 0 <= other < len(compartments):
        compartments[index], compartments[other] = compartments[other], compartments[index]
        _renumber(compartments)


@transaction.atomic
def delete_drawer(freezer: Freezer, compartment_id: int) -> None:
    compartments = lock_freezer(freezer)
    _, compartment = _drawer(freezer, compartments, compartment_id)
    if len(compartments) == 1:
        raise RuleError("A freezer needs at least one drawer.")
    if compartment.items.exists():
        raise RuleError(f"Take out or move the food in {compartment.name} first.")
    compartment.delete()


def check_capacities(freezer: Freezer) -> None:
    """Refuse capacities below what is already stored. Call inside the saving transaction."""
    for compartment in Compartment.objects.filter(freezer=freezer):
        used = used_portions(compartment)
        if compartment.capacity < used:
            label = compartment.name or freezer.name
            raise RuleError(f"{label} holds {used} portions, so its capacity can't be lower.")


@transaction.atomic
def delete_freezer(freezer: Freezer) -> None:
    lock_freezer(freezer)
    if FoodItem.objects.filter(compartment__freezer=freezer).exists():
        raise RuleError(f"Take out or move the food in {freezer.name} first.")
    freezer.delete()


# Food


def used_portions(compartment: Compartment, exclude: FoodItem | None = None) -> int:
    items = compartment.items.all()
    if exclude is not None:
        items = items.exclude(pk=exclude.pk)
    return items.aggregate(total=Sum("portions"))["total"] or 0


def _lock_compartments(*compartments: Compartment) -> dict[int, Compartment]:
    """Lock and re-read compartments. Use the returned rows: a capacity read before the
    lock may already be out of date."""
    ids = sorted({c.pk for c in compartments})  # same order everywhere, so no deadlocks
    # of=("self",) locks only the compartment rows, not the joined freezer. Freezer edits
    # lock the freezer first, so locking it here too could deadlock.
    locked = (
        Compartment.objects.select_for_update(of=("self",))
        .select_related("freezer")
        .filter(pk__in=ids)
    )
    return {c.pk: c for c in locked.order_by("pk")}


def _require_space(compartment: Compartment, portions: int, exclude=None) -> None:
    free = compartment.capacity - used_portions(compartment, exclude)
    if portions > free:
        noun = "portion" if free == 1 else "portions"
        raise RuleError(f"Only {free} {noun} free in {location_name(compartment)}.")


def location_name(compartment: Compartment) -> str:
    return compartment.name or compartment.freezer.name


def weeks_for(food_type: FoodType) -> int:
    return food_type.weeks or Settings.load().default_weeks


def _check_date(frozen_on: date) -> None:
    if frozen_on > timezone.localdate():
        raise RuleError("The freeze date can't be in the future.")


@transaction.atomic
def add_food(
    *,
    content: str,
    food_type: FoodType,
    compartment: Compartment,
    frozen_on: date,
    portions: int,
    containers: int = 1,
) -> list[FoodItem]:
    """Add one or more identical containers. All of them fit, or none are added."""
    _check_date(frozen_on)
    compartment = _lock_compartments(compartment)[compartment.pk]
    _require_space(compartment, portions * containers)
    weeks = weeks_for(food_type)
    return FoodItem.objects.bulk_create(
        FoodItem(
            content=content,
            food_type=food_type,
            compartment=compartment,
            frozen_on=frozen_on,
            weeks=weeks,
            portions=portions,
        )
        for _ in range(containers)
    )


@transaction.atomic
def update_food(item: FoodItem, **changes) -> FoodItem:
    """Edit or move a container. Changing the type refreshes its shelf life."""
    current = FoodItem.objects.select_related("compartment").get(pk=item.pk)
    target = changes.get("compartment", current.compartment)
    target = _lock_compartments(current.compartment, target)[target.pk]
    current = FoodItem.objects.select_for_update().get(pk=item.pk)
    if "compartment" in changes:
        changes["compartment"] = target
    if "frozen_on" in changes:
        _check_date(changes["frozen_on"])
    if changes.get("food_type") and changes["food_type"].pk != current.food_type_id:
        current.weeks = weeks_for(changes["food_type"])
    for field, value in changes.items():
        setattr(current, field, value)
    _require_space(target, current.portions, exclude=current)
    current.save()
    current.refresh_from_db(fields=["expires_on"])
    return current


@dataclass(frozen=True)
class TakeResult:
    item: FoodItem | None  # None when the last portion was taken
    compartment: Compartment
    undo_token: str


def _snapshot(item: FoodItem) -> dict:
    return {
        "id": item.pk,
        "content": item.content,
        "food_type": item.food_type_id,
        "compartment": item.compartment_id,
        "frozen_on": item.frozen_on.isoformat(),
        "weeks": item.weeks,
        "portions": item.portions,
    }


@transaction.atomic
def take_portion(item_id: int, *, expected_portions: int) -> TakeResult:
    """Take one portion out. At zero the container is deleted.

    expected_portions is what the page showed, so a double tap or an outdated page
    cannot take more than the person meant to.
    """
    item = FoodItem.objects.select_related("compartment").filter(pk=item_id).first()
    if item is None:
        raise StaleError("That food was already taken out.")
    _lock_compartments(item.compartment)
    item = FoodItem.objects.select_for_update().get(pk=item_id)
    if item.portions != expected_portions:
        raise StaleError("Someone else just changed this. The freezer now shows the latest.")

    before = _snapshot(item)
    if item.portions == 1:
        compartment = item.compartment
        item.delete()
        remaining = None
    else:
        item.portions -= 1
        item.save(update_fields=["portions"])
        compartment, remaining = item.compartment, item
    token = signing.dumps({"before": before, "after": before["portions"] - 1}, salt=UNDO_SALT)
    return TakeResult(item=remaining, compartment=compartment, undo_token=token)


@transaction.atomic
def undo_take(token: str) -> FoodItem:
    """Put back the portion taken with this token, if nothing changed since."""
    try:
        data = signing.loads(token, salt=UNDO_SALT, max_age=UNDO_MAX_AGE)
    except signing.BadSignature as error:
        raise RuleError("It's too late to undo this.") from error
    before, after = data["before"], data["after"]
    compartment = Compartment.objects.filter(pk=before["compartment"]).first()
    if compartment is None:
        raise RuleError("That drawer no longer exists.")
    compartment = _lock_compartments(compartment)[compartment.pk]
    item = FoodItem.objects.select_for_update().filter(pk=before["id"]).first()

    if after == 0:
        if item is not None:
            raise RuleError("This was already undone.")
        _require_space(compartment, before["portions"])
        food_type = FoodType.objects.filter(pk=before["food_type"]).first()
        if food_type is None:
            raise RuleError("Its food type no longer exists.")
        item = FoodItem(
            pk=before["id"],
            content=before["content"],
            food_type=food_type,
            compartment=compartment,
            frozen_on=date.fromisoformat(before["frozen_on"]),
            weeks=before["weeks"],
            portions=before["portions"],
        )
        item.save(force_insert=True)
    else:
        if item is None or item.portions != after or item.compartment_id != compartment.pk:
            raise RuleError("This changed since, so it can't be undone.")
        _require_space(compartment, before["portions"], exclude=item)
        item.portions = before["portions"]
        item.save(update_fields=["portions"])
    return item


def used_by_compartment() -> dict[int, int]:
    rows = FoodItem.objects.values("compartment").annotate(total=Sum("portions"))
    return {row["compartment"]: row["total"] for row in rows}


def used_by_freezer(freezer: Freezer) -> int:
    return (
        FoodItem.objects.filter(compartment__freezer=freezer).aggregate(total=Sum("portions"))[
            "total"
        ]
        or 0
    )


def food_types_in_use() -> set[int]:
    return set(FoodItem.objects.values_list("food_type", flat=True).distinct())


@transaction.atomic
def delete_food_type(food_type_id: int) -> None:
    food_type = FoodType.objects.select_for_update().filter(pk=food_type_id).first()
    if food_type is None:
        raise RuleError("That food type no longer exists.")
    if food_type.items.exists():
        raise RuleError(f"{food_type.name} is used by food in your freezers.")
    if FoodType.objects.count() == 1:
        raise RuleError("Keep at least one food type.")
    food_type.delete()
