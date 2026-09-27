# VAT, and Stripe Managed Payments (2026-09-25) - built, waiting for Stripe's approval

**The question the user asked:** "Doesn't Stripe handle VAT?" **The answer:** plain Stripe does
not; *Stripe Managed Payments* does. This file is the research, the decision and the checklist.
A box is ticked only when the code, its tests and the docs it touches are all done.

**Status: built and tested 2026-09-25, off by default.** What is left needs the live Stripe
account: the eligibility review, the tax codes, tax-inclusive prices, and one test-mode purchase
(GO-LIVE step 10, 2e). One legal point is open for the user - Adaptive Pricing and the GDPR
"not targeted" argument (GO-LIVE, Legal section).

## What the research found (sources: see the end)

| Where | Obligation for Goldfish Lab (Swiss private individual, tiny B2C revenue) |
|---|---|
| **Switzerland** | None below **CHF 100,000** turnover a year - foreign sales count towards it. Electronic services are taxed where the customer is (Art. 8 para. 1 MWSTG), so sales to foreign consumers are not Swiss-taxable at all |
| **EU** | **From the first euro.** The EUR 10,000 threshold is only for sellers established in an EU state. A SaaS subscription is an "electronically supplied service" (Implementing Regulation 282/2011, Art. 7 and Annex II), taxed at the consumer's country's rate. "We do not target the EU" is irrelevant for VAT - it only matters for the GDPR and consumer law |
| **UK** | **From the first sale**, full UK VAT registration, no OSS equivalent |
| Norway, Australia, Canada, US states | Thresholds (NOK 50k, AUD 75k, CAD 30k, usually USD 100k per state) - irrelevant at this scale |

