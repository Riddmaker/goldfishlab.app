"""The news mail's list (PR 22). Its rules are the changelog's (`core.mailing`);
its salt is its own, so a link in a news mail stops only the news."""

from core import mailing

LIST = mailing.MailList("news", "news.unsubscribe", "news_unsubscribe")
