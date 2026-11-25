"""`{% look_style deck %}`: the owner's look as CSS variables for a table (B-10).

For the deck's owner only: anyone else, or a signed-out visitor, gets the
standard look (an empty string), so a look is never shown to others.
"""

from django import template

from looks import css, services

register = template.Library()


@register.simple_tag(takes_context=True)
def look_style(context, deck) -> str:
    request = context.get("request")
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated or deck is None or deck.owner_id != user.pk:
        return ""
    return css.style(services.default_look(user))
