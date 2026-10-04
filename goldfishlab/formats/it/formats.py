"""Italian dates without the zero and with the months small: 4 ottobre 2026.

Django's Italian writes "04 Ottobre 2026". The small month names come from
our catalogue (`core/month_names.py`); every other format is Django's.
"""

DATE_FORMAT = "j F Y"  # 4 ottobre 2026
DATETIME_FORMAT = "j F Y, H:i"  # 4 ottobre 2026, 14:05
