"""English dates as the site wrote them before phase 12: day first, no US order.

Only these four are ours; every other format (and every other language) is
Django's. Templates use them by name, never a literal pattern.
"""

DATE_FORMAT = "j F Y"  # 3 October 2026
DATETIME_FORMAT = "j F Y, H:i T"  # 3 October 2026, 14:05 CEST
SHORT_DATE_FORMAT = "j M Y"  # 3 Oct 2026
SHORT_DATETIME_FORMAT = "j M Y, H:i"  # 3 Oct 2026, 14:05
