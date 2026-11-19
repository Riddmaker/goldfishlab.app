"""Whether this visitor asked for the news by mail before signing up, so the
sign-up page can say what will happen (like `changelog.context_processors`)."""

from django.utils.functional import SimpleLazyObject

from news.services import LIST


def mail_wanted(request):
    return {"news_wanted": SimpleLazyObject(lambda: LIST.wanted(request))}
