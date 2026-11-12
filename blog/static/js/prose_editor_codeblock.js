// blog/static/js/prose_editor_codeblock.js
(function () {
  'use strict';

  function parseEditorConfig(wrapper) {
    const textarea = wrapper.querySelector('textarea');
    if (!textarea) return {};

    try {
      return JSON.parse(
        textarea.dataset.djangoProseEditorConfigurable ||
        textarea.dataset.djangoProseEditorDefault ||
        '{}'
      );
    } catch (e) {
      return {};
    }
  }

  function addCodeButtons(wrapper) {
    if (!wrapper || wrapper.dataset.codeButtonsReady === '1') return;

    const menubar = wrapper.querySelector('.prose-menubar');
    const tiptap = wrapper.querySelector('.tiptap');
    const editor = tiptap && tiptap.editor;

    if (!menubar || !editor) return;

    const cfg = parseEditorConfig(wrapper);
    const extensions = cfg.extensions || {};

    const hasCodeBlock = !!extensions.CodeBlock;
    const hasInlineCode = !!extensions.Code;

    if (!hasCodeBlock && !hasInlineCode) return;

    wrapper.dataset.codeButtonsReady = '1';

    // ---------------------------------------------------------
    // 1. УДАЛЯЕМ ДУБЛИРУЮЩИЕ СТАНДАРТНЫЕ КНОПКИ АВТОРА
    // ---------------------------------------------------------
    // Удаляем стандартную кнопку инлайн-кода (button[data-name="code"])
    const defaultCodeBtn = menubar.querySelector('button[data-name="code"]');
    if (defaultCodeBtn) {
      defaultCodeBtn.remove();
    }

    // Удаляем пункт Code block из выпадающего списка
    const defaultCodeBlockOption = menubar.querySelector('button[data-name="codeBlock"]');
    if (defaultCodeBlockOption) {
      defaultCodeBlockOption.remove();
    }

    // Если в выпадающем меню остался только 1 пункт (Paragraph) — скрываем сам выпадающий список
    const dropdown = menubar.querySelector('.prose-menubar__dropdown');
    if (dropdown) {
      const remainingOptions = dropdown.querySelectorAll('.prose-menubar__option');
      if (remainingOptions.length <= 1) {
        dropdown.style.display = 'none';
      }
    }

    // ---------------------------------------------------------
    // 2. СОЗДАЁМ ВАШУ КРАСИВУЮ ПАНЕЛЬ
    // ---------------------------------------------------------
    const group = document.createElement('div');
    group.className = 'prose-menubar__group prose-menubar__group--code';

    let blockBtn = null;
    let inlineBtn = null;

    // Инлайн-код
    if (hasInlineCode) {
      inlineBtn = document.createElement('button');
      inlineBtn.type = 'button';
      inlineBtn.className = 'prose-menubar__button material-icons prose-menubar__button--inline-code';
      inlineBtn.textContent = 'code';
      inlineBtn.title = 'Инлайн-код';

      inlineBtn.addEventListener('click', function (e) {
        e.preventDefault();
        editor.chain().focus().toggleCode().run();
      });

      group.appendChild(inlineBtn);
    }

    // Блок кода (терминал)
    if (hasCodeBlock) {
      blockBtn = document.createElement('button');
      blockBtn.type = 'button';
      blockBtn.className = 'prose-menubar__button material-icons prose-menubar__button--code-block';
      blockBtn.textContent = 'terminal';
      blockBtn.title = 'Блок кода (Terminal)';

      blockBtn.addEventListener('click', function (e) {
        e.preventDefault();
        editor.chain().focus().toggleCodeBlock().run();
      });

      group.appendChild(blockBtn);
    }

    // Вставляем сразу после группы форматирования (жирный/курсив)
    const marksGroup = menubar.querySelector('.prose-menubar__group:nth-child(4)') || menubar.lastElementChild;
    if (marksGroup && marksGroup.parentNode === menubar) {
      marksGroup.after(group);
    } else {
      menubar.appendChild(group);
    }

    // ---------------------------------------------------------
    // 3. ПОДСВЕТКА АКТИВНОГО СОСТОЯНИЯ
    // ---------------------------------------------------------
    function syncState() {
      if (inlineBtn) {
        inlineBtn.classList.toggle('active', editor.isActive('code'));
      }
      if (blockBtn) {
        blockBtn.classList.toggle('active', editor.isActive('codeBlock'));
      }
    }

    editor.on('selectionUpdate', syncState);
    editor.on('transaction', syncState);
    editor.on('focus', syncState);

    syncState();
  }

  document.addEventListener('DOMContentLoaded', function () {
    const observer = new MutationObserver(function () {
      document.querySelectorAll('.prose-editor').forEach(addCodeButtons);
    });

    observer.observe(document.body, { childList: true, subtree: true });
    document.querySelectorAll('.prose-editor').forEach(addCodeButtons);
  });
})();
