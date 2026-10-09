(() => {
  const operationForm = document.getElementById('operationForm');
  const codeForm = document.getElementById('transferCodeForm');
  if (!operationForm || !codeForm) return;

  const syncTransferDetails = () => {
    for (const name of ['beneficiary_id', 'amount', 'idempotency_key']) {
      const source = operationForm.elements.namedItem(name);
      const target = codeForm.elements.namedItem(name);
      if (source && target) target.value = source.value;
    }
  };

  operationForm.addEventListener('input', syncTransferDetails);
  operationForm.addEventListener('change', syncTransferDetails);
  codeForm.addEventListener('submit', syncTransferDetails);
  syncTransferDetails();
})();
