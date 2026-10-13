/*
 * NORTE · Centro de control: comportamiento mínimo del área administrativa.
 * - Menú lateral en móvil: se abre y cierra con ratón, teclado (Esc) y lector de pantalla; el foco se gestiona y el contenido queda inerte mientras está abierto.
 * - Tablas apiladas en pantallas estrechas: copia el texto de cada cabecera en `data-label` de sus celdas.
 * Sin dependencias. Sin JavaScript la navegación y las tablas siguen funcionando (menú visible en escritorio, tablas con desplazamiento horizontal).
 */
(() => {
  const app = document.querySelector('[data-adm-app]');
  if (!app) return;

  const sidebar = app.querySelector('[data-adm-sidebar]');
  const toggle = app.querySelector('[data-adm-toggle]');
  const overlay = app.querySelector('[data-adm-overlay]');
  const main = app.querySelector('main');
  const mobile = window.matchMedia('(max-width: 960px)');

  if (sidebar && toggle) {
    const focusables = () => Array.from(sidebar.querySelectorAll('a[href], button:not([disabled])'));

    const setOpen = (open, { restoreFocus = true } = {}) => {
      sidebar.classList.toggle('is-open', open);
      if (overlay) overlay.classList.toggle('is-open', open);
      toggle.setAttribute('aria-expanded', String(open));
      toggle.setAttribute('aria-label', open ? 'Cerrar menú de navegación' : 'Abrir menú de navegación');
      if (main) main.inert = open && mobile.matches;
      document.documentElement.style.overflow = open && mobile.matches ? 'hidden' : '';
      if (open) {
        const first = focusables()[0];
        if (first) first.focus();
      } else if (restoreFocus && mobile.matches) {
        toggle.focus();
      }
    };

    toggle.addEventListener('click', () => setOpen(!sidebar.classList.contains('is-open')));
    if (overlay) overlay.addEventListener('click', () => setOpen(false));

    document.addEventListener('keydown', event => {
      if (!sidebar.classList.contains('is-open') || !mobile.matches) return;
      if (event.key === 'Escape') {
        event.preventDefault();
        setOpen(false);
        return;
      }
      if (event.key === 'Tab') {                       // el foco no se escapa del menú abierto
        const items = focusables();
        if (!items.length) return;
        const first = items[0];
        const last = items[items.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    });

    const onViewportChange = () => { if (!mobile.matches) setOpen(false, { restoreFocus: false }); };
    if (mobile.addEventListener) mobile.addEventListener('change', onViewportChange);
    else mobile.addListener(onViewportChange);
  }

  document.querySelectorAll('.table-wrap > table').forEach(table => {
    const headings = Array.from(table.querySelectorAll('thead th')).map(th => th.textContent.trim());
    if (!headings.length) return;
    table.querySelectorAll('tbody tr').forEach(row => {
      Array.from(row.children).forEach((cell, index) => {
        if (cell.colSpan > 1) return;                   // filas «vacío»: ocupan todo el ancho
        if (headings[index]) cell.setAttribute('data-label', headings[index]);
      });
    });
    table.parentElement.classList.add('is-stacked');
  });
})();
