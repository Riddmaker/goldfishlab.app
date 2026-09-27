"""Give every new user a Subscription on the free plan.

Doing this from the first release is what keeps `user.subscription` non-null
everywhere, so no quota check ever needs a "what if they have no plan" branch.
"""

import logging

from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from billing.models import Plan, Subscription

logger = logging.getLogger(__name__)


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_free_subscription(sender, instance, created, **kwargs):
    """Attach the default plan to a newly created user."""
    if not created:
        return
    plan = Plan.objects.filter(is_default=True, is_active=True).first()
    if plan is None:
        # Only possible before the seed migration has run (e.g. a bare test
        # database). Loud enough to notice, quiet enough not to break signup.
        logger.warning("No default Plan; user %s has no subscription", instance.pk)
        return
    Subscription.objects.get_or_create(user=instance, defaults={"plan": plan})
