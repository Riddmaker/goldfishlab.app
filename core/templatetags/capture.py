"""`{% capture as name %}…{% endcapture %}`: render once, use many times (P3).

Django allows each `{% block %}` once per template, but a page's title is
wanted three times - in `<title>`, `og:title` and `twitter:title` - and so is
its description. base.html captures each block into a variable instead, so the
thirty pages that set them keep doing so in one place.

The captured text is the block's rendered output, which autoescaping has
already escaped; it is marked safe so that it is not escaped a second time.
Surrounding whitespace is stripped, so a block written over several lines
still makes a clean attribute.
"""

from django import template
from django.utils.safestring import mark_safe

register = template.Library()


class CaptureNode(template.Node):
    def __init__(self, nodelist, name):
        self.nodelist = nodelist
        self.name = name

    def render(self, context):
        context[self.name] = mark_safe(self.nodelist.render(context).strip())  # noqa: S308 - escaped when rendered
        return ""


@register.tag
def capture(parser, token):
    """`{% capture as title %}{% block title %}…{% endblock %}{% endcapture %}`"""
    bits = token.split_contents()
    if len(bits) != 3 or bits[1] != "as":
        raise template.TemplateSyntaxError(f"Usage: {{% {bits[0]} as name %}}…{{% end{bits[0]} %}}")
    nodelist = parser.parse(("endcapture",))
    parser.delete_first_token()
    return CaptureNode(nodelist, bits[2])
