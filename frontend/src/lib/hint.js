/**
 * `use:hint={'where this number comes from'}` — a tooltip that explains a value.
 *
 * The popup is appended to <body> rather than next to the element, because most of our
 * panels live inside scrolling containers that would clip it. Shows on hover and on
 * keyboard focus, and makes the element focusable if it isn't already.
 */
const FOCUSABLE = /^(a|button|input|select|textarea|summary)$/i;

export function hint(node, text) {
  let el = null;
  let id = null;

  function show() {
    if (!text || el) return;
    el = document.createElement('div');
    el.className = 't6-hint';
    el.setAttribute('role', 'tooltip');
    el.textContent = text; // textContent, never innerHTML
    id = `hint-${Math.random().toString(36).slice(2, 8)}`;
    el.id = id;
    document.body.appendChild(el);
    node.setAttribute('aria-describedby', id);

    const r = node.getBoundingClientRect();
    const w = el.offsetWidth;
    const h = el.offsetHeight;
    const above = r.top - h - 8;
    el.style.top = `${above < 8 ? Math.min(r.bottom + 8, window.innerHeight - h - 8) : above}px`;
    el.style.left = `${Math.max(8, Math.min(window.innerWidth - w - 8, r.left + r.width / 2 - w / 2))}px`;
    el.style.opacity = '1';
  }

  function hide() {
    el?.remove();
    el = null;
    node.removeAttribute('aria-describedby');
  }

  if (!FOCUSABLE.test(node.tagName) && !node.hasAttribute('tabindex')) node.tabIndex = 0;
  const on = [
    ['mouseenter', show],
    ['mouseleave', hide],
    ['focus', show],
    ['blur', hide],
  ];
  for (const [ev, fn] of on) node.addEventListener(ev, fn);
  const onKey = (e) => e.key === 'Escape' && hide();
  node.addEventListener('keydown', onKey);

  return {
    update(next) {
      text = next;
      if (el) {
        hide();
        show();
      }
    },
    destroy() {
      hide();
      for (const [ev, fn] of on) node.removeEventListener(ev, fn);
      node.removeEventListener('keydown', onKey);
    },
  };
}
