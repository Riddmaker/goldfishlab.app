"""Which currency a visitor sees a price in (phase 11 G, K13).

Three currencies, each with the same round number: CHF 4, €4 and $4. A rate
conversion would turn $4 into CHF 3.18, which is what the second user test
called unreadable; the same number in every currency is the decision (K13).

**The country comes from Cloudflare.** Every request reaches us through the
Cloudflare Tunnel, and Cloudflare adds `CF-IPCountry` (the visitor's country,
from their IP address) when "IP Geolocation" is on in the dashboard. The tunnel
passes request headers on unchanged. Without the header - development, the test
suite, an unknown country (`XX`) or Tor (`T1`) - the visitor sees US dollars.

**Switched off by default** (`LOCAL_PRICES`). Euro prices on our own page are
a sign of offering to EU customers (GDPR Art. 3(2), EDPB 3/2018 Example 16),
so switching this on waits for Managed Payments and an EU representative (P10).
Off, every visitor sees Swiss francs, exactly as before.

The header is not a security boundary: anybody who reaches Cloudflare gets the
country Cloudflare sees, and the worst a forged one could do is pick another
of three prices that are the same number.
"""

from django.conf import settings

CHF, EUR, USD = "chf", "eur", "usd"
CURRENCIES = (CHF, EUR, USD)

#: Switzerland and Liechtenstein pay in francs.
FRANC_COUNTRIES = frozenset({"CH", "LI"})

#: The EU and the rest of the EEA (Iceland, Norway; Liechtenstein is above).
#: Every one sees euros, also where the local money is not the euro (P9).
EURO_COUNTRIES = frozenset({
    "AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR", "GR",
    "HR", "HU", "IE", "IT", "LT", "LU", "LV", "MT", "NL", "PL", "PT", "RO",
    "SE", "SI", "SK",
    "IS", "NO",
})

#: How each currency is written in front of the number.
SYMBOLS = {CHF: "CHF ", EUR: "€", USD: "$"}


def for_country(country: str) -> str:
    """The currency for an ISO country code; US dollars for anything else."""
    country = (country or "").strip().upper()
    if country in FRANC_COUNTRIES:
        return CHF
    if country in EURO_COUNTRIES:
        return EUR
    return USD


def for_request(request) -> str:
    """The currency this visitor sees. Swiss francs while `LOCAL_PRICES` is off."""
    if not settings.LOCAL_PRICES:
        return CHF
    return for_country(request.META.get("HTTP_CF_IPCOUNTRY", ""))


def label(cents: int, currency: str) -> str:
    """'CHF 4', '€4', '$4' - and two decimals only when there are cents."""
    whole, rest = divmod(cents, 100)
    amount = f"{whole}" if not rest else f"{whole}.{rest:02d}"
    return f"{SYMBOLS[currency]}{amount}"
