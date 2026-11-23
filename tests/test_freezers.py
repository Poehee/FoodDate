import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse
from pytest_django.asserts import assertContains, assertNotContains, assertRedirects

from inventory import services
from inventory.models import Compartment, Freezer
from inventory.selectors import MAX_CELLS, CompartmentView, freezer_overview

pytestmark = pytest.mark.django_db


def drawers(name="Kitchen", count=3, capacity=10):
    return services.create_freezer(
        name=name, kind="drawers", drawer_count=count, drawer_capacity=capacity
    )


def toploader(name="Garage", capacity=20):
    return services.create_freezer(name=name, kind="toploader", capacity=capacity)


def names(freezer):
    return list(freezer.compartments.values_list("name", flat=True))


def edit_data(freezer, action="save", name=None, **overrides):
    """POST data for the edit page, as the browser would send it unchanged."""
    compartments = list(freezer.compartments.all())
    data = {
        "action": action,
        "freezer-name": name or freezer.name,
        "compartments-TOTAL_FORMS": len(compartments),
        "compartments-INITIAL_FORMS": len(compartments),
    }
    for i, c in enumerate(compartments):
        data[f"compartments-{i}-id"] = c.pk
        data[f"compartments-{i}-capacity"] = c.capacity
        if freezer.has_drawers:
            data[f"compartments-{i}-name"] = c.name
    data.update(overrides)
    return data


class TestConstraints:
    def test_freezer_names_are_unique_regardless_of_case(self):
        Freezer.objects.create(name="Kitchen", kind="drawers")
        with pytest.raises(IntegrityError), transaction.atomic():
            Freezer.objects.create(name="KITCHEN", kind="toploader")

    def test_drawer_names_are_unique_per_freezer_regardless_of_case(self):
        freezer = drawers(count=1)
        with pytest.raises(IntegrityError), transaction.atomic():
            Compartment.objects.create(freezer=freezer, name="drawer 1", capacity=5)

    def test_same_drawer_name_is_allowed_in_another_freezer(self):
        drawers("Kitchen", count=1)
        drawers("Garage", count=1)
        assert Compartment.objects.filter(name="Drawer 1").count() == 2

    def test_toploader_has_exactly_one_unnamed_space(self):
        freezer = toploader()
        with pytest.raises(IntegrityError), transaction.atomic():
            Compartment.objects.create(freezer=freezer, name=None, capacity=5)

    @pytest.mark.parametrize("capacity", [0, 1000])
    def test_capacity_must_be_between_1_and_999(self, capacity):
        freezer = Freezer.objects.create(name="Kitchen", kind="drawers")
        with pytest.raises(IntegrityError), transaction.atomic():
            Compartment.objects.create(freezer=freezer, name="Top", capacity=capacity)

    def test_names_cannot_be_empty(self):
        with pytest.raises(IntegrityError), transaction.atomic():
            Freezer.objects.create(name="", kind="drawers")
        freezer = Freezer.objects.create(name="Kitchen", kind="drawers")
        with pytest.raises(IntegrityError), transaction.atomic():
            Compartment.objects.create(freezer=freezer, name="", capacity=5)

    def test_kind_must_be_known(self):
        with pytest.raises(IntegrityError), transaction.atomic():
            Freezer.objects.create(name="Kitchen", kind="fridge")


class TestServices:
    def test_create_drawer_freezer_names_and_orders_drawers(self):
        freezer = drawers(count=3, capacity=8)
        assert names(freezer) == ["Drawer 1", "Drawer 2", "Drawer 3"]
        assert {c.capacity for c in freezer.compartments.all()} == {8}

    def test_create_toploader_has_one_unnamed_space(self):
        freezer = toploader(capacity=25)
        assert [(c.name, c.capacity) for c in freezer.compartments.all()] == [(None, 25)]

    def test_add_drawer_goes_to_the_bottom(self):
        freezer = drawers(count=2)
        services.add_drawer(freezer, name="Bottom", capacity=4)
        assert names(freezer) == ["Drawer 1", "Drawer 2", "Bottom"]

    def test_toploader_cannot_get_drawers(self):
        with pytest.raises(services.RuleError):
            services.add_drawer(toploader(), name="Extra", capacity=4)

    def test_move_drawer_up_and_down(self):
        freezer = drawers(count=3)
        third = freezer.compartments.get(name="Drawer 3")
        services.move_drawer(freezer, third.pk, up=True)
        assert names(freezer) == ["Drawer 1", "Drawer 3", "Drawer 2"]
        services.move_drawer(freezer, third.pk, up=False)
        assert names(freezer) == ["Drawer 1", "Drawer 2", "Drawer 3"]

    def test_moving_past_the_end_changes_nothing(self):
        freezer = drawers(count=2)
        services.move_drawer(freezer, freezer.compartments.first().pk, up=True)
        assert names(freezer) == ["Drawer 1", "Drawer 2"]

    def test_drawer_of_another_freezer_is_refused(self):
        kitchen, garage = drawers("Kitchen"), drawers("Garage")
        with pytest.raises(services.RuleError):
            services.delete_drawer(kitchen, garage.compartments.first().pk)
        assert garage.compartments.count() == 3

    def test_last_drawer_cannot_be_deleted(self):
        freezer = drawers(count=1)
        with pytest.raises(services.RuleError):
            services.delete_drawer(freezer, freezer.compartments.get().pk)


