/* Лендинг: метки рекламы (utm_*, yclid и др.) переносятся в ссылки на вход/регистрацию; цели Яндекс.Метрики — по клику на кнопки. */
'use strict';
(function () {
  const KEEP = /^(utm_[a-z_]+|yclid|gclid|fbclid|vkclid|ref|from)$/i;
  const q = new URLSearchParams(location.search), keep = new URLSearchParams();
  q.forEach((v, k) => { if (KEEP.test(k)) keep.set(k, v.slice(0, 200)); });
  const tail = keep.toString() ? '?' + keep.toString() : '';
  document.querySelectorAll('a[data-cta]').forEach(a => { a.href = '/' + tail + '#register'; });
  document.querySelectorAll('a[data-login]').forEach(a => { a.href = '/' + tail; });

  const id = (window.P24_LANDING || {}).metrika;
  const goal = name => { if (id && window.ym) window.ym(id, 'reachGoal', name); };
  if (id) {
    window.ym = window.ym || function () { (window.ym.a = window.ym.a || []).push(arguments); };
    window.ym.l = +new Date();
    const s = document.createElement('script'); s.async = true; s.src = 'https://mc.yandex.ru/metrika/tag.js'; document.head.append(s);
    window.ym(id, 'init', { clickmap: true, trackLinks: true, accurateTrackBounce: true, webvisor: false });
  }
  document.addEventListener('click', e => {
    const a = e.target.closest('a[data-cta]'); if (a) goal('cta_click');
    const l = e.target.closest('a[data-login]'); if (l) goal('login_click');
  });
  let scrolled = false;
  addEventListener('scroll', () => { if (!scrolled && scrollY > innerHeight * 0.6) { scrolled = true; goal('scroll_60'); } }, { passive: true });
})();
