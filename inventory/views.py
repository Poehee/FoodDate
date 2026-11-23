import logging
import re

from django.contrib import messages
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_safe

from . import services
from .forms import (
    DRAWER_NAME_TAKEN,
    NAME_TAKEN,
    AddDrawerForm,
    DrawerFormSet,
    FreezerCreateForm,
    FreezerNameForm,
    ToploaderFormSet,
)
from .models import Freezer
from .selectors import freezer_overview

logger = logging.getLogger(__name__)
ACTION = re.compile(r"(up|down|delete):(\d+)")


@require_safe
def home(request: HttpRequest) -> HttpResponse:
    return render(request, "inventory/home.html", {"has_freezers": Freezer.objects.exists()})


@require_safe
@never_cache
def health(request: HttpRequest) -> JsonResponse:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except DatabaseError:
        logger.exception("Health check could not reach the database")
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})


@require_safe
def freezer_list(request: HttpRequest) -> HttpResponse:
    first = Freezer.objects.first()
    if first:
        return redirect(first)
    return render(request, "inventory/freezer_empty.html")


@require_safe
def freezer_detail(request: HttpRequest, pk: int) -> HttpResponse:
    freezer = get_object_or_404(Freezer, pk=pk)
    context = {"freezer": freezer, "freezers": Freezer.objects.all(), **freezer_overview(freezer)}
    return render(request, "inventory/freezer_detail.html", context)


@require_http_methods(["GET", "POST"])
def freezer_create(request: HttpRequest) -> HttpResponse:
    form = FreezerCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        drawers = data["kind"] == Freezer.Kind.DRAWERS
        try:
            freezer = services.create_freezer(
                name=data["name"],
                kind=data["kind"],
                capacity=None if drawers else data["capacity"],
                drawer_count=data["drawer_count"] if drawers else None,
                drawer_capacity=data["drawer_capacity"] if drawers else None,
            )
        except IntegrityError:
            form.add_error("name", NAME_TAKEN)
        else:
            messages.success(request, f"{freezer.name} added")
            return redirect(freezer)
    return render(request, "inventory/freezer_create.html", {"form": form})


def _edit_forms(request: HttpRequest, freezer: Freezer, action: str):
    data = request.POST if request.method == "POST" else None
    formset_class = DrawerFormSet if freezer.has_drawers else ToploaderFormSet
    return (
        FreezerNameForm(data, instance=freezer, prefix="freezer"),
        formset_class(data, queryset=freezer.compartments.all(), prefix="compartments"),
        AddDrawerForm(data if action == "add" else None, prefix="new"),
    )


def _apply(request: HttpRequest, freezer: Freezer, action: str, add_form) -> str:
    """Apply the button that was pressed and return where to go next."""
    edit_url = reverse("inventory:freezer_edit", args=[freezer.pk])
    if action == "save":
        messages.success(request, "Changes saved")
        return freezer.get_absolute_url()
    if action == "add":
        drawer = services.add_drawer(freezer, **add_form.cleaned_data)
        messages.success(request, f"{drawer.name} added")
        return f"{edit_url}#compartment-{drawer.pk}"
    verb, compartment_id = ACTION.fullmatch(action).groups()
    if verb == "delete":
        services.delete_drawer(freezer, int(compartment_id))
        messages.success(request, "Drawer removed")
        return f"{edit_url}#drawers"
    services.move_drawer(freezer, int(compartment_id), up=verb == "up")
    return f"{edit_url}#compartment-{compartment_id}"


@require_http_methods(["GET", "POST"])
def freezer_edit(request: HttpRequest, pk: int) -> HttpResponse:
    freezer = get_object_or_404(Freezer, pk=pk)
    action = request.POST.get("action", "save")
    if action not in ("save", "add") and not ACTION.fullmatch(action):
        raise Http404("Unknown action")
    name_form, formset, add_form = _edit_forms(request, freezer, action)

    if request.method == "POST":
        valid = name_form.is_valid() and formset.is_valid()
        if action == "add" and add_form.is_valid() and freezer.has_drawers:
            if add_form.cleaned_data["name"].casefold() in formset.names():
                add_form.add_error("name", DRAWER_NAME_TAKEN)
        valid = valid and (action != "add" or add_form.is_valid())
        if valid:
            try:
                with transaction.atomic():
                    name_form.save()
                    formset.save()
                    target = _apply(request, freezer, action, add_form)
            except IntegrityError:
                messages.error(request, "Someone else changed this freezer. Check and try again.")
                return redirect("inventory:freezer_edit", pk=freezer.pk)
            except services.RuleError as error:
                messages.error(request, str(error))
                return redirect("inventory:freezer_edit", pk=freezer.pk)
            return redirect(target)

    context = {
        "freezer": freezer,
        "name_form": name_form,
        "formset": formset,
        "add_form": add_form,
        **freezer_overview(freezer),
    }
    return render(request, "inventory/freezer_edit.html", context)


@require_http_methods(["GET", "POST"])
def freezer_delete(request: HttpRequest, pk: int) -> HttpResponse:
    freezer = get_object_or_404(Freezer, pk=pk)
    if request.method == "POST":
        freezer.delete()
        messages.success(request, f"{freezer.name} removed")
        return redirect("inventory:freezer_list")
    return render(request, "inventory/freezer_delete.html", {"freezer": freezer})