class TestSelectors:
    def test_cells_up_to_the_limit_then_a_bar(self):
        assert len(CompartmentView(1, "A", MAX_CELLS, 0).cells) == MAX_CELLS
        assert CompartmentView(1, "A", MAX_CELLS + 1, 0).cells is None

    def test_totals_add_up_all_compartments(self):
        overview = freezer_overview(drawers(count=3, capacity=10))
        assert (overview["totals"].capacity, overview["totals"].used) == (30, 0)
        assert overview["totals"].free == 30

    def test_toploader_compartment_uses_the_freezer_name(self):
        overview = freezer_overview(toploader("Garage"))
        assert overview["compartments"][0].label == "Garage"


class TestPages:
    def test_home_invites_to_add_a_first_freezer(self, client):
        assertContains(client.get(reverse("inventory:home")), "Add your first freezer")
        drawers()
        assertNotContains(client.get(reverse("inventory:home")), "Add your first freezer")

    def test_freezer_list_opens_the_first_freezer_by_name(self, client):
        drawers("kitchen")
        garage = toploader("Garage")
        assertRedirects(client.get(reverse("inventory:freezer_list")), garage.get_absolute_url())

    def test_freezer_list_without_freezers_shows_empty_state(self, client):
        assertContains(client.get(reverse("inventory:freezer_list")), "Add freezer")

    def test_freezer_page_shows_drawers_in_order_with_portions(self, client):
        freezer = drawers(count=2, capacity=10)
        response = client.get(freezer.get_absolute_url())
        content = response.content.decode()
        assert content.index("Drawer 1") < content.index("Drawer 2")
        assertContains(response, 'of <span data-role="capacity">10</span> portions', count=2)
        assertContains(response, 'data-total="capacity">20<')

    def test_switch_only_appears_with_more_than_one_freezer(self, client):
        freezer = drawers()
        assertNotContains(client.get(freezer.get_absolute_url()), 'aria-label="Freezers"')
        toploader()
        assertContains(client.get(freezer.get_absolute_url()), 'aria-label="Freezers"')

    def test_large_capacity_shows_a_bar(self, client):
        freezer = toploader(capacity=MAX_CELLS + 1)
        response = client.get(freezer.get_absolute_url())
        assertContains(response, "portions--bar")
        assertNotContains(response, 'class="cell')

    def test_unknown_freezer_is_404(self, client):
        assert client.get(reverse("inventory:freezer_detail", args=[999])).status_code == 404


class TestCreate:
    url = reverse("inventory:freezer_create")

    def test_create_drawer_freezer(self, client):
        response = client.post(
            self.url,
            {"name": "  Kitchen ", "kind": "drawers", "drawer_count": 4, "drawer_capacity": 6},
        )
        freezer = Freezer.objects.get()
        assertRedirects(response, freezer.get_absolute_url())
        assert freezer.name == "Kitchen"
        assert freezer.compartments.count() == 4

    def test_create_toploader_ignores_drawer_fields(self, client):
        client.post(
            self.url, {"name": "Garage", "kind": "toploader", "capacity": 30, "drawer_count": 5}
        )
        freezer = Freezer.objects.get()
        assert [(c.name, c.capacity) for c in freezer.compartments.all()] == [(None, 30)]

    def test_duplicate_name_is_refused_with_a_clear_message(self, client):
        drawers("Kitchen")
        response = client.post(self.url, {"name": "kitchen", "kind": "toploader", "capacity": 5})
        assertContains(response, "You already have a freezer with this name.")
        assert Freezer.objects.count() == 1

    @pytest.mark.parametrize(
        ("data", "field_error"),
        [
            ({"name": "K", "kind": "drawers", "drawer_capacity": 5}, "Enter a number."),
            ({"name": "K", "kind": "toploader"}, "Enter a number."),
            ({"name": "K", "kind": "toploader", "capacity": 0}, "greater than or equal to 1"),
            ({"name": "K", "kind": "toploader", "capacity": 1000}, "less than or equal to 999"),
            ({"name": "K", "kind": "fridge", "capacity": 5}, "Select a valid choice"),
            ({"name": "   ", "kind": "toploader", "capacity": 5}, "This field is required"),
        ],
    )
    def test_invalid_input_is_explained(self, client, data, field_error):
        assertContains(client.post(self.url, data), field_error)
        assert not Freezer.objects.exists()


