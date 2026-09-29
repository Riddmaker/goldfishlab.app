"""Empty on purpose: the collection feature was removed in phase 9, batch B.

The app stays in `INSTALLED_APPS` for exactly one deploy, so that its last
migration - the one that drops both tables - runs in production. Batch I then
removes the app, this directory and its content types, which is the order
Django's "How to delete a Django application" guide prescribes.
"""
