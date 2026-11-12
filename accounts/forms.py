"""Антибот-защита регистрации (Д50/Д51, координатор 2026-11-06).

Три уровня для формы signup (только новые аккаунты):
1. Honeypot: скрытое поле с правдоподобным именем; заполнено -> отказ без
   создания пользователя и без письма подтверждения.
2. Математическая проверка: пример генерируется на сервере; правильный ответ
   хранится ТОЛЬКО на сервере (кэш security, TTL 30 минут); в HTML передаётся
   только случайный непрозрачный идентификатор задания. Ответ не попадает в
   HTML, JS или какой-либо подписанный/сериализованный клиентский токен
   (Django signing ПОДПИСЫВАЕТ, но не ШИФРУЕТ — ответ в токене читаем ботом,
   Д51-Б). Одноразовость: used-маркер в persistent (DatabaseCache) —
   атомарное погашение через PK-констрейнт PostgreSQL (Д56).
3. Ограничение частоты: штатный allauth-лимитер (ACCOUNT_RATE_LIMITS в
   settings), ключ по REMOTE_ADDR (X-Forwarded-For не доверяется по
   умолчанию — проверено get_adapter().get_client_ip); кэш default —
   FileBasedCache, общий для воркеров.

Подключение: ACCOUNT_FORMS['signup'] (резолв в рантайме запроса — цикла
импорта с allauth.account.forms нет, в отличие от ACCOUNT_SIGNUP_FORM_CLASS).
Перехват происходит ДО создания пользователя и до отправки подтверждения;
почта/SMTP/шаблоны почты не затрагиваются.
"""
import secrets

from django import forms
from django.core.cache import caches

from allauth.account.forms import SignupForm

# Задания проверки: кэш security (без вытеснения до истечения TTL —
# вытесненное задание блокировало бы легального пользователя).
MATH_CACHE = "security"
# Used-маркер одноразовости: кэш persistent (DatabaseCache, PostgreSQL).
# FileBasedCache.add() — check-then-set без блокировок (has_key() -> set()):
# два воркера, погасившие задание одновременно, ОБА получали True (Д55,
# 39/40 раундов двухпроцессного зонда). DatabaseCache.add() = INSERT внутри
# transaction.atomic, cache_key — PRIMARY KEY; проигравший конкурентный
# INSERT получает IntegrityError -> Django возвращает False (Д56).
USED_CACHE = "persistent"
MATH_TTL = 30 * 60  # 30 минут на отправку формы с этим заданием

# honeypot-поле: правдоподобное имя, автозаполнение отключено
HONEYPOT_FIELD = "company"


def _new_math_task():
    """Сгенерировать задание: вопрос и ответ одной цифрой (1..9).

    Возвращает (task_id, question): task_id — непрозрачный идентификатор,
    answer кладётся в кэш security и никогда не покидает сервер.
    """
    if secrets.randbelow(2) == 0:
        a = secrets.randbelow(8) + 1
        b = secrets.randbelow(9 - a) + 1  # сумма <= 9
        question, answer = f"{a} + {b} = ?", a + b
    else:
        a = secrets.randbelow(8) + 2  # 2..9
        b = secrets.randbelow(a - 1) + 1  # разность >= 1
        question, answer = f"{a} - {b} = ?", a - b
    task_id = "mt_" + secrets.token_urlsafe(16)
    caches[MATH_CACHE].set(
        f"signup-math:{task_id}", {"question": question, "answer": answer}, MATH_TTL
    )
    return task_id, question


def consume_math_task(task_id):
    """Атомарно погасить задание. True = погашено ЭТОЙ попыткой.

    Гарантия межпроцессной атомарности (Д55/Д56): used-маркер пишется в
    persistent (DatabaseCache). add() = INSERT в transaction.atomic с
    уникальным cache_key (PRIMARY KEY): при двух одновременных погашениях
    из разных воркеров СУБД допускает только первый INSERT — второй
    получает IntegrityError и Django возвращает False. TTL маркера равен
    TTL задания; использованный идентификатор не переиспользуется.
    """
    return caches[USED_CACHE].add(f"signup-math:used:{task_id}", True, MATH_TTL)


class AntiSpamSignupForm(SignupForm):
    """SignupForm + honeypot + серверная математическая проверка (Д50/Д51)."""

    company = forms.CharField(
        required=False,
        label="Компания",
        widget=forms.TextInput(
            attrs={
                # Скрытие: уводим за экран (не только display:none),
                # tabindex=-1 исключает клавиатурную навигацию,
                # autocomplete=off запрещает автозаполнение,
                # aria-hidden исключает озвучивание скринридером.
                "tabindex": "-1",
                "autocomplete": "off",
                "aria-hidden": "true",
                "style": "position:absolute;left:-10000px;top:auto;"
                "width:1px;height:1px;overflow:hidden;",
            }
        ),
    )
    math_answer = forms.IntegerField(
        required=False,
        label="",  # вопрос отображается через form.math_question
        widget=forms.TextInput(
            attrs={
                "inputmode": "numeric",
                "autocomplete": "off",
                "placeholder": "Ответ цифрой",
            }
        ),
    )
    # Токен-идентификатор задания: значение рендерит шаблон из
    # form.math_task_id (всегда актуальное); сам ответ в нём не хранится.
    math_token = forms.CharField(required=False, widget=forms.HiddenInput())

    def __init__(self, *args, request=None, **kwargs):
        # request не пробрасывается в BaseForm (allauth его не принимает).
        self.request = request
        super().__init__(*args, **kwargs)
        # Задание создаётся ТОЛЬКО для несвязанной формы (GET-рендер): на POST
        # проверяется задание из запроса, а свежее генерирует clean() при
        # ошибках. Иначе каждый POST плодил бы задания в кэше до TTL (Д52).
        if not self.is_bound:
            self.math_task_id, self.math_question = _new_math_task()
        else:
            self.math_task_id = self.data.get("math_token") or ""
            self.math_question = ""

    def clean_company(self):
        """Honeypot: заполнено -> отклоняем без создания пользователя."""
        if self.cleaned_data.get(HONEYPOT_FIELD):
            raise forms.ValidationError("Регистрация отклонена.")
        return ""

    def clean(self):
        cleaned_data = super().clean()
        # Проверка задания выполняется в clean() (после всех полей), чтобы
        # видеть финальный ответ; при любой ошибке формы задание
        # перегенерируется — повторный рендер показывает свежий вопрос.
        if self.errors:
            self.math_task_id, self.math_question = _new_math_task()
            return cleaned_data
        task_id = cleaned_data.get("math_token") or ""
        task = caches[MATH_CACHE].get(f"signup-math:{task_id}")
        if task is None:
            # Истёк TTL или задание уже погашено.
            self.add_error(
                "math_answer",
                "Время на проверку истекло или задание уже использовано. "
                "Обновите страницу и ответьте на новый вопрос.",
            )
            self.math_task_id, self.math_question = _new_math_task()
            return cleaned_data
        answer = cleaned_data.get("math_answer")
        if answer is None or int(answer) != int(task["answer"]):
            self.add_error("math_answer", "Неверный ответ на проверку.")
            self.math_task_id, self.math_question = _new_math_task()
            return cleaned_data
        # Одноразовость: атомарное погашение (Д52). Два параллельных POST
        # с одним заданием — погасит только первый, второй будет отклонён.
        if not consume_math_task(task_id):
            self.add_error(
                "math_answer",
                "Это задание уже было использовано. Обновите страницу.",
            )
            self.math_task_id, self.math_question = _new_math_task()
        return cleaned_data
