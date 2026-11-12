// blog/static/js/prose_editor_media.js
(function () {
  'use strict';

  function escapeAttr(value) {
    return String(value || '').replace(/&/g, '&amp;').replace(/"/g, '&quot;');
  }

  function insertTextIntoEditor(editor, text) {
    if (!editor) return;
    editor.chain().focus().insertContent(text).run();
  }

  function extractVkParams(raw) {
    if (!raw) return null;

    var src = raw;

    // iframe → достаём src
    var iframeMatch = raw.match(/<iframe[^>]+src=["']([^"']+)["']/i);
    if (iframeMatch) {
      src = iframeMatch[1];
    }

    src = src.replace(/&amp;/g, '&').trim();

    // video_ext.php или clip_ext.php
    var isClip = src.indexOf('clip_ext.php') !== -1;
    var isVideo = src.indexOf('video_ext.php') !== -1;

    if (!isClip && !isVideo) return null;

    var oid, id, hash;

    try {
      var url = new URL(src);
      oid = url.searchParams.get('oid');
      id = url.searchParams.get('id');
      hash = url.searchParams.get('hash') || '';
    } catch (e) {
      var oidM = src.match(/oid=(-?\d+)/);
      var idM = src.match(/[&?]id=(\d+)/);
      var hashM = src.match(/hash=([A-Za-z0-9_-]+)/);
      oid = oidM ? oidM[1] : null;
      id = idM ? idM[1] : null;
      hash = hashM ? hashM[1] : '';
    }

    if (!oid || !id) return null;

    // Определяем пропорции из width/height iframe
    var ratio = '';
    if (iframeMatch) {
      var wMatch = raw.match(/width=["']?(\d+)/i);
      var hMatch = raw.match(/height=["']?(\d+)/i);
      if (wMatch && hMatch) {
        var w = parseInt(wMatch[1], 10);
        var h = parseInt(hMatch[1], 10);
        if (h > w * 1.2) {
          ratio = '9x16';
        }
      }
    }

    return {
      oid: oid,
      id: id,
      hash: hash,
      type: isClip ? 'clip' : 'video',
      ratio: ratio
    };
  }

  function isLocalVideoUrl(url) {
    if (!url) return false;
    var lower = url.toLowerCase().trim();
    return lower.endsWith('.mp4') || lower.endsWith('.webm') || lower.endsWith('.m4v');
  }

  function handleInsertVideo(editor) {
    var raw = prompt(
      'Вставьте:\n' +
      '• iframe-код видео VK (video или clip)\n' +
      '• или ссылку video_ext.php / clip_ext.php\n' +
      '• или путь к локальному видео (.mp4)\n\n' +
      'Примеры:\n' +
      '/media/videos/trip.mp4\n' +
      'https://vk.com/video_ext.php?oid=...&id=...&hash=...\n' +
      'https://vk.com/clip_ext.php?oid=...&id=...'
    );

    if (!raw || !raw.trim()) return;

    raw = raw.trim();

    // 1. VK
    var vk = extractVkParams(raw);
    if (vk) {
      var sc = '[vk_video oid="' + escapeAttr(vk.oid) +
               '" id="' + escapeAttr(vk.id) + '"';

      if (vk.hash) {
        sc += ' hash="' + escapeAttr(vk.hash) + '"';
      }
      if (vk.type === 'clip') {
        sc += ' type="clip"';
      }
      if (vk.ratio && vk.ratio !== '16x9') {
        sc += ' ratio="' + escapeAttr(vk.ratio) + '"';
      }

      sc += ']';
      insertTextIntoEditor(editor, sc);
      return;
    }

    // 2. Локальное видео
    if (isLocalVideoUrl(raw)) {
      var poster = prompt('Ссылка на постер (Enter — пропустить):', '') || '';
      var caption = prompt('Подпись к видео (Enter — пропустить):', '') || '';

      var shortcode = '[video src="' + escapeAttr(raw.trim()) + '"';
      if (poster.trim()) {
        shortcode += ' poster="' + escapeAttr(poster.trim()) + '"';
      }
      if (caption.trim()) {
        shortcode += ' caption="' + escapeAttr(caption.trim()) + '"';
      }
      shortcode += ']';

      insertTextIntoEditor(editor, shortcode);
      return;
    }

    // 3. Не распознали
    alert(
      'Не удалось распознать ссылку.\n\n' +
      'Для VK нужен iframe-код или ссылка video_ext.php / clip_ext.php.\n' +
      'Для локального видео — путь к .mp4 файлу.'
    );
  }

  function addVideoButton(wrapper) {
    if (wrapper.querySelector('.prose-menubar__button--media-embed')) return;

    var menubar = wrapper.querySelector('.prose-menubar');
    if (!menubar) return;

    var btn = document.createElement('span');
    btn.className = 'prose-menubar__button material-icons prose-menubar__button--media-embed';
    btn.textContent = 'movie';
    btn.title = 'Вставить видео';
    btn.style.cursor = 'pointer';

    btn.addEventListener('click', function () {
      var tiptap = wrapper.querySelector('.tiptap');
      var editor = tiptap && tiptap.editor;
      if (!editor) {
        alert('Редактор не найден.');
        return;
      }
      handleInsertVideo(editor);
    });

    menubar.appendChild(btn);
  }

  document.addEventListener('DOMContentLoaded', function () {
    var observer = new MutationObserver(function () {
      document.querySelectorAll('.prose-editor').forEach(addVideoButton);
    });

    observer.observe(document.body, { childList: true, subtree: true });
    document.querySelectorAll('.prose-editor').forEach(addVideoButton);
  });
})();