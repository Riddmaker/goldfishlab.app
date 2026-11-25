"""A person's look for their decks (B-10): mat, sleeve and their options.

The images are files on the share (`looks.images`); a row holds only their
path relative to `settings.CUSTOM_IMAGES_DIR`, never a URL.
"""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q

from looks.colours import validate_stored


class Look(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="looks"
    )
    name = models.CharField(max_length=60, blank=True)
    #: The account's look: it shows wherever a deck has none of its own.
    is_default = models.BooleanField(default=False)
    #: Paths on the share, `<look hex>/<uuid4 hex>.webp`; blank: the standard.
    mat_image = models.CharField(max_length=80, blank=True)
    sleeve_image = models.CharField(max_length=80, blank=True)
    #: An inner sleeve shows as a coloured rim inside the outer one.
    inner_sleeve = models.BooleanField(default=False)
    inner_colour = models.CharField(max_length=9, blank=True, validators=[validate_stored])
    #: The stitched edge of a mat.
    seam = models.BooleanField(default=False)
    seam_colour = models.CharField(max_length=9, blank=True, validators=[validate_stored])
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner"], condition=Q(is_default=True), name="one_default_look_per_owner"
            ),
        ]

    def __str__(self):
        return self.name or str(self.pk)

    def images(self) -> list[str]:
        return [path for path in (self.mat_image, self.sleeve_image) if path]
