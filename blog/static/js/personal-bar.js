/* Личная шапка и форма подписки на кэшируемых страницах.
 *
 * Страница кэшируется с анонимным меню. Для подмены личного меню JS всегда
 * запрашивает /personal/menu/ (private, no-store) — сервер сам определяет
 * авторизацию по сессионной cookie (HttpOnly, JS её не видит).
 * Если пользователь анонимен, эндпоинт возвращает {"authenticated": false}
 * и меню не меняется. CSRF-cookie для JS-форм выдаётся лениво
 * /personal/csrf/ (private, no-store). */
(function () {
  "use strict";

  function csrfFromCookie() {
    var m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return m ? m[1] : "";
  }

  function initUserbarScripts() {
    // Скрипты userbar из фрагмента /personal/menu/ лежат в DOM как инертные
    // узлы (innerHTML/insertAdjacentHTML не исполняют <script>). Пересоздаём
    // их по одному: браузер скачает и исполнит. Порядок vendor -> userbar
    // важен (webpack-чанки); дубликаты из нескольких [data-auth-menu]
    // схлопываются по src. Имя файла может быть с хэшем манифеста
    // (vendor.<hash>.js на проде) или с query-версией (локальный runserver) —
    // матчим по префиксу базового имени, а не по полному 'vendor.js'.
    var order = ["vendor", "userbar"];
    var seen = {};
    var inert = Array.prototype.slice.call(
      document.querySelectorAll("script[src*='wagtailadmin/js/']")
    );
    order.forEach(function (name) {
      inert
        .filter(function (old) {
          var base = old.getAttribute("src").split("/").pop().split("?")[0];
          return base.indexOf(name + ".") === 0;
        })
        .forEach(function (old) {
          var src = old.getAttribute("src");
          old.remove();
          if (seen[src]) return;
          seen[src] = 1;
          var s = document.createElement("script");
          s.src = src;
          document.body.appendChild(s);
        });
    });
  }

  function swapAuthMenu() {
    var placeholders = document.querySelectorAll("[data-auth-menu]");
    if (!placeholders.length) return;
    fetch("/personal/menu/", { credentials: "same-origin", cache: "no-store" })
      .then(function (r) {
        if (!r.ok) throw new Error(r.status);
        return r.text();
      })
      .then(function (html) {
        // Ответ — HTML с <li> авторизованного меню. Если сервер вернул
        // JSON {"authenticated": false} (аноним), текст не содержит <li> —
        // вставка безопасна, но бессмысленна.
        if (html.indexOf("<li") === -1) return;
        document.querySelectorAll("[data-auth-menu]").forEach(function (ph) {
          ph.insertAdjacentHTML("beforebegin", html);
          ph.remove();
        });
        // Wagtail userbar: <script> внутри insertAdjacentHTML не исполняется —
        // пересоздаём скрипты вручную, чтобы браузер скачал и исполнил их.
        // Начиная с webpack-архитектуры userbar (8.0.x: userbar.js 585 КБ,
        // 8.1: userbar.js 14 КБ + vendor.js 942 КБ) клонировать <aside> из
        // шаблона нельзя: это даёт только мёртвое статичное меню без JS
        // (кнопка «Checks», диалог, shadow DOM — всё инициализирует сам
        // userbar.js, потребляя #wagtail-userbar-template из DOM).
        initUserbarScripts();

        // Админ-бары статей: только для персонала (сервер отдаёт 403 остальным)
        document.querySelectorAll("[data-admin-bar]").forEach(function (slot) {
          fetch("/personal/admin-bar/" + slot.dataset.pageId + "/", { credentials: "same-origin" })
            .then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); })
            .then(function (html) {
              slot.insertAdjacentHTML("beforebegin", html);
              slot.remove();
            })
            .catch(function () { /* не персонал — слот скрыт */ });
        });
      })
      .catch(function () { /* остаётся анонимное меню — статья читаема */ });
  }

  function bindSubscribe() {
    document.querySelectorAll("form[data-subscribe-form]").forEach(function (form) {
      if (form.dataset.bound === "1") return;
      form.dataset.bound = "1";
      form.addEventListener("submit", function (e) {
        e.preventDefault();
        var note = form.querySelector(".subscribe-note");
        var show = function (text, ok) {
          if (!note) { note = document.createElement("p"); note.className = "subscribe-note"; form.appendChild(note); }
          note.textContent = text;
          note.style.color = ok ? "inherit" : "#c0392b";
        };
        fetch("/personal/csrf/", { credentials: "same-origin", cache: "no-store" })
          .then(function () {
            return fetch(form.action, {
              method: "POST", credentials: "same-origin",
              headers: { "X-CSRFToken": csrfFromCookie() },
              body: new FormData(form),
            });
          })
          .then(function (r) {
            if (r.ok) { show("Готово! Проверьте почту.", true); form.reset(); }
            else show("Сервис подписки временно недоступен.", false);
          })
          .catch(function () { show("Сервис подписки временно недоступен.", false); });
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      swapAuthMenu(); bindSubscribe();
    });
  } else {
    swapAuthMenu(); bindSubscribe();
  }
})();
