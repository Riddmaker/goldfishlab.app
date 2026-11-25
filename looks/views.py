"""Serving a look's images: to their owner, and to nobody else (B-10)."""

from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET

from looks import images
from looks.models import Look


@require_GET
@login_required
def image(request, pk, kind):
    if kind not in images.KINDS:
        raise Http404
    # Somebody else's look is a 404, not a 403: it does not say it exists.
    look = get_object_or_404(Look, pk=pk, owner=request.user)
    relative = getattr(look, f"{kind}_image")
    if not relative:
        raise Http404
    try:
        path = images.resolve(relative)
        handle = open(path, "rb")  # noqa: SIM115 - FileResponse closes it
    except (ValueError, OSError) as error:
        # A file lost from the share: the page falls back to the standard look.
        raise Http404 from error
    response = FileResponse(handle, content_type="image/webp", filename=f"{kind}.webp")
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, max-age=86400"
    return response