class TestEdit:
    def url(self, freezer):
        return reverse("inventory:freezer_edit", args=[freezer.pk])

    def test_rename_and_change_capacity(self, client):
        freezer = drawers(count=2, capacity=10)
        data = edit_data(freezer, name="Utility room", **{"compartments-1-capacity": 15})
        data["compartments-0-name"] = "Top"
        assertRedirects(client.post(self.url(freezer), data), freezer.get_absolute_url())
        freezer.refresh_from_db()
        assert freezer.name == "Utility room"
        assert [(c.name, c.capacity) for c in freezer.compartments.all()] == [
            ("Top", 10),
            ("Drawer 2", 15),
        ]

    def test_duplicate_drawer_names_are_refused(self, client):
        freezer = drawers(count=2)
        data = edit_data(freezer, **{"compartments-1-name": "drawer 1"})
        assertContains(client.post(self.url(freezer), data), "already has a drawer with this name")
        assert names(freezer) == ["Drawer 1", "Drawer 2"]

    def test_renaming_to_another_freezers_name_is_refused(self, client):
        drawers("Kitchen")
        garage = toploader("Garage")
        response = client.post(self.url(garage), edit_data(garage, name="KITCHEN"))
        assertContains(response, "You already have a freezer with this name.")

    def test_add_drawer_keeps_other_edits(self, client):
        freezer = drawers(count=1)
        data = edit_data(
            freezer,
            action="add",
            **{"compartments-0-name": "Top", "new-name": "Bottom", "new-capacity": 7},
        )
        response = client.post(self.url(freezer), data)
        assert response.status_code == 302
        assert [(c.name, c.capacity) for c in freezer.compartments.all()] == [
            ("Top", 10),
            ("Bottom", 7),
        ]

    def test_added_drawer_name_must_be_unique(self, client):
        freezer = drawers(count=1)
        data = edit_data(freezer, action="add", **{"new-name": "DRAWER 1", "new-capacity": 5})
        assertContains(client.post(self.url(freezer), data), "already has a drawer with this name")
        assert freezer.compartments.count() == 1

    def test_move_and_delete_buttons(self, client):
        freezer = drawers(count=3)
        second = freezer.compartments.get(name="Drawer 2")
        client.post(self.url(freezer), edit_data(freezer, action=f"up:{second.pk}"))
        assert names(freezer) == ["Drawer 2", "Drawer 1", "Drawer 3"]
        client.post(self.url(freezer), edit_data(freezer, action=f"delete:{second.pk}"))
        assert names(freezer) == ["Drawer 1", "Drawer 3"]

    def test_deleting_the_last_drawer_is_refused(self, client):
        freezer = drawers(count=1)
        only = freezer.compartments.get()
        response = client.post(
            self.url(freezer), edit_data(freezer, action=f"delete:{only.pk}"), follow=True
        )
        assertContains(response, "A freezer needs at least one drawer.")
        assert freezer.compartments.count() == 1

    def test_unknown_action_is_404(self, client):
        freezer = drawers()
        response = client.post(self.url(freezer), edit_data(freezer, action="explode"))
        assert response.status_code == 404

    def test_cannot_edit_another_freezers_drawer(self, client):
        kitchen, garage = drawers("Kitchen", count=1), drawers("Garage", count=1)
        data = edit_data(kitchen, **{"compartments-0-id": garage.compartments.get().pk})
        data["compartments-0-name"] = "Hijacked"
        client.post(self.url(kitchen), data)
        assert names(garage) == ["Drawer 1"]
        assert names(kitchen) == ["Drawer 1"]

    def test_extra_forms_cannot_create_drawers(self, client):
        freezer = drawers(count=1)
        data = edit_data(freezer, **{"compartments-TOTAL_FORMS": 2})
        data |= {"compartments-1-name": "Sneaky", "compartments-1-capacity": 5}
        client.post(self.url(freezer), data)
        assert names(freezer) == ["Drawer 1"]

    def test_toploader_edit_changes_capacity_only(self, client):
        freezer = toploader(capacity=20)
        data = edit_data(freezer, **{"compartments-0-capacity": 35})
        assertRedirects(client.post(self.url(freezer), data), freezer.get_absolute_url())
        assert freezer.compartments.get().capacity == 35
        assertNotContains(client.get(self.url(freezer)), "Add a drawer")


class TestDelete:
    def test_get_asks_for_confirmation_and_post_removes(self, client):
        freezer = drawers()
        url = reverse("inventory:freezer_delete", args=[freezer.pk])
        assertContains(client.get(url), "You can't undo this.")
        assert Freezer.objects.exists()
        assertRedirects(
            client.post(url), reverse("inventory:freezer_list"), fetch_redirect_response=False
        )
        assert not Freezer.objects.exists()
        assert not Compartment.objects.exists()


class TestAccessibility:
    def test_hints_and_errors_are_linked_to_their_fields(self, client):
        response = client.post(
            reverse("inventory:freezer_create"), {"name": "", "kind": "toploader", "capacity": 0}
        )
        content = response.content.decode()
        assert 'aria-describedby="id_capacity_helptext id_capacity_error"' in content
        for target in ("id_capacity_helptext", "id_capacity_error", "id_name_error"):
            assert f'id="{target}"' in content
        assert content.count('aria-invalid="true"') == 2

    def test_drawer_errors_on_the_edit_page_are_linked(self, client):
        freezer = drawers(count=2)
        data = edit_data(freezer, **{"compartments-1-name": "drawer 1"})
        content = client.post(reverse("inventory:freezer_edit", args=[freezer.pk]), data)
        assert 'id="id_compartments-1-name_error"' in content.content.decode()
