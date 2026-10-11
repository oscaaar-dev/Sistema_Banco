const { test, expect } = require('@playwright/test');

const ruleStates = (page, form = 'form') =>
  page.locator(`${form} .pw-rules li`).evaluateAll((items) =>
    Object.fromEntries(items.map((item) => [item.dataset.rule,
      item.classList.contains('is-ok') ? 'ok' : item.classList.contains('is-bad') ? 'bad' : 'idle'])));

test('el registro muestra requisitos de contraseña que se actualizan al escribir', async ({ page }) => {
  await page.goto('/register');
  const password = page.locator('#registerPassword');
  const confirmation = page.locator('#registerPasswordConfirmation');

  // Antes de escribir no hay errores ni aciertos.
  expect(Object.values(await ruleStates(page))).toEqual(['idle', 'idle', 'idle', 'idle', 'idle']);
  await expect(page.locator('[data-pw-match]')).toBeHidden();

  await password.fill('Norte20');
  expect(await ruleStates(page)).toMatchObject({ upper: 'ok', lower: 'ok', digit: 'ok', length: 'idle', dot: 'idle' });

  await password.fill('Norte2026.');
  expect(Object.values(await ruleStates(page))).toEqual(['ok', 'ok', 'ok', 'ok', 'ok']);

  await password.fill('Norte20261.');
  expect(await ruleStates(page)).toMatchObject({ length: 'bad', dot: 'ok' });
  expect(await password.evaluate((input) => input.hasAttribute('maxlength'))).toBe(false);

  await password.fill('Norte2026.');
  await confirmation.fill('Norte2027.');
  await expect(page.locator('[data-pw-match]')).toContainText('no coinciden');
  await confirmation.fill('Norte2026.');
  await expect(page.locator('[data-pw-match]')).toContainText('coinciden');

  const toggle = page.locator('[data-password-toggle="registerPassword"]');
  await toggle.click();
  await expect(password).toHaveAttribute('type', 'text');
  await toggle.click();
  await expect(password).toHaveAttribute('type', 'password');
});

test('el registro no envía el formulario si no se cumple la política', async ({ page }) => {
  await page.goto('/register');
  await page.locator('input[name="full_name"]').fill('Cliente Prueba');
  await page.locator('input[name="dni"]').fill('45678912');
  await page.locator('input[name="email"]').fill('sin-punto@example.test');
  await page.locator('#registerPassword').fill('Norte2026a');
  await page.locator('#registerPasswordConfirmation').fill('Norte2026a');
  await page.getByRole('button', { name: /Crear cuenta/ }).click();
  await expect(page).toHaveURL(/\/register$/);
  expect(await ruleStates(page)).toMatchObject({ dot: 'bad' });
});

test('el inicio de sesión enlaza a la recuperación y la respuesta pública es genérica', async ({ page }) => {
  await page.goto('/login');
  await page.getByRole('link', { name: '¿Olvidaste tu contraseña?' }).click();
  await expect(page).toHaveURL(/\/recuperar$/);

  await page.locator('input[name="email"]').fill('no-es-un-correo');
  await page.getByRole('button', { name: /Enviar enlace/ }).click();
  await expect(page.locator('.flash.error')).toContainText('correo electrónico válida');

  await page.locator('input[name="email"]').fill('nadie@example.test');
  await page.getByRole('button', { name: /Enviar enlace/ }).click();
  await expect(page.locator('.flash.info')).toContainText('Si el correo corresponde a una cuenta de cliente');
  await expect(page.locator('input[name="email"]')).toHaveCount(0);
});

test('un enlace de recuperación inválido no muestra el formulario', async ({ page }) => {
  const response = await page.goto('/recuperar/' + 'a'.repeat(43));
  expect(response.status()).toBe(400);
  await expect(page.getByRole('heading', { name: 'No pudimos validar el enlace' })).toBeVisible();
  await expect(page.locator('#resetPassword')).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
});
