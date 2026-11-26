import logging
import re

from django.contrib import messages
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST, require_safe

from . import services
from .forms import (
    DRAWER_NAME_TAKEN,
    NAME_TAKEN,
    AddDrawerForm,
    AddFoodTypeForm,
    DrawerFormSet,
    FoodEditForm,
    FoodForm,
    FoodTypeFormSet,
    FreezerCreateForm,
    FreezerNameForm,
    GeneralSettingsForm,
    ToploaderFormSet,
    food_type_name_taken,
)
from .models import Compartment, FoodItem, FoodType, Freezer, Settings
from .selectors import (
    compartment_state,
    expiry,
    freezer_overview,
    location_label,
    stock_summary,
)

logger = logging.getLogger(__name__)
ACTION = re.compile(r"(up|down|delete):(\d+)")
UNDO_SESSION_KEY = "undo"


def wants_json(request: HttpRequest) -> bool:
    return "application/json" in request.headers.get("Accept", "")


def portions_text(n: int) -> str:
    return f"{n} portion{'s' if n != 1 else ''}"


# Pages


@require_safe
def home(request: HttpRequest) -> HttpResponse:
    context = {"has_freezers": Freezer.objects.exists(), "stock": stock_summary()}
    return render(request, "inventory/home.html", context)


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
    try:
        selected_id = int(request.GET.get("drawer", ""))
    except ValueError:
        selected_id = None
    overview = freezer_overview(freezer, selected_id)
    context = {
        "freezer": freezer,
        "freezers": Freezer.objects.all(),
        "add_form": FoodForm(
            initial={"compartment": str(overview["selected"].pk)} if overview["selected"] else {}
        ),
        "just_added": set(request.session.pop("just_added", [])),
        "undo": request.session.pop(UNDO_SESSION_KEY, None),
        **overview,
    }
    return render(request, "inventory/freezer_detail.html", context)


# Freezers


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
                    services.lock_freezer(freezer)
                    name_form.save()
                    formset.save()
                    services.check_capacities(freezer)
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
    portions = services.used_by_freezer(freezer)
    if request.method == "POST":
        try:
            services.delete_freezer(freezer)
        except services.RuleError as error:
            messages.error(request, str(error))
            return redirect("inventory:freezer_delete", pk=freezer.pk)
        messages.success(request, f"{freezer.name} removed")
        return redirect("inventory:freezer_list")
    return render(
        request, "inventory/freezer_delete.html", {"freezer": freezer, "portions": portions}
    )


# Food


def _back_to(compartment: Compartment) -> str:
    return f"{compartment.freezer.get_absolute_url()}?drawer={compartment.pk}#drawer"


@require_http_methods(["GET", "POST"])
def item_create(request: HttpRequest) -> HttpResponse:
    initial = {}
    if request.method == "GET" and request.GET.get("drawer", "").isdigit():
        initial["compartment"] = request.GET["drawer"]
    form = FoodForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            items = services.add_food(**form.cleaned_data)
        except services.RuleError as error:
            form.add_error(None, str(error))
        else:
            first = items[0]
            numbers = ", ".join(f"#{i.pk}" for i in items)
            messages.success(request, f"Added {first.content}: write {numbers} on the lid")
            request.session["just_added"] = [i.pk for i in items]
            return redirect(_back_to(first.compartment))
    if not Compartment.objects.exists():
        return redirect("inventory:freezer_list")
    return render(request, "inventory/item_form.html", {"form": form, "creating": True})


def _item_context(item: FoodItem) -> dict:
    return {
        "item": item,
        "expiry": expiry(item.expires_on, Settings.load().color_window_weeks),
        "location": location_label(item.compartment, Freezer.objects.count()),
    }


@require_safe
def item_detail(request: HttpRequest, pk: int) -> HttpResponse:
    item = get_object_or_404(
        FoodItem.objects.select_related("food_type", "compartment__freezer"), pk=pk
    )
    return render(request, "inventory/item_detail.html", _item_context(item))


@require_http_methods(["GET", "POST"])
def item_edit(request: HttpRequest, pk: int) -> HttpResponse:
    item = get_object_or_404(FoodItem.objects.select_related("compartment__freezer"), pk=pk)
    form = FoodEditForm(request.POST or None, item=item)
    if request.method == "POST" and form.is_valid():
        try:
            item = services.update_food(item, **form.cleaned_data)
        except services.RuleError as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, f"#{item.pk} saved")
            return redirect(item)
    return render(request, "inventory/item_form.html", {"form": form, "item": item})


@require_POST
def item_take(request: HttpRequest, pk: int) -> HttpResponse:
    item = FoodItem.objects.select_related("compartment__freezer").filter(pk=pk).first()
    try:
        expected = int(request.POST.get("portions", ""))
    except ValueError as error:
        raise Http404("Missing portions") from error
    try:
        result = services.take_portion(pk, expected_portions=expected)
    except services.StaleError as error:
        if wants_json(request):
            return JsonResponse({"error": str(error), "reload": True}, status=409)
        messages.error(request, str(error))
        return redirect(_back_to(item.compartment) if item else reverse("inventory:home"))

    content = item.content
    text = (
        f"Took 1 portion of {content}, {portions_text(result.item.portions)} left"
        if result.item
        else f"Took the last portion of {content}"
    )
    if wants_json(request):
        return JsonResponse(
            {
                "item": {"id": pk, "portions": result.item.portions if result.item else 0},
                "portions_text": portions_text(result.item.portions) if result.item else "",
                "undo": {"token": result.undo_token, "text": text},
                **compartment_state(result.compartment),
            }
        )
    request.session[UNDO_SESSION_KEY] = {"token": result.undo_token, "text": text}
    return redirect(_back_to(result.compartment))


@require_POST
def item_undo(request: HttpRequest) -> HttpResponse:
    try:
        item = services.undo_take(request.POST.get("token", ""))
    except services.RuleError as error:
        if wants_json(request):
            return JsonResponse({"error": str(error)}, status=409)
        messages.error(request, str(error))
        return redirect("inventory:freezer_list")
    compartment = Compartment.objects.select_related("freezer").get(pk=item.compartment_id)
    if wants_json(request):
        return JsonResponse({"restored": item.pk, **compartment_state(compartment)})
    messages.success(request, f"Put back 1 portion of {item.content}")
    return redirect(_back_to(compartment))


# Settings


def _settings_forms(request: HttpRequest, action: str):
    data = request.POST if request.method == "POST" else None
    settings = Settings.load()
    default = {"default_weeks": settings.default_weeks}
    return (
        GeneralSettingsForm(data, instance=settings, prefix="general"),
        FoodTypeFormSet(data, queryset=FoodType.objects.all(), prefix="types", form_kwargs=default),
        AddFoodTypeForm(data if action == "add" else None, prefix="new", **default),
    )


@require_http_methods(["GET", "POST"])
def settings_view(request: HttpRequest) -> HttpResponse:
    action = request.POST.get("action", "save")
    delete = re.fullmatch(r"delete:(\d+)", action)
    if action not in ("save", "add") and not delete:
        raise Http404("Unknown action")
    general, types, add_form = _settings_forms(request, action)

    if request.method == "POST":
        valid = general.is_valid() and types.is_valid()
        if action == "add" and add_form.is_valid():
            name = add_form.cleaned_data["name"]
            if name.casefold() in types.names() or food_type_name_taken(name):
                add_form.add_error("name", "This food type already exists.")
        if valid and (action != "add" or add_form.is_valid()):
            try:
                with transaction.atomic():
                    general.save()
                    types.save()
                    target = reverse("inventory:settings")
                    if action == "add":
                        food_type = FoodType.objects.create(**add_form.cleaned_data)
                        messages.success(request, f"{food_type.name} added")
                        target += "#types"
                    elif delete:
                        services.delete_food_type(int(delete.group(1)))
                        messages.success(request, "Food type removed")
                        target += "#types"
                    else:
                        messages.success(request, "Settings saved")
            except IntegrityError:
                messages.error(request, "Someone else changed this. Check and try again.")
                return redirect("inventory:settings")
            except services.RuleError as error:
                messages.error(request, str(error))
                return redirect(reverse("inventory:settings") + "#types")
            return redirect(target)

    in_use = services.food_types_in_use()
    return render(
        request,
        "inventory/settings.html",
        {"general": general, "types": types, "add_form": add_form, "in_use": in_use},
    )
