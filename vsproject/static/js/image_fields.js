(function() {
    'use strict';

    // Автозаполнение year и slug для изображений
    const getCurrentYear = () => new Date().getFullYear();

    const getPageSlug = () => {
        // Получаем slug из поля формы страницы
        const slugField = document.querySelector('#id_slug');
        return slugField ? slugField.value : 'misc';
    };

    // Патчим формы загрузки изображений
    function patchImageUploadForm(form) {
        const year = getCurrentYear();
        const slug = getPageSlug();

        // Находим или создаем поля year и slug
        let yearInput = form.querySelector('input[name*="year"]');
        let slugInput = form.querySelector('input[name*="slug"]');

        if (!yearInput) {
            yearInput = document.createElement('input');
            yearInput.type = 'hidden';
            yearInput.name = 'year';
            form.appendChild(yearInput);
        }

        if (!slugInput) {
            slugInput = document.createElement('input');
            slugInput.type = 'hidden';
            slugInput.name = 'slug';
            form.appendChild(slugInput);
        }

        yearInput.value = year;
        slugInput.value = slug;
    }

    // Наблюдаем за появлением модальных окон
    const observer = new MutationObserver(mutations => {
        mutations.forEach(mutation => {
            mutation.addedNodes.forEach(node => {
                if (node.nodeType === 1) {
                    // Ищем формы загрузки в новых элементах
                    const forms = node.querySelectorAll('form[action*="/images/"]');
                    forms.forEach(patchImageUploadForm);
                }
            });
        });
    });

    // Запускаем наблюдение
    document.addEventListener('DOMContentLoaded', () => {
        observer.observe(document.body, {
            childList: true,
            subtree: true
        });
    });
})();