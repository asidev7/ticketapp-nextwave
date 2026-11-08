from django.conf import settings
from django.utils import translation
from django.utils.deprecation import MiddlewareMixin


class CookieLocaleMiddleware(MiddlewareMixin):
    """Locale middleware: English by default, French only when chosen.

    Unlike django.middleware.locale.LocaleMiddleware it ignores the browser's
    Accept-Language header, so every first visit is in English. The EN | FR
    switch (django's `set_language` view) stores the choice in a cookie.
    """

    def process_request(self, request):
        supported = dict(settings.LANGUAGES)
        language = request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME, "")
        if language not in supported:
            language = settings.LANGUAGE_CODE
        translation.activate(language)
        request.LANGUAGE_CODE = translation.get_language()

    def process_response(self, request, response):
        response.headers.setdefault("Content-Language", translation.get_language())
        patch = response.headers.get("Vary", "")
        if "Cookie" not in patch:
            response.headers["Vary"] = f"{patch}, Cookie" if patch else "Cookie"
        return response
