"""Who gets the changelog by mail, and how they stop (C7).

The mail is off for everybody until they ask for it, and it is only asked for
by a click on "Subscribe by email" (/changelog/) or the switch on "Your plan".
The rules - the wish kept before there is an account, the one-click link that
never expires - are shared with the weekly news and live in `core.mailing`;
this module names the changelog's list and keeps the names the rest of the
code has always used.
"""

from core import mailing

SALT = "changelog.unsubscribe"
LIST = mailing.MailList("changelog", SALT, "changelog_unsubscribe")

#: The session key of a wish to subscribe made before there was an account.
INTENT = LIST.intent
INTENT_FOR = mailing.INTENT_FOR

mail_on = mailing.mail_on
language_of = mailing.language_of
can_subscribe = mailing.can_subscribe
keep_across = mailing.keep_across

subscribe = LIST.subscribe
unsubscribe = LIST.unsubscribe
want = LIST.want
wanted = LIST.wanted
take = LIST.take
unsubscribe_token = LIST.token
user_for = LIST.user_for
unsubscribe_path = LIST.unsubscribe_path
