# blog/middleware.py
from django.http import HttpResponsePermanentRedirect


class ThemePaletteMiddleware:
    """Тема по домену + нормализация легаси-ссылок ?theme=/?palette=.

    Палитра применяется ТОЛЬКО на клиенте (localStorage `site_palette`,
    раннее применение до отрисовки в base.html). Сервер не читает и не
    пишет session ради палитры: обращения к сессии на каждом анонимном
    запросе добавляют `Vary: Cookie` и лишают страницы кэшируемости.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.get_host().lower()

        # 1. Жесткая привязка темы к домену
        if "floppygames" in host or "floppy" in host:
            request.theme = "floppy"
        elif "zenway" in host:
            request.theme = "zenway"
        else:
            request.theme = "nova"

        # 2. Атавизм старых ссылок: ?theme=/?palette= срезаем 301-редиректом.
        #    Раньше palette дополнительно писалась в сессию — теперь выбор
        #    палитры живёт в localStorage браузера и серверу не нужен.
        theme_param = request.GET.get("theme")
        palette_param = request.GET.get("palette")

        if request.method in ("GET", "HEAD") and (
            theme_param or (palette_param and not request.path.startswith("/admin/"))
        ):
            clean_query = request.GET.copy()
            clean_query.pop("theme", None)
            clean_query.pop("palette", None)

            clean_path = "/" + request.path.lstrip("/")
            if clean_query:
                clean_path += f"?{clean_query.urlencode()}"
            return HttpResponsePermanentRedirect(clean_path)

        return self.get_response(request)
