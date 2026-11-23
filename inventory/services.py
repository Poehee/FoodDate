"""Changes to freezers and drawers. Views call these instead of changing models directly."""

from django.db import transaction

from .models import Compartment, Freezer


class RuleError(Exception):
    """A change that the rules in PLAN.md do not allow."""


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


def _lock(freezer: Freezer) -> list[Compartment]:
    """Lock the freezer for the rest of the transaction and return its compartments in order."""
    Freezer.objects.select_for_update().get(pk=freezer.pk)
    return list(freezer.compartments.order_by("position", "pk"))


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
    compartments = _lock(freezer)
    if not freezer.has_drawers:
        raise RuleError("A toploader has no drawers.")
    position = max((c.position for c in compartments), default=0) + 1
    return Compartment.objects.create(
        freezer=freezer, name=name, capacity=capacity, position=position
    )


@transaction.atomic
def move_drawer(freezer: Freezer, compartment_id: int, *, up: bool) -> None:
    compartments = _lock(freezer)
    index, _ = _drawer(freezer, compartments, compartment_id)
    other = index - 1 if up else index + 1
    if 0 <= other < len(compartments):
        compartments[index], compartments[other] = compartments[other], compartments[index]
        _renumber(compartments)


@transaction.atomic
def delete_drawer(freezer: Freezer, compartment_id: int) -> None:
    compartments = _lock(freezer)
    _, compartment = _drawer(freezer, compartments, compartment_id)
    if len(compartments) == 1:
        raise RuleError("A freezer needs at least one drawer.")
    compartment.delete()
