# blog/context_processors.py

def theme(request):
    current = getattr(request, "theme", "nova")

    # Базовые шаблоны для каждой темы
    theme_templates = {
        "floppy": "blog/floppy/base.html",
        "zenway": "blog/zenway/base.html",
        "nova": "base.html",
    }
    base_template = theme_templates.get(current, "base.html")

    return {
        "theme": current,
        "theme_base_template": base_template,
    }
