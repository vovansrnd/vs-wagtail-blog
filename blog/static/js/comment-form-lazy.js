/* Ленивая загрузка формы комментариев.
 * Статья кэшируется целиком (без CSRF/токенов/сессии), форма приезжает
 * приватным фрагментом (Cache-Control: private, no-store) по клику,
 * по ссылке-якорю #comment-form-anchor или по кнопке «Ответить». */
(function () {
  "use strict";

  function openSlot(slot) {
    if (slot.dataset.loaded === "1") {
      document.dispatchEvent(new CustomEvent("comment-form-ready", { bubbles: true }));
      return;
    }
    var body = slot.querySelector(".cfl-body");
    var btn = slot.querySelector(".cfl-btn");
    var url = "/comments/form/" + slot.dataset.pageId + "/";
    btn && btn.setAttribute("disabled", "disabled");
    fetch(url, { credentials: "same-origin" })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.text();
      })
      .then(function (html) {
        var frag = document.createRange().createContextualFragment(html);
        body.appendChild(frag);
        slot.dataset.loaded = "1";
        btn && btn.remove();
        // Инициализация prose-редактора во вставленном фрагменте (модуль
        // уже предзагружен в <head> страницы через {{ comment_form_media }}).
        var ta = body.querySelector("[data-django-prose-editor-configurable]");
        var moduleUrl = slot.dataset.editorModule;
        if (ta && moduleUrl) {
          import(/* @vite-ignore */ moduleUrl)
            .then(function (mod) {
              if (mod && typeof mod.createEditor === "function") mod.createEditor(ta);
            })
            .catch(function () { /* редактор недоступен — останется textarea */ });
        }
        slot.dispatchEvent(new CustomEvent("comment-form-ready", { bubbles: true }));
        var anchor = document.getElementById("comment-form-anchor");
        if (anchor && location.hash === "#comment-form-anchor") {
          anchor.scrollIntoView({ behavior: "smooth", block: "start" });
        }
      })
      .catch(function () {
        btn && btn.removeAttribute("disabled");
        body.innerHTML = '<p class="cfl-error">Не удалось загрузить форму. Обновите страницу и попробуйте ещё раз.</p>';
      });
  }

  function bind() {
    document.querySelectorAll("[data-comment-form-lazy]").forEach(function (slot) {
      if (slot.dataset.bound === "1") return;
      slot.dataset.bound = "1";
      var btn = slot.querySelector(".cfl-btn");
      btn && btn.addEventListener("click", function () { openSlot(slot); });
    });
    // «Ответить» под комментарием: открыть форму, потом расставить цитату.
    document.addEventListener("click", function (e) {
      if (!e.target.closest(".comment-reply-btn")) return;
      var slot = document.querySelector("[data-comment-form-lazy]");
      if (slot) {
        CommentFormLazy.open(slot);
      }
    });
    // Возврат после POST: редирект ведёт на якорь формы — открыть сразу.
    if (location.hash === "#comment-form-anchor") {
      var slot = document.querySelector("[data-comment-form-lazy]");
      slot && CommentFormLazy.open(slot);
    }
  }

  var CommentFormLazy = { open: openSlot };
  window.CommentFormLazy = CommentFormLazy;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }
})();
