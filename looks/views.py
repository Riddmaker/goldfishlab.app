"""Serving a look's images: to their owner, and to nobody else (B-10)."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.decorators import method_decorator
from django.utils.translation import gettext
from django.views import View
from django.views.decorators.http import require_GET
from django_ratelimit.decorators import ratelimit

from decks.models import Deck
from looks import css as look_css
from looks import images, presets, services
from looks.forms import LookForm
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


@method_decorator(ratelimit(key="user", rate="30/h", method="POST", block=True), name="post")
class LookView(LoginRequiredMixin, View):
    """Customisation (B-10, PR 2): the account's mat and sleeves, reached from
    a deck page and shown on that deck. Only the owner sees them."""

    template_name = "looks/edit.html"

    def _deck(self, request, pk):
        return get_object_or_404(Deck.objects.filter(owner=request.user), pk=pk)

    def _initial(self, look):
        if look is None:
            return {"mat": presets.STANDARD_MAT, "sleeve": presets.STANDARD_SLEEVE}
        return {
            "mat": look.mat_preset or presets.STANDARD_MAT,
            "sleeve": look.sleeve_preset or presets.STANDARD_SLEEVE,
            "inner_sleeve": look.inner_sleeve, "inner_colour": look.inner_colour,
            "seam": look.seam, "seam_colour": look.seam_colour,
        }

    def _render(self, request, deck, form, look, status=200):
        return render(request, self.template_name, {
            "deck": deck, "form": form, "look": look,
            "preview_style": look_css.style(look),
            "mats": [(slug, label, css) for slug, (label, css) in presets.MATS.items()],
            "sleeves": [(slug, label, css) for slug, (label, css) in presets.SLEEVES.items()],
            "own_mat": look_css.mat(look) if look and look.mat_image else "",
            "own_sleeve": look_css.sleeve(look) if look and look.sleeve_image else "",
            "swatches": presets.SWATCHES,
            "max_mb": images.MAX_BYTES // (1024 * 1024),
        }, status=status)

    def get(self, request, pk):
        deck = self._deck(request, pk)
        look = services.default_look(request.user)
        return self._render(request, deck, LookForm(initial=self._initial(look), look=look), look)

    def post(self, request, pk):
        deck = self._deck(request, pk)
        look = services.default_look(request.user)
        if "reset" in request.POST:
            services.reset(request.user)
            messages.success(request, gettext("Back to the standard look."))
            return redirect("decks:look", pk=deck.pk)
        form = LookForm(request.POST, request.FILES, look=look)
        if form.is_valid():
            try:
                services.save(request.user, form.cleaned_data)
            except images.ImageRefused as refused:
                form.add_error(f"{refused.kind}_file", str(refused))
            else:
                messages.success(request, gettext("Your look is saved."))
                return redirect("decks:look", pk=deck.pk)
        return self._render(request, deck, form, look, status=400)
