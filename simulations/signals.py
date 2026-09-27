"""Giving a concurrency slot back when a run is deleted mid-flight.

A run holds one of its owner's concurrency slots from enqueue until `_close`
writes a terminal status. Deleting a deck cascades to its runs, and a run that
is deleted while it is still going never reaches `_close` at all: its chunks
find no row and skip, `finalize_run` finds no row and returns. Until the
2026-09-25 review that left the slot held until its three-hour TTL ran out - so
somebody on the free plan who deleted a deck while it was simulating was told
"you already have 1 simulation running" for three hours, with nothing running
and nothing on screen to explain it.
"""

from django.db.models.signals import pre_delete
from django.dispatch import receiver

from simulations.models import SimulationRun


@receiver(pre_delete, sender=SimulationRun)
def release_slot_of_unfinished_run(sender, instance, **kwargs):
    """A run that never finished gives its slot back as it goes."""
    if instance.is_finished:
        return
    from simulations import services

    services.finish_slot(instance)
