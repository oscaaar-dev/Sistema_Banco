(() => {
  // Pide confirmación antes de enviar formularios marcados con data-confirm (p. ej. eliminar un contacto).
  document.querySelectorAll('form[data-confirm]').forEach((form) => {
    form.addEventListener('submit', (event) => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  });
})();
