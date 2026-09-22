from django import template
from django.contrib.staticfiles import finders
from django.templatetags.static import static

register = template.Library()


@register.simple_tag
def asset(path):
    """static() plus ?v=<mtime>, so browsers pick up CSS/JS changes instead of a cached copy."""
    found = finders.find(path)
    if not found:
        return static(path)
    from os.path import getmtime

    return f"{static(path)}?v={int(getmtime(found))}"
