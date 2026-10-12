(() => {
  // Ocultar / mostrar el saldo de la tarjeta de cuenta.
  const balance = document.getElementById('acBalance');
  const toggle = document.getElementById('acBalanceToggle');
  if (balance && toggle) {
    const label = toggle.querySelector('span');
    const icon = toggle.querySelector('i');
    toggle.addEventListener('click', () => {
      const hidden = toggle.getAttribute('aria-pressed') !== 'true';
      balance.textContent = hidden ? '•••••' : balance.dataset.value;
      toggle.setAttribute('aria-pressed', String(hidden));
      toggle.setAttribute('aria-label', hidden ? 'Mostrar saldo' : 'Ocultar saldo');
      if (label) label.textContent = hidden ? 'Mostrar saldo' : 'Ocultar saldo';
      if (icon) icon.className = hidden ? 'bi bi-eye-slash' : 'bi bi-eye';
    });
  }

  // Copiar número de cuenta y CCI al portapapeles, con respaldo si el navegador no expone la API.
  const status = document.getElementById('acCopyStatus');
  const fallbackCopy = (text) => {
    const area = document.createElement('textarea');
    area.value = text;
    area.setAttribute('readonly', '');
    area.style.cssText = 'position:fixed;top:0;left:0;opacity:0';
    document.body.appendChild(area);
    area.select();
    let copied = false;
    try { copied = document.execCommand('copy'); } catch (error) { copied = false; }
    area.remove();
    return copied;
  };
  document.querySelectorAll('[data-copy]').forEach((button) => {
    button.addEventListener('click', async () => {
      const value = button.dataset.copy;
      let copied = false;
      try {
        if (navigator.clipboard && window.isSecureContext) {
          await navigator.clipboard.writeText(value);
          copied = true;
        }
      } catch (error) { copied = false; }
      if (!copied) copied = fallbackCopy(value);
      const icon = button.querySelector('i');
      const text = button.querySelector('span');
      if (copied && icon && text) {
        icon.className = 'bi bi-check2';
        text.textContent = 'Copiado';
        window.setTimeout(() => { icon.className = 'bi bi-copy'; text.textContent = 'Copiar'; }, 1800);
      }
      if (status) {
        status.textContent = copied
          ? `Copiaste el ${button.dataset.copyLabel}.`
          : `No se pudo copiar automáticamente. Selecciona el ${button.dataset.copyLabel} y cópialo manualmente.`;
      }
    });
  });
})();
