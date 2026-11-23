from django.db import migrations

STARTER_TYPES = ["Burrito", "Curry", "Other", "Pasta", "Potato mash", "Rice", "Soup", "Stew"]


def add_starter_data(apps, schema_editor):
    apps.get_model("inventory", "Settings").objects.get_or_create(pk=1)
    FoodType = apps.get_model("inventory", "FoodType")
    for name in STARTER_TYPES:
        if not FoodType.objects.filter(name__iexact=name).exists():
            FoodType.objects.create(name=name)


class Migration(migrations.Migration):
    dependencies = [("inventory", "0002_food")]

    operations = [migrations.RunPython(add_starter_data, migrations.RunPython.noop)]
