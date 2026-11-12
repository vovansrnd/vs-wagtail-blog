// blog/static/js/admin_prose_editor_sticky.js
(function () {
    'use strict';

    function updateStickyOffset() {
        const header =
            document.querySelector('.w-header') ||
            document.querySelector('header[role="banner"]') ||
            document.querySelector('[data-controller="w-header"]');

        let top = 72;

        if (header) {
            const rect = header.getBoundingClientRect();
            if (rect.height) {
                top = Math.ceil(rect.height + 8);
            }
        }

        document.documentElement.style.setProperty('--admin-prose-sticky-top', `${top}px`);
    }

    document.addEventListener('DOMContentLoaded', updateStickyOffset);
    window.addEventListener('load', updateStickyOffset);
    window.addEventListener('resize', updateStickyOffset);
})();