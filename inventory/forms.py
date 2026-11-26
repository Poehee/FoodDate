from django import forms
from django.utils import timezone

from . import services
from .models import (
    MAX_CAPACITY,
    MAX_NAME_LENGTH,
    MAX_WEEKS,
    Compartment,
    FoodItem,
    FoodType,
    Freezer,
    Settings,
)

NAME_TAKEN = "You already have a freezer with this name."
DRAWER_NAME_TAKEN = "This freezer already has a drawer with this name."
MAX_DRAWERS = 12


def capacity_field(initial: int | None = None, required: bool = True) -> forms.IntegerField:
    return forms.IntegerField(
        label="Capacity",
        min_value=1,
        max_value=MAX_CAPACITY,
        initial=initial,
        required=required,
        help_text="In portions",
        widget=forms.NumberInput(attrs={"inputmode": "numeric", "data-stepper": ""}),
    )


def name_field(label: str, placeholder: str) -> forms.CharField:
    return forms.CharField(
        label=label,
        max_length=MAX_NAME_LENGTH,
        strip=True,
        widget=forms.TextInput(attrs={"placeholder": placeholder, "autocomplete": "off"}),
    )


def freezer_name_taken(name: str, exclude_pk: int | None = None) -> bool:
    return Freezer.objects.filter(name__iexact=name).exclude(pk=exclude_pk).exists()


class FreezerCreateForm(forms.Form):
    name = name_field("Name", "Kitchen")
    kind = forms.ChoiceField(
        label="Type",
        choices=Freezer.Kind.choices,
        initial=Freezer.Kind.DRAWERS,
        widget=forms.RadioSelect,
    )
    drawer_count = forms.IntegerField(
        label="Drawers",
        min_value=1,
        max_value=MAX_DRAWERS,
        initial=3,
        required=False,
        widget=forms.NumberInput(attrs={"inputmode": "numeric", "data-stepper": ""}),
    )
    drawer_capacity = capacity_field(initial=10, required=False)
    capacity = capacity_field(initial=20, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["drawer_capacity"].label = "Portions per drawer"
        self.fields["drawer_capacity"].help_text = "You can change each drawer later"

    def clean_name(self) -> str:
        name = self.cleaned_data["name"]
        if freezer_name_taken(name):
            raise forms.ValidationError(NAME_TAKEN)
        return name

    def clean(self):
        cleaned = super().clean()
        required = (
            ["drawer_count", "drawer_capacity"]
            if cleaned.get("kind") == Freezer.Kind.DRAWERS
            else ["capacity"]
        )
        for field in required:
            if cleaned.get(field) is None and field not in self.errors:
                self.add_error(field, "Enter a number.")
        return cleaned


class FreezerNameForm(forms.ModelForm):
    name = name_field("Name", "Kitchen")

    class Meta:
        model = Freezer
        fields = ["name"]

    def clean_name(self) -> str:
        name = self.cleaned_data["name"]
        if freezer_name_taken(name, exclude_pk=self.instance.pk):
            raise forms.ValidationError(NAME_TAKEN)
        return name

    def validate_constraints(self):
        # clean_name reports duplicates with a clearer message
        pass


def check_capacity_not_below_used(form) -> int:
    capacity = form.cleaned_data["capacity"]
    used = services.used_portions(form.instance) if form.instance.pk else 0
    if capacity < used:
        raise forms.ValidationError(f"Holds {used} portions now, so it can't be lower.")
    return capacity


class DrawerForm(forms.ModelForm):
    name = name_field("Drawer name", "Top drawer")
    capacity = capacity_field()

    class Meta:
        model = Compartment
        fields = ["name", "capacity"]

    clean_capacity = check_capacity_not_below_used

    def validate_constraints(self):
        # Checked across the whole formset instead, see UniqueDrawerNames
        pass


class ToploaderForm(forms.ModelForm):
    capacity = capacity_field()

    class Meta:
        model = Compartment
        fields = ["capacity"]

    clean_capacity = check_capacity_not_below_used

    def validate_constraints(self):
        pass


class UniqueDrawerNames(forms.BaseModelFormSet):
    def clean(self):
        super().clean()
        seen = set()
        for form in self.forms:
            name = form.cleaned_data.get("name")
            if not name:
                continue
            if name.casefold() in seen:
                form.add_error("name", DRAWER_NAME_TAKEN)
            seen.add(name.casefold())

    def names(self) -> set[str]:
        return {f.cleaned_data["name"].casefold() for f in self.forms if f.cleaned_data.get("name")}


DrawerFormSet = forms.modelformset_factory(
    Compartment, form=DrawerForm, formset=UniqueDrawerNames, extra=0, edit_only=True
)
ToploaderFormSet = forms.modelformset_factory(
    Compartment, form=ToploaderForm, extra=0, edit_only=True
)


class AddDrawerForm(forms.Form):
    name = name_field("New drawer", "Bottom drawer")
    capacity = capacity_field(initial=10)


# Food


class CompartmentChoiceField(forms.ChoiceField):
    """Locations grouped by freezer, each with its free portions."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        compartments = list(
            Compartment.objects.select_related("freezer").order_by(
                "freezer__name", "position", "pk"
            )
        )
        used = services.used_by_compartment()
        freezers = {c.freezer_id for c in compartments}
        groups: dict[str, list] = {}
        for c in compartments:
            free = c.capacity - used.get(c.pk, 0)
            label = c.name or c.freezer.name
            groups.setdefault(c.freezer.name, []).append((str(c.pk), f"{label} · {free} free"))
        if len(freezers) > 1:
            self.choices = list(groups.items())
        else:
            self.choices = [choice for choices in groups.values() for choice in choices]
        self._compartments = {str(c.pk): c for c in compartments}

    def clean(self, value):
        value = super().clean(value)
        return self._compartments[value]


def weeks_field(label: str, required: bool, help_text: str, max_value: int = MAX_WEEKS):
    return forms.IntegerField(
        label=label,
        min_value=1,
        max_value=max_value,
        required=required,
        help_text=help_text,
        widget=forms.NumberInput(attrs={"inputmode": "numeric", "data-stepper": ""}),
    )


class FoodTypeChoiceField(forms.ModelChoiceField):
    def __init__(self, **kwargs):
        super().__init__(queryset=FoodType.objects.all(), label="Type", empty_label=None, **kwargs)
        # The page uses these to preview the expiration date
        self.default_weeks = Settings.load().default_weeks
        self.weeks = {str(t.pk): t.weeks or self.default_weeks for t in self.queryset}


class FoodForm(forms.Form):
    content = forms.CharField(
        label="What is it",
        max_length=80,
        strip=True,
        widget=forms.TextInput(attrs={"placeholder": "Chicken tikka", "autocomplete": "off"}),
    )
    food_type = None  # set in __init__, so new types show up without a restart
    frozen_on = forms.DateField(
        label="Frozen on",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )
    portions = forms.IntegerField(
        label="Portions per container",
        min_value=1,
        max_value=MAX_CAPACITY,
        initial=1,
        widget=forms.NumberInput(attrs={"inputmode": "numeric", "data-stepper": ""}),
    )
    containers = forms.IntegerField(
        label="Containers",
        min_value=1,
        max_value=50,
        initial=1,
        help_text="Identical containers get a number each",
        widget=forms.NumberInput(attrs={"inputmode": "numeric", "data-stepper": ""}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["food_type"] = FoodTypeChoiceField()
        self.fields["compartment"] = CompartmentChoiceField(label="Location")
        self.fields["frozen_on"].initial = timezone.localdate
        self.order_fields(
            ["content", "food_type", "frozen_on", "portions", "containers", "compartment"]
        )

    def clean_frozen_on(self):
        frozen_on = self.cleaned_data["frozen_on"]
        if frozen_on > timezone.localdate():
            raise forms.ValidationError("The freeze date can't be in the future.")
        return frozen_on


class FoodEditForm(FoodForm):
    def __init__(self, *args, item: FoodItem, **kwargs):
        initial = {
            "content": item.content,
            "food_type": item.food_type_id,
            "frozen_on": item.frozen_on,
            "portions": item.portions,
            "compartment": str(item.compartment_id),
        }
        super().__init__(*args, initial=initial, **kwargs)
        del self.fields["containers"]
        self.fields["portions"].label = "Portions"


class GeneralSettingsForm(forms.ModelForm):
    default_weeks = weeks_field(
        "Default shelf life", True, "In weeks, for food types without their own"
    )
    color_window_weeks = weeks_field(
        "Color change starts",
        True,
        "Weeks before expiration that food turns from green towards red",
        max_value=52,
    )

    class Meta:
        model = Settings
        fields = ["default_weeks", "color_window_weeks"]


def food_type_name_taken(name: str, exclude_pk: int | None = None) -> bool:
    return FoodType.objects.filter(name__iexact=name).exclude(pk=exclude_pk).exists()


def show_default_weeks(form, default_weeks: int | None) -> None:
    if default_weeks:
        form.fields["weeks"].widget.attrs["placeholder"] = default_weeks


class FoodTypeForm(forms.ModelForm):
    name = name_field("Name", "Lasagne")
    weeks = weeks_field("Shelf life", False, "Weeks. Empty uses the default.")

    class Meta:
        model = FoodType
        fields = ["name", "weeks"]

    def __init__(self, *args, default_weeks: int | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        show_default_weeks(self, default_weeks)

    def validate_constraints(self):
        pass


class UniqueTypeNames(forms.BaseModelFormSet):
    def clean(self):
        super().clean()
        seen = set()
        for form in self.forms:
            name = form.cleaned_data.get("name")
            if name and name.casefold() in seen:
                form.add_error("name", "This food type already exists.")
            elif name:
                seen.add(name.casefold())

    def names(self) -> set[str]:
        return {f.cleaned_data["name"].casefold() for f in self.forms if f.cleaned_data.get("name")}


FoodTypeFormSet = forms.modelformset_factory(
    FoodType, form=FoodTypeForm, formset=UniqueTypeNames, extra=0, edit_only=True
)


class AddFoodTypeForm(forms.Form):
    name = name_field("New food type", "Lasagne")
    weeks = weeks_field("Shelf life", False, "Weeks. Empty uses the default.")

    def __init__(self, *args, default_weeks: int | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        show_default_weeks(self, default_weeks)
