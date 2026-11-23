from django import template
from django.utils.html import format_html
from django.utils.safestring import mark_safe

register = template.Library()

# Outline icons, 24px grid, drawn with the current text color
PATHS = {
    "plus": "M12 5v14M5 12h14",
    "minus": "M5 12h14",
    "up": "M6 15l6-6 6 6",
    "down": "M6 9l6 6 6-6",
    "trash": "M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3",
    "edit": "M4 20h4L18.5 9.5a2.1 2.1 0 0 0-4-4L4 16v4M13.5 6.5l4 4",
    "back": "M15 6l-6 6 6 6",
    "alert": (
        "M12 9v4M12 17h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9"
        "a2 2 0 0 0-3.4 0"
    ),
    "clock": "M12 7v5l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0",
    "check": "M9 12l2 2 4-4M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0",
}


@register.simple_tag
def icon(name: str, label: str = "") -> str:
    hidden = "" if label else ' aria-hidden="true"'
    title = format_html("<title>{}</title>", label) if label else ""
    return format_html(
        '<svg class="icon" viewBox="0 0 24 24" width="20" height="20" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
        'stroke-linejoin="round" focusable="false"{}>{}<path d="{}"/></svg>',
        mark_safe(hidden),  # noqa: S308 - a constant string
        title,
        PATHS[name],
    )


@register.filter
def get_item(mapping: dict, key):
    return mapping.get(key, "")