Doing it ourselves would mean non-Union OSS registration (one EU state, quarterly returns, every
country's rate, 10 years of records, two pieces of location evidence per sale) plus UK VAT
registration - about 8 returns a year for a few thousand francs of revenue. Disproportionate.

### The options

| Option | Who is liable for VAT | Extra cost at CHF 2,000/yr | Effort |
|---|---|---|---|
| **Stripe Tax** (Basic, 0.5% per transaction) | **We are.** It calculates and collects; Stripe's docs: "As a business, you're required to identify… where you have tax obligations. You must then register…". Filing via "Tax Complete" (Taxually) from CHF 80/month | ~CHF 10 + registrations + filings | High, recurring |
| **Stripe Managed Payments** - **chosen** | **Stripe ("Sold through Link")** is the merchant of record and registers, files and remits in the EU, UK, CH, NO, US, CA, AU and more | **+3.5% ≈ CHF 70** (total Stripe cost ≈ 12% of revenue) | One Checkout parameter, a tax code per product, text changes |
| Paddle (fallback) | Paddle, as merchant of record | ≈ CHF 220 (5% + USD 0.50 hurts a CHF 4 plan) | Full re-integration: Paddle checkout, webhooks, portal. Accepts individuals; Swiss sellers not confirmed |
| Lemon Squeezy | - | - | Being wound down into Stripe Managed Payments; not for new integrations |
| Block EU/UK consumers | nobody | CHF 0 | Loses the main markets; country blocking in Checkout is imperfect |

### What Managed Payments means, concretely

- **Eligibility:** Switzerland is a supported business location; SaaS is eligible (tax code
  `txcd_10103000`, "SaaS - personal use"; every product needs a tax code, and the product must be
  fully automated). **Access is an eligibility review** "that considers factors such as business
  type and geography" - a sole proprietor is not excluded anywhere, but approval is not guaranteed.
- **API:** `managed_payments[enabled]=true` on the Checkout Session, API version
  2025-03-31.basil or later. Our pinned `stripe==15.6.1` defaults to `2026-08-26.dahlia` and
  already has the parameter (`SessionCreateParamsManagedPayments`). Checkout in **subscription
  mode is supported**; the Customer Portal still works.
- **Forbidden alongside it:** `automatic_tax`, `tax_id_collection`, `payment_method_types` /
  `payment_method_configuration`, `customer_update[name|address]`, `adaptive_pricing`,
  `invoice_creation`, `subscription_data.invoice_settings`. **`billing/services.py` uses none of
  them** - checked 2026-09-25. Custom checkout domains are not supported. Existing subscriptions
  cannot be moved over (there are none).
- **Webhooks:** Subscription and Invoice objects are still created in our account, so
  `customer.subscription.*` and `invoice.*` should keep firing - **inferred from the docs, must be
  confirmed in test mode.**
- **Who sells:** the customer buys from **Link** ("Sold through Link"); receipts come from Link;
  the card statement says `LINK.COM* …`. Stripe's own buyer terms appear in the Checkout footer;
  our terms and privacy URLs can be added in the Checkout settings.
- **Refunds and disputes:** Stripe handles disputes itself; for a refund request "If you don't
  respond within 48 hours, Stripe might issue a refund without your approval", within 60 days,
  and Stripe applies statutory cooling-off periods itself. Customers may ask Link to delete their
  data, which "cancels any subscriptions sold to them through Managed Payments".
- **Prices** are shown in the customer's currency (Adaptive Pricing, always on). Payout currency
  stays the account's (CHF) - **assumed, not confirmed in the docs.** This is also why the GDPR
  "not targeted" argument (CHF only) needs a decision - GO-LIVE, Legal section.
- **Tax on top by default.** "If you don't specify the price's tax behavior, Managed Payments
  adds tax to the price you set" (update-checkout page, re-read 2026-09-25). Our terms say prices
  include tax, so the prices must be created tax-inclusive - GO-LIVE step 10, 2e.3.
- **Customers can also cancel or change the subscription at link.com**, and a data-deletion
  request to Link cancels it. Both arrive as ordinary `customer.subscription.*` events, which
  the webhook already handles; the plans page and the terms mention link.com when it is on.
- **The seller entity** is "Sold through Link, LLC" (formerly LemonSqueezy LLC, renamed 6 April
  2026), a Stripe company; payments are processed by Stripe Payments Company or Stripe
  Technology Europe, Limited. Link is an **independent controller** for the payment data
  (link.com/privacy).

## The decision

**Build Managed Payments behind a setting, keep the current path as the fallback.** Stripe may
decline the account in its review; then the plain path still works (and the VAT question is back
on the table - Paddle, or selling only where no registration is needed). Decided with the user
2026-09-25: "dokumentier das gut … dann gehen wir das an … dann machen wir go-live".

## The checklist

### Code
- [x] `STRIPE_MANAGED_PAYMENTS = env.bool("STRIPE_MANAGED_PAYMENTS", default=False)` in
      `goldfishlab/settings/base.py` beside the Stripe keys; `.env.example`; a
      `jelastic.jps.example` form field (string, default "false") on the `cp` node only.
- [x] `billing/services.start_checkout` adds `"managed_payments": {"enabled": True}` when the
      setting is on. The forbidden list was re-read 2026-09-25 and lives in
      `services.MANAGED_PAYMENTS_FORBIDDEN` (+ `..._IN_SUBSCRIPTION_DATA`); `consent_collection`,
      `custom_text`, `submit_type`, `customer`, `customer_email`, `client_reference_id` and
      `subscription_data.metadata` are not on it and stay. The checkbox text names link.com as a
      second place to cancel; our 14-day refund sentence is unchanged (we refund from the
      dashboard, which Managed Payments allows).
- [x] Tests in `tests/test_billing_stripe.py`: the parameter only when on, the checkbox text in
      both states, no forbidden parameter for a new or a returning customer, the metadata kept.
- [x] The plans page: a "sold through Link" line under the tiers (only when something is
      purchasable) and a link.com mention beside the portal button. Context processor
      `core.context_processors.payments` → `sold_through_link`.

### Legal pages (both states must be described truthfully - render by the setting)
- [x] **Terms, "Plans and paying" and "Refunds":** with Managed Payments on, the seller of the
      subscription is Link (Stripe), which also issues receipts and handles payment, refunds and
      disputes under its own buyer terms; our 14-day refund still stands - we issue it from the
      Stripe dashboard (Managed Payments allows that; the refund includes the tax), and Link's
      support may refund too. Prices set in CHF, may be shown converted, taxes included.
- [x] **Privacy, "Who else sees what":** Link / Stripe as merchant of record is an **independent
      controller** of payment and tax data (name, email, billing country/address, IP for tax
      location evidence); link Link's and Stripe's privacy policies; a deletion request to Link
      cancels the subscription.
- [x] `test_privacy.py::test_the_legal_pages_say_who_sells_a_paid_plan` pins both states (off:
      Link is not mentioned at all). The privacy table's invoice row says who keeps invoices.

### Documents
- [x] GO-LIVE step 10: apply for Managed Payments (Dashboard → Managed Payments / eligibility);
      set tax code `txcd_10103000` on both products; add our terms and privacy URLs in the
      Checkout settings; set `STRIPE_MANAGED_PAYMENTS=true` only after approval; **test-mode
      purchase proves the webhooks** (`customer.subscription.created`, `invoice.paid`) still reach
      `/billing/webhook/` and change the plan.
- [x] GO-LIVE "Legal" section: VAT bullet points here; the Adaptive Pricing / GDPR open point.
- [x] RESUME top block, project memory.

### Verification
- [x] `pytest -m "not slow"` (1020 passed), ruff, djlint, `makemigrations --check`; screenshots of terms,
      privacy in both states (the plans-page line is pinned by a test only - it needs purchasable plans).

## Sources

- Swiss VAT liability and turnover: https://www.estv.admin.ch/estv/de/home/mehrwertsteuer/mwst-steuerpflicht.html
- EU non-Union OSS and the EUR 10,000 threshold: https://vat-one-stop-shop.ec.europa.eu/one-stop-shop_en ·
  https://taxation-customs.ec.europa.eu/taxation/vat/vat-directive/place-taxation_en
- ESS definition: https://eur-lex.europa.eu/eli/reg_impl/2011/282/2022-07-01/eng
- UK: https://www.gov.uk/guidance/the-vat-rules-if-you-supply-digital-services-to-private-consumers
- Stripe Tax registration duty: https://docs.stripe.com/tax/registering · pricing https://stripe.com/tax/pricing
- Managed Payments: https://stripe.com/managed-payments · https://docs.stripe.com/payments/managed-payments/eligibility ·
  https://docs.stripe.com/payments/managed-payments/update-checkout ·
  https://docs.stripe.com/payments/managed-payments/how-it-works ·
  https://docs.stripe.com/payments/managed-payments/tax-compliance ·
  https://support.stripe.com/questions/why-is-sold-through-link-llc-the-legal-entity-on-my-invoice-for-stripe-managed-payments-fees
- Paddle: https://www.paddle.com/pricing · Lemon Squeezy: https://www.lemonsqueezy.com/blog/2026-update
