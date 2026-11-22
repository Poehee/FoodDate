from datetime import date

import pytest
from django.db import OperationalError, connection
from django.urls import reverse
from django.utils import formats
from pytest_django.asserts import assertContains, assertTemplateUsed


def test_home_renders_responsive_layout(client):
    response = client.get(reverse("inventory:home"))

    assertTemplateUsed(response, "inventory/base.html")
    assertContains(response, "width=device-width")
    assertContains(response, "inventory/app.css")


def test_home_rejects_writes(client):
    assert client.post(reverse("inventory:home")).status_code == 405


@pytest.mark.django_db
def test_health_reports_ok(client):
    response = client.get(reverse("inventory:health"))

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_hides_database_error(client, monkeypatch):
    def fail():
        raise OperationalError("internal detail")

    monkeypatch.setattr(connection, "cursor", fail)
    response = client.get(reverse("inventory:health"))

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


@pytest.mark.django_db
def test_database_is_postgresql_17():
    assert connection.vendor == "postgresql"
    with connection.cursor():
        assert connection.pg_version // 10000 == 17


def test_dates_display_as_day_month_year():
    assert formats.date_format(date(2027, 2, 14)) == "14-02-2027"
