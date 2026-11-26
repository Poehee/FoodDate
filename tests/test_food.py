import threading
from datetime import date, timedelta

import pytest
from django.core import signing
from django.db import connection
from django.urls import reverse
from django.utils import timezone
from pytest_django.asserts import assertContains, assertNotContains, assertRedirects

from inventory import services
from inventory.models import Compartment, FoodItem, FoodType, Freezer, Settings
from inventory.selectors import expiry, location_label

pytestmark = pytest.mark.django_db
TODAY = timezone.localdate


def freezer(name="Kitchen", count=2, capacity=10):
    return services.create_freezer(
        name=name, kind="drawers", drawer_count=count, drawer_capacity=capacity
    )


def drawer(f, n=1):
    return f.compartments.get(name=f"Drawer {n}")


def food_type(name="Curry"):
    # Transactional tests empty the database, including the starter types
    return FoodType.objects.get_or_create(name=name)[0]


def add(compartment, portions=1, containers=1, content="Chicken tikka", frozen_on=None, **kw):
    return services.add_food(
        content=content,
        food_type=kw.get("food_type") or food_type(),
        compartment=compartment,
        frozen_on=frozen_on or TODAY(),
        portions=portions,
        containers=containers,
    )


class TestExpiry:
    @pytest.mark.parametrize(
        ("days", "hue", "icon", "text"),
        [
            (40, 145, "check", "Use by"),
            (28, 145, "check", "Use by"),
            (14, 85, "clock", "14 days left"),
            (7, 55, "clock", "7 days left"),
            (1, 29, "clock", "1 day left"),
            (0, 25, "alert", "Expires today"),
            (-1, 25, "alert", "Expired 1 day ago"),
            (-3, 25, "alert", "Expired 3 days ago"),
        ],
    )
    def test_colors_and_text_with_a_four_week_window(self, days, hue, icon, text):
        today = date(2026, 11, 23)
        state = expiry(today + timedelta(days=days), 4, today=today)
        assert (state.hue, state.icon) == (hue, icon)
        assert state.text.startswith(text)

    def test_use_by_date_is_day_month_year(self):
        assert expiry(date(2027, 2, 14), 4, today=date(2026, 11, 23)).text == "Use by 14-02-2027"

    def test_a_longer_window_turns_food_yellow_sooner(self):
        today = date(2026, 11, 23)
        assert expiry(today + timedelta(days=28), 8, today=today).hue == 85


class TestLocation:
    @pytest.mark.parametrize(
        ("kind", "freezers", "expected"),
        [
            ("drawers", 1, "Drawer 1"),
            ("toploader", 1, ""),
            ("drawers", 2, "Kitchen / Drawer 1"),
            ("toploader", 2, "Kitchen"),
        ],
    )
    def test_freezer_name_only_with_more_than_one_freezer(self, kind, freezers, expected):
        f = services.create_freezer(
            name="Kitchen", kind=kind, capacity=5, drawer_count=1, drawer_capacity=5
        )
        assert location_label(f.compartments.get(), freezers) == expected


class TestAddFood:
    def test_shelf_life_comes_from_the_default_or_the_type(self):
        f = freezer()
        settings = Settings.load()
        assert add(drawer(f))[0].weeks == settings.default_weeks == 12
        pasta = food_type("Pasta")
        pasta.weeks = 6
        pasta.save()
        assert add(drawer(f), food_type=pasta)[0].weeks == 6

    def test_expiration_date_is_calculated_by_the_database(self):
        f = freezer()
        item = add(drawer(f), frozen_on=date(2024, 1, 1))[0]
        item.refresh_from_db()
        assert item.expires_on == date(2024, 3, 25)

    def test_later_setting_changes_do_not_move_frozen_food(self):
        item = add(drawer(freezer()))[0]
        Settings.objects.update(default_weeks=20)
        item.refresh_from_db()
        assert item.weeks == 12

    def test_identical_containers_all_fit_or_none_are_added(self):
        f = freezer(capacity=10)
        items = add(drawer(f), portions=2, containers=3)
        assert [i.portions for i in items] == [2, 2, 2]
        assert len({i.pk for i in items}) == 3
        with pytest.raises(services.RuleError, match="Only 4 portions free in Drawer 1"):
            add(drawer(f), portions=2, containers=3)
        assert FoodItem.objects.count() == 3

    def test_exact_fill_is_accepted(self):
        add(drawer(freezer(capacity=10)), portions=10)
        assert FoodItem.objects.get().portions == 10

    def test_future_freeze_date_is_refused(self):
        with pytest.raises(services.RuleError, match="future"):
            add(drawer(freezer()), frozen_on=TODAY() + timedelta(days=1))


