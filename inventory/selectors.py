"""Read-only views of the inventory for pages."""

from dataclasses import dataclass

from .models import Compartment, Freezer

# Above this many portions a drawer shows one bar instead of a cell per portion
MAX_CELLS = 24


@dataclass(frozen=True)
class CompartmentView:
    pk: int
    label: str
    capacity: int
    used: int

    @property
    def free(self) -> int:
        return self.capacity - self.used

    @property
    def cells(self) -> list[bool] | None:
        if self.capacity > MAX_CELLS:
            return None
        return [True] * self.used + [False] * self.free

    @property
    def fill_percent(self) -> int:
        return round(100 * self.used / self.capacity)


@dataclass(frozen=True)
class Totals:
    capacity: int
    used: int

    @property
    def free(self) -> int:
        return self.capacity - self.used


def compartment_view(compartment: Compartment, freezer: Freezer) -> CompartmentView:
    # Food arrives in M2. Until then every compartment is empty.
    return CompartmentView(
        pk=compartment.pk,
        label=compartment.name or freezer.name,
        capacity=compartment.capacity,
        used=0,
    )


def freezer_overview(freezer: Freezer) -> dict:
    compartments = [compartment_view(c, freezer) for c in freezer.compartments.all()]
    totals = Totals(
        capacity=sum(c.capacity for c in compartments),
        used=sum(c.used for c in compartments),
    )
    return {"compartments": compartments, "totals": totals}
