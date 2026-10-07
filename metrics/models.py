"""One number per thing per day (P2).

**Nothing here points at a person.** A row is a day, the name of what
happened and how often - no user, no address, no session. That is the whole
privacy design: a count cannot be exported, corrected or deleted for somebody
because it is about nobody, and it outlives the guests and accounts it
counted, which is the point - a guest is deleted after a day, and a week's
numbers must not shrink with it.
"""

from django.db import models


class DailyCount(models.Model):
    class Name(models.TextChoices):
        GUEST_STARTED = "guest_started", "Guests started"
        GUEST_SAVED = "guest_saved", "Guests saved as an account"
        SIGNUP = "signup", "Accounts opened"
        RUN_GUEST = "run_guest", "Simulations started by guests"
        RUN_MEMBER = "run_member", "Simulations started by accounts"
        #: Counted on a person's first simulation of the week (Monday to
        #: Sunday, the site's time zone), so a week's sum is how many
        #: different people simulated in it - the launch plan's lead metric.
        SIMULATOR_GUEST = "simulator_guest", "Guests who simulated"
        SIMULATOR_MEMBER = "simulator_member", "Accounts that simulated"
        REPORT_OPENED = "report_opened", "Shared reports opened"
        #: P9: a finished report opened by its owner (a guest or an account),
        #: once per report and browser session - the fake door's denominator.
        REPORT_VIEWED = "report_viewed", "Reports viewed"
        #: P9: "Compare two versions" clicked, once per browser session.
        COMPARE_CLICKED = "compare_clicked", "Compare two versions clicked"
        #: P11: a data page opened (the precon table or a precon), robots
        #: left out - is anybody finding them?
        DATA_PAGE_OPENED = "data_page_opened", "Data pages opened"

    #: In the site's time zone (settings.TIME_ZONE), as the stats page reads it.
    day = models.DateField()
    name = models.CharField(max_length=32, choices=Name.choices)
    value = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-day", "name"]
        constraints = [
            models.UniqueConstraint(fields=["day", "name"], name="one_count_per_day_and_name"),
        ]

    def __str__(self):
        return f"{self.day} {self.name}={self.value}"
