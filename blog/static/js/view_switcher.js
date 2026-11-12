/**
 * View Switcher - переключение между сеткой и списком
 */
(function() {
    'use strict';

    const container = document.getElementById('posts-container');
    const buttons = document.querySelectorAll('.view-btn');

    if (!container || !buttons.length) return;

    // Загрузка сохраненных настроек
    const savedView = localStorage.getItem('blogViewPreference') || 'grid';
    setView(savedView, false);

    // Обработчики кликов
    buttons.forEach(button => {
        button.addEventListener('click', function(e) {
            e.preventDefault();
            const view = this.dataset.view;
            setView(view, true);
            localStorage.setItem('blogViewPreference', view);
        });
    });

    /**
     * Установка вида отображения
     */
    function setView(view, animate) {
        // Обновление кнопок
        buttons.forEach(btn => {
            const isActive = btn.dataset.view === view;
            btn.classList.toggle('active', isActive);
            btn.setAttribute('aria-pressed', isActive ? 'true' : 'false');
        });

        // Анимация переключения
        if (animate) {
            container.style.opacity = '0';
            container.style.transform = 'translateY(10px)';

            setTimeout(() => {
                applyView(view);
                container.style.opacity = '1';
                container.style.transform = 'translateY(0)';
            }, 200);
        } else {
            applyView(view);
        }
    }

    /**
     * Применение класса для вида
     */
    function applyView(view) {
        container.className = 'posts-container posts-' + view;
        container.setAttribute('data-view', view);
    }
})();