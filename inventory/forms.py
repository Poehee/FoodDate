from django import forms

from .models import MAX_CAPACITY, MAX_NAME_LENGTH, Compartment, Freezer

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


class DrawerForm(forms.ModelForm):
    name = name_field("Drawer name", "Top drawer")
    capacity = capacity_field()

    class Meta:
        model = Compartment
        fields = ["name", "capacity"]

    def validate_constraints(self):
        # Checked across the whole formset instead, see UniqueDrawerNames
        pass


class ToploaderForm(forms.ModelForm):
    capacity = capacity_field()

    class Meta:
        model = Compartment
        fields = ["capacity"]

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
