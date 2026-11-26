from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone
from pytest_django.asserts import assertContains, assertNotContains, assertRedirects

from inventory import services
from inventory.models import FoodItem, FoodType
from inventory.selectors import filter_stock

pytestmark = pytest.mark.django_db
TODAY = timezone.localdate()  # the page filters relative to the real date


@pytest.fixture
def drawer():
    freezer = services.create_freezer(
        name="Kitchen", kind="drawers", drawer_count=1, drawer_capacity=99
    )
    return freezer.compartments.get()


def food_type(name):
    return FoodType.objects.get_or_create(name=name)[0]


def expiring(drawer, days, content=None, type_name="Curry", portions=1):
    """A container that expires `days` after TODAY (negative: before)."""
    item = FoodItem.objects.create(
        content=content or f"{days:+d} days",
        food_type=food_type(type_name),
        compartment=drawer,
        frozen_on=TODAY + timedelta(days=days) - timedelta(weeks=1),
        weeks=1,
        portions=portions,
    )
    item.refresh_from_db()
    assert item.expires_on == TODAY + timedelta(days=days)
    return item


def contents(**filters):
    return [i.content for i in filter_stock(today=TODAY, **filters)]


class TestExpirationBoundaries:
    @pytest.fixture(autouse=True)
    def stock(self, drawer):
        for days in (-8, -1, 0, 1, 7, 8, 14, 15, 30, 31):
            expiring(drawer, days)

    def test_no_filter_lists_everything_soonest_first(self):
        assert contents() == [
            "-8 days", "-1 days", "+0 days", "+1 days", "+7 days",
            "+8 days", "+14 days", "+15 days", "+30 days", "+31 days",
        ]  # fmt: skip

    def test_expired_means_before_today(self):
        assert contents(expires="expired") == ["-8 days", "-1 days"]

    def test_within_7_days_includes_today_and_day_7(self):
        assert contents(expires="7") == ["+0 days", "+1 days", "+7 days"]

    def test_within_14_days(self):
        assert contents(expires="14") == ["+0 days", "+1 days", "+7 days", "+8 days", "+14 days"]

    def test_within_30_days_stops_at_day_30(self):
        result = contents(expires="30")
        assert result[0] == "+0 days" and result[-1] == "+30 days"
        assert "+31 days" not in result and "-1 days" not in result

    def test_date_range_includes_both_ends(self):
        assert contents(
            expires="range",
            expires_from=TODAY + timedelta(days=1),
            expires_to=TODAY + timedelta(days=8),
        ) == ["+1 days", "+7 days", "+8 days"]

    def test_date_range_with_one_end_open(self):
        assert contents(expires="range", expires_to=TODAY - timedelta(days=1)) == [
            "-8 days",
            "-1 days",
        ]
        assert contents(expires="range", expires_from=TODAY + timedelta(days=30)) == [
            "+30 days",
            "+31 days",
        ]


class TestTypes:
    def test_filter_by_one_or_more_types_and_combine_with_expiration(self, drawer):
        expiring(drawer, 3, "Tikka", "Curry")
        expiring(drawer, 20, "Korma", "Curry")
        expiring(drawer, 5, "Bolognese", "Pasta")
        expiring(drawer, 4, "Pea soup", "Soup")
        curry, pasta = food_type("Curry").pk, food_type("Pasta").pk
        assert contents(types=[curry]) == ["Tikka", "Korma"]
        assert contents(types=[curry, pasta]) == ["Tikka", "Bolognese", "Korma"]
        assert contents(types=[curry, pasta], expires="7") == ["Tikka", "Bolognese"]


class TestPage:
    url = reverse("inventory:home")

    def test_only_types_in_stock_are_offered_with_counts(self, client, drawer):
        expiring(drawer, 3, "Tikka", "Curry", portions=2)
        expiring(drawer, 9, "Korma", "Curry")
        page = client.get(self.url)
        assertContains(page, "Curry (2)")
        assertNotContains(page, "Burrito")
        assertContains(page, "3 portions in 2 containers")

    def test_filters_combine_through_the_address(self, client, drawer):
        tikka = expiring(drawer, 3, "Tikka", "Curry")
        expiring(drawer, 20, "Korma", "Curry")
        expiring(drawer, 2, "Bolognese", "Pasta")
        page = client.get(self.url, {"type": food_type("Curry").pk, "expires": "30"})
        content = page.content.decode()
        assert "Tikka" in content and "Korma" in content and "Bolognese" not in content
        assertContains(page, "2 of 3 containers match")
        page = client.get(self.url, {"type": food_type("Curry").pk, "expires": "7"})
        assertContains(page, f"#{tikka.pk}</span> Tikka")
        assertContains(page, "1 of 3 containers matches")

    def test_no_match_offers_to_clear_filters(self, client, drawer):
        expiring(drawer, 40)
        page = client.get(self.url, {"expires": "expired"})
        assertContains(page, "No food matches these filters.")
        assertContains(page, "Clear filters")

    def test_range_without_dates_is_explained(self, client, drawer):
        expiring(drawer, 3)
        assertContains(client.get(self.url, {"expires": "range"}), "Choose a from or to date.")

    def test_reversed_range_is_explained(self, client, drawer):
        expiring(drawer, 3)
        page = client.get(
            self.url,
            {
                "expires": "range",
                "expires_from": (TODAY + timedelta(days=10)).isoformat(),
                "expires_to": (TODAY + timedelta(days=1)).isoformat(),
            },
        )
        assertContains(page, "Choose a date on or after the from date.")

    def test_unknown_type_does_not_break_the_page(self, client, drawer):
        expiring(drawer, 3, "Tikka")
        page = client.get(self.url, {"type": "99999"})
        assert page.status_code == 200
        assertContains(page, "Tikka")

    def test_location_shows_on_each_row(self, client, drawer):
        expiring(drawer, 3, "Tikka")
        assertContains(client.get(self.url), "1 portion</span> · Drawer 1")

    def test_take_from_the_stock_list_returns_to_it(self, client, drawer):
        item = expiring(drawer, 3, "Tikka", portions=2)
        back = f"{self.url}?expires=7"
        response = client.post(
            reverse("inventory:item_take", args=[item.pk]), {"portions": 2, "next": back}
        )
        assertRedirects(response, back, fetch_redirect_response=False)

    def test_take_ignores_a_next_address_on_another_site(self, client, drawer):
        item = expiring(drawer, 3, "Tikka", portions=2)
        response = client.post(
            reverse("inventory:item_take", args=[item.pk]),
            {"portions": 2, "next": "https://example.com/"},
        )
        assert "example.com" not in response.url