class TestUpdateFood:
    def test_move_to_a_drawer_with_room(self):
        f = freezer()
        item = add(drawer(f, 1), portions=3)[0]
        moved = services.update_food(item, compartment=drawer(f, 2))
        assert moved.compartment == drawer(f, 2)

    def test_move_to_a_full_drawer_is_refused(self):
        f = freezer(capacity=4)
        add(drawer(f, 2), portions=3)
        item = add(drawer(f, 1), portions=2)[0]
        with pytest.raises(services.RuleError, match="Only 1 portion free in Drawer 2"):
            services.update_food(item, compartment=drawer(f, 2))

    def test_own_portions_are_not_counted_twice(self):
        item = add(drawer(freezer(capacity=5)), portions=5)[0]
        assert services.update_food(item, portions=5, content="Renamed").content == "Renamed"

    def test_changing_type_refreshes_weeks_and_date_moves_expiration(self):
        item = add(drawer(freezer()), frozen_on=date(2024, 1, 1))[0]
        soup = food_type("Soup")
        soup.weeks = 4
        soup.save()
        item = services.update_food(item, food_type=soup)
        assert (item.weeks, item.expires_on) == (4, date(2024, 1, 29))
        item = services.update_food(item, frozen_on=date(2024, 2, 1))
        assert item.expires_on == date(2024, 2, 29)


class TestTakeAndUndo:
    def test_take_one_portion(self):
        item = add(drawer(freezer()), portions=3)[0]
        result = services.take_portion(item.pk, expected_portions=3)
        assert result.item.portions == 2

    def test_last_portion_deletes_the_container(self):
        item = add(drawer(freezer()))[0]
        assert services.take_portion(item.pk, expected_portions=1).item is None
        assert not FoodItem.objects.exists()

    def test_outdated_page_cannot_take_twice(self):
        item = add(drawer(freezer()), portions=3)[0]
        services.take_portion(item.pk, expected_portions=3)
        with pytest.raises(services.StaleError):
            services.take_portion(item.pk, expected_portions=3)
        item.refresh_from_db()
        assert item.portions == 2

    def test_undo_puts_a_portion_back(self):
        item = add(drawer(freezer()), portions=3)[0]
        token = services.take_portion(item.pk, expected_portions=3).undo_token
        assert services.undo_take(token).portions == 3

    def test_undo_restores_a_deleted_container_with_its_number(self):
        item = add(drawer(freezer()), frozen_on=date(2024, 1, 1))[0]
        token = services.take_portion(item.pk, expected_portions=1).undo_token
        restored = services.undo_take(token)
        restored.refresh_from_db()
        assert (restored.pk, restored.content, restored.expires_on) == (
            item.pk,
            "Chicken tikka",
            date(2024, 3, 25),
        )

    def test_undo_only_works_once(self):
        item = add(drawer(freezer()), portions=2)[0]
        token = services.take_portion(item.pk, expected_portions=2).undo_token
        services.undo_take(token)
        with pytest.raises(services.RuleError):
            services.undo_take(token)

    def test_undo_is_refused_when_the_space_was_filled(self):
        f = freezer(capacity=2)
        item = add(drawer(f), portions=1)[0]
        token = services.take_portion(item.pk, expected_portions=1).undo_token
        add(drawer(f), portions=2)
        with pytest.raises(services.RuleError, match="Only 0 portions free"):
            services.undo_take(token)

    def test_tampered_token_is_refused(self):
        item = add(drawer(freezer()), portions=2)[0]
        forged = signing.dumps({"before": {"id": item.pk}, "after": 99}, salt="other")
        with pytest.raises(services.RuleError, match="too late"):
            services.undo_take(forged)


class TestProtection:
    def test_capacity_cannot_go_below_portions_in_it(self, client):
        f = freezer(count=1, capacity=10)
        add(drawer(f), portions=6)
        c = drawer(f)
        data = {
            "action": "save",
            "freezer-name": f.name,
            "compartments-TOTAL_FORMS": 1,
            "compartments-INITIAL_FORMS": 1,
            "compartments-0-id": c.pk,
            "compartments-0-name": c.name,
            "compartments-0-capacity": 5,
        }
        response = client.post(reverse("inventory:freezer_edit", args=[f.pk]), data)
        assertContains(response, "Holds 6 portions now")
        c.refresh_from_db()
        assert c.capacity == 10

    def test_drawer_with_food_cannot_be_removed(self):
        f = freezer()
        add(drawer(f, 2))
        with pytest.raises(services.RuleError, match="Drawer 2"):
            services.delete_drawer(f, drawer(f, 2).pk)

    def test_freezer_with_food_cannot_be_removed(self, client):
        f = freezer()
        add(drawer(f), portions=2)
        url = reverse("inventory:freezer_delete", args=[f.pk])
        assertContains(client.get(url), "still holds 2 portions")
        client.post(url, follow=True)
        assert Freezer.objects.filter(pk=f.pk).exists()

    def test_food_type_in_use_cannot_be_removed(self):
        add(drawer(freezer()))
        with pytest.raises(services.RuleError, match="Curry is used"):
            services.delete_food_type(food_type().pk)
        services.delete_food_type(food_type("Rice").pk)
        assert not FoodType.objects.filter(name="Rice").exists()


class TestPages:
    def test_add_from_the_drawer_panel(self, client):
        f = freezer()
        c = drawer(f, 2)
        response = client.post(
            reverse("inventory:item_create"),
            {
                "content": "Pea soup",
                "food_type": food_type("Soup").pk,
                "frozen_on": TODAY().isoformat(),
                "portions": 2,
                "containers": 1,
                "compartment": c.pk,
            },
        )
        item = FoodItem.objects.get()
        assertRedirects(
            response, f"{f.get_absolute_url()}?drawer={c.pk}#drawer", fetch_redirect_response=False
        )
        page = client.get(f"{f.get_absolute_url()}?drawer={c.pk}")
        assertContains(page, f"#{item.pk}</span> Pea soup")
        assertContains(page, "is-new")
        assertContains(page, f"write #{item.pk} on the lid")

    def test_add_that_does_not_fit_shows_why(self, client):
        f = freezer(capacity=2)
        response = client.post(
            reverse("inventory:item_create"),
            {
                "content": "Lasagne",
                "food_type": food_type().pk,
                "frozen_on": TODAY().isoformat(),
                "portions": 3,
                "containers": 1,
                "compartment": drawer(f).pk,
            },
        )
        assertContains(response, "Only 2 portions free in Drawer 1.")
        assert not FoodItem.objects.exists()

    def test_freezer_page_colors_cells_and_lists_soonest_first(self, client):
        f = freezer(count=1)
        late = add(drawer(f), content="Late", frozen_on=TODAY())[0]
        early = add(drawer(f), content="Early", frozen_on=TODAY() - timedelta(weeks=12))[0]
        page = client.get(f.get_absolute_url()).content.decode()
        assert page.index(f"#{early.pk}</span> Early") < page.index(f"#{late.pk}</span> Late")
        assert 'class="cell is-used" style="--h: 25"' in page
        assert "Expires today" in page
        assert 'class="cell is-used" style="--h: 145"' in page

    def test_expired_portions_are_named_in_the_drawer(self, client):
        f = freezer(count=1)
        add(drawer(f), portions=2, frozen_on=TODAY() - timedelta(weeks=13))
        assertContains(client.get(f.get_absolute_url()), "2 expired portions")

    def test_take_with_json_returns_new_state_and_undo(self, client):
        f = freezer(count=1, capacity=10)
        item = add(drawer(f), portions=3)[0]
        response = client.post(
            reverse("inventory:item_take", args=[item.pk]),
            {"portions": 3},
            HTTP_ACCEPT="application/json",
        )
        data = response.json()
        assert data["item"] == {"id": item.pk, "portions": 2}
        assert data["compartment"]["used"] == 2
        assert data["totals"] == {"capacity": 10, "used": 2, "free": 8}
        assert data["undo"]["text"] == "Took 1 portion of Chicken tikka, 2 portions left"

    def test_stale_take_with_json_asks_for_a_reload(self, client):
        item = add(drawer(freezer()), portions=3)[0]
        response = client.post(
            reverse("inventory:item_take", args=[item.pk]),
            {"portions": 5},
            HTTP_ACCEPT="application/json",
        )
        assert response.status_code == 409
        assert response.json()["reload"] is True

    def test_take_without_javascript_offers_undo_on_the_next_page(self, client):
        f = freezer()
        item = add(drawer(f))[0]
        response = client.post(
            reverse("inventory:item_take", args=[item.pk]), {"portions": 1}, follow=True
        )
        assertContains(response, "Took the last portion of Chicken tikka")
        assertContains(response, reverse("inventory:item_undo"))
        token = response.context["undo"]["token"]
        client.post(reverse("inventory:item_undo"), {"token": token})
        assert FoodItem.objects.filter(pk=item.pk).exists()

    def test_label_shows_number_dates_and_location(self, client):
        f = freezer()
        item = add(drawer(f, 2), frozen_on=date(2024, 1, 1))[0]
        page = client.get(item.get_absolute_url())
        assertContains(page, f"#{item.pk}")
        assertContains(page, "01-01-2024")
        assertContains(page, "25-03-2024")
        assertContains(page, "<dd>Drawer 2</dd>")
        assertNotContains(page, "Kitchen / Drawer 2")
        freezer("Garage", count=1)
        assertContains(client.get(item.get_absolute_url()), "Kitchen / Drawer 2")

    def test_edit_moves_food(self, client):
        f = freezer()
        item = add(drawer(f, 1), portions=2)[0]
        data = {
            "content": "Chicken tikka",
            "food_type": food_type().pk,
            "frozen_on": item.frozen_on.isoformat(),
            "portions": 2,
            "compartment": drawer(f, 2).pk,
        }
        response = client.post(reverse("inventory:item_edit", args=[item.pk]), data)
        assertRedirects(response, item.get_absolute_url(), fetch_redirect_response=False)
        item.refresh_from_db()
        assert item.compartment == drawer(f, 2)

    def test_settings_save_and_add_type(self, client):
        types = list(FoodType.objects.all())
        data = {
            "action": "add",
            "general-default_weeks": 10,
            "general-color_window_weeks": 3,
            "types-TOTAL_FORMS": len(types),
            "types-INITIAL_FORMS": len(types),
            "new-name": "Lasagne",
            "new-weeks": 8,
        }
        for i, t in enumerate(types):
            data |= {f"types-{i}-id": t.pk, f"types-{i}-name": t.name, f"types-{i}-weeks": ""}
        client.post(reverse("inventory:settings"), data)
        settings = Settings.load()
        assert (settings.default_weeks, settings.color_window_weeks) == (10, 3)
        assert FoodType.objects.get(name="Lasagne").weeks == 8
        data["new-name"] = "lasagne"
        assertContains(client.post(reverse("inventory:settings"), data), "already exists")

    @pytest.mark.parametrize(("field", "value"), [("color_window_weeks", 53), ("default_weeks", 0)])
    def test_settings_ranges(self, client, field, value):
        data = {
            "action": "save",
            "general-default_weeks": 12,
            "general-color_window_weeks": 4,
            "types-TOTAL_FORMS": 0,
            "types-INITIAL_FORMS": 0,
            f"general-{field}": value,
        }
        response = client.post(reverse("inventory:settings"), data)
        assert response.status_code == 200
        assert Settings.load().default_weeks == 12


@pytest.mark.django_db(transaction=True)
class TestTwoPeopleAtOnce:
    """Each thread has its own database connection, like two phones at the freezer."""

    def run_together(self, *actions):
        barrier = threading.Barrier(len(actions))
        results = [None] * len(actions)

        def run(i, action):
            try:
                barrier.wait()
                results[i] = action()
            except Exception as error:  # noqa: BLE001 - collected for the assertion
                results[i] = error
            finally:
                connection.close()

        threads = [threading.Thread(target=run, args=(i, a)) for i, a in enumerate(actions)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return results

    def test_two_adds_cannot_overfill_a_drawer(self):
        c = drawer(freezer(capacity=5))
        results = self.run_together(lambda: add(c, portions=3), lambda: add(c, portions=3))
        assert sum(isinstance(r, list) for r in results) == 1
        assert sum(isinstance(r, services.RuleError) for r in results) == 1
        assert sum(i.portions for i in FoodItem.objects.all()) == 3

    def test_two_takes_of_the_same_portion_count_once(self):
        item = add(drawer(freezer()), portions=4)[0]
        take = lambda: services.take_portion(item.pk, expected_portions=4)  # noqa: E731
        results = self.run_together(take, take)
        assert sum(isinstance(r, services.StaleError) for r in results) == 1
        item.refresh_from_db()
        assert item.portions == 3

    def test_capacity_change_and_add_cannot_both_win(self):
        f = freezer(count=1, capacity=6)
        c = drawer(f)
        add(c, portions=3)

        def shrink():
            from django.db import transaction

            with transaction.atomic():
                services.lock_freezer(f)
                Compartment.objects.filter(pk=c.pk).update(capacity=4)
                services.check_capacities(f)

        results = self.run_together(shrink, lambda: add(c, portions=3))
        c.refresh_from_db()
        used = sum(i.portions for i in FoodItem.objects.all())
        assert used <= c.capacity
        assert sum(isinstance(r, services.RuleError) for r in results) == 1, results
