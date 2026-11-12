// blog/static/js/auto_code_collapse.js
(function () {
    'use strict';

    const MAX_VISIBLE_LINES = 32; // поменяй на 20 / 40 если захочешь
    const SELECTOR = '.post-content pre, .forum-post__body pre';

    function getLineHeight(el) {
        const style = window.getComputedStyle(el);
        let lineHeight = parseFloat(style.lineHeight);

        if (Number.isNaN(lineHeight)) {
            const fontSize = parseFloat(style.fontSize) || 16;
            lineHeight = fontSize * 1.5;
        }

        return lineHeight;
    }

    function processPre(pre) {
        if (!pre || pre.dataset.codeCollapseReady === '1') return;

        const code = pre.querySelector('code') || pre;
        const text = (code.textContent || '').replace(/\s+$/, '');

        if (!text) return;

        const lines = text.split(/\r?\n/).length;
        if (lines <= MAX_VISIBLE_LINES) return;

        pre.dataset.codeCollapseReady = '1';

        const style = window.getComputedStyle(pre);
        const padTop = parseFloat(style.paddingTop) || 0;
        const padBottom = parseFloat(style.paddingBottom) || 0;
        const borderTop = parseFloat(style.borderTopWidth) || 0;
        const borderBottom = parseFloat(style.borderBottomWidth) || 0;
        const lineHeight = getLineHeight(pre);

        const collapsedHeight =
            Math.ceil(lineHeight * MAX_VISIBLE_LINES + padTop + padBottom + borderTop + borderBottom);

        const wrapper = document.createElement('div');
        wrapper.className = 'code-collapse is-collapsed';

        const footer = document.createElement('div');
        footer.className = 'code-collapse__footer';

        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'code-collapse__toggle';
        button.setAttribute('aria-expanded', 'false');

        const labelMore = `Показать ещё (${lines} строк)`;
        const labelLess = 'Свернуть';

        function applyState(expanded) {
            if (expanded) {
                wrapper.classList.remove('is-collapsed');
                wrapper.classList.add('is-expanded');
                pre.style.maxHeight = pre.scrollHeight + 'px';
                button.textContent = labelLess;
                button.setAttribute('aria-expanded', 'true');
            } else {
                wrapper.classList.remove('is-expanded');
                wrapper.classList.add('is-collapsed');
                pre.style.maxHeight = collapsedHeight + 'px';
                button.textContent = labelMore;
                button.setAttribute('aria-expanded', 'false');
            }
        }

        const parent = pre.parentNode;
        parent.insertBefore(wrapper, pre);
        wrapper.appendChild(pre);
        footer.appendChild(button);
        wrapper.appendChild(footer);

        button.addEventListener('click', function () {
            const expanded = wrapper.classList.contains('is-expanded');
            applyState(!expanded);
        });

        applyState(false);
    }

    function init() {
        document.querySelectorAll(SELECTOR).forEach(processPre);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () {
            setTimeout(init, 80);
        });
    } else {
        setTimeout(init, 80);
    }

    window.addEventListener('load', init);
})();