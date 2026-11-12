// Вынесенный скрипт копирования URL
document.addEventListener('DOMContentLoaded', function() {
    const copyButtons = document.querySelectorAll('.copy-url-btn');

    copyButtons.forEach(button => {
        button.addEventListener('click', function() {
            const url = this.dataset.url;

            navigator.clipboard.writeText(url).then(() => {
                showCopyFeedback(this);
            }).catch(() => {
                // Fallback для старых браузеров
                fallbackCopyTextToClipboard(url, this);
            });
        });
    });

    function showCopyFeedback(button) {
        const icon = button.querySelector('i');
        const originalClass = icon.className;
        icon.className = 'fas fa-check';
        setTimeout(() => {
            icon.className = originalClass;
        }, 2000);
    }

    function fallbackCopyTextToClipboard(text, button) {
        const textArea = document.createElement('textarea');
        textArea.value = text;
        textArea.style.position = 'fixed';
        textArea.style.left = '-999999px';
        document.body.appendChild(textArea);
        textArea.select();
        document.execCommand('copy');
        document.body.removeChild(textArea);
        showCopyFeedback(button);
    }
});