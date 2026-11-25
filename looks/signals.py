"""A look's files go with its row, also when an account goes (CASCADE)."""

from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from looks import images
from looks.models import Look


@receiver(post_delete, sender=Look)
def remove_files(sender, instance, **kwargs):
    # After the commit: a deletion that rolls back keeps its files.
    for relative in instance.images():
        transaction.on_commit(lambda relative=relative: images.remove(relative))
