/* static/js/auto_image_link.js */
document.addEventListener('DOMContentLoaded', () => {
    // Ищем картинки только в контенте поста, не в комментариях
    document.querySelectorAll('.rich-text img, .post-content img, .body img')
        .forEach(img => {
            // Уже обёрнута ссылкой
            if (img.closest('a')) return;

            // Исключаем аватары и всё внутри комментариев/формы
            if (
                img.classList.contains('comment-avatar') ||
                img.classList.contains('compose-avatar') ||
                img.classList.contains('comment-form-avatar') ||
                img.classList.contains('zw-topbar-avatar') ||
                img.closest('.comments-wrap') ||
                img.closest('.comment-compose') ||
                img.closest('.compose-trigger') ||
                img.closest('.compose-body')
            ) {
                return;
            }

            const a = document.createElement('a');
            a.href = img.currentSrc || img.src;
            a.classList.add('gallery-link');
            a.dataset.caption = img.alt || '';
            img.parentNode.insertBefore(a, img);
            a.appendChild(img);
        });

    baguetteBox.run('.blog-post', {
        buttons: 'auto',
        captions: true,
        async: false,
    });
});