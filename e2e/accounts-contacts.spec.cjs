const { test, expect } = require('@playwright/test');
const { execFileSync } = require('child_process');

function authenticatedSessionCookie() {
  const script = [
    'from flask import Flask',
    'from flask.sessions import SecureCookieSessionInterface',
    "app = Flask('e2e-accounts')",
    "app.config['SECRET_KEY'] = 'playwright-test-session-only'",
    "print(SecureCookieSessionInterface().get_signing_serializer(app).dumps({'_user_id': '1', '_fresh': True, 'login_2fa_verified': True}))",
  ].join('; ');

  return execFileSync(process.env.PYTHON || 'python', ['-c', script], {
    encoding: 'utf8',
  }).trim();
}

test.beforeEach(async ({ page }) => {
  await page.context().addCookies([{
    name: 'session',
    value: authenticatedSessionCookie(),
    domain: '127.0.0.1',
    path: '/',
  }]);
});

test('la pantalla de cuentas oculta y muestra el saldo y ofrece el CCI ficticio', async ({ page }) => {
  await page.goto('/accounts');
  await expect(page.getByRole('heading', { name: 'Cuentas', level: 1 })).toBeVisible();

  const balance = page.locator('#acBalance');
  const toggle = page.locator('#acBalanceToggle');
  await expect(balance).toHaveText('8,540.50');

  await toggle.click();
  await expect(balance).toHaveText('•••••');
  await expect(toggle).toHaveAttribute('aria-pressed', 'true');

  await toggle.click();
  await expect(balance).toHaveText('8,540.50');

  await expect(page.locator('#acCci')).toHaveText(/^\d{20}$/);
  await expect(page.getByText('ficticio, solo demostración')).toBeVisible();
  await expect(page.getByRole('link', { name: /Descargar estado de cuenta/ })).toHaveAttribute('href', /\/statements\/1\.pdf/);
});

test('la pantalla de cuentas no desborda en móvil', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 });
  await page.goto('/accounts');
  await expect(page.locator('.ac-card')).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  const tableOverflow = await page.locator('.ac-recent').evaluate((element) => element.scrollWidth - element.clientWidth);
  expect(tableOverflow).toBeLessThanOrEqual(0);
});

test('agregar contacto muestra mensajes claros sin revelar de más', async ({ page }) => {
  await page.goto('/operation/transfer');
  // El aviso de la cámara es un diálogo modal: se acepta antes de usar el resto de la página.
  await page.locator('#acceptCameraConsent').click();
  await expect(page.locator('dialog[open]')).toHaveCount(0);
  await page.getByRole('link', { name: /Agregar contacto/ }).click();
  await expect(page.getByRole('heading', { name: 'Agregar contacto' })).toBeVisible();

  await page.getByLabel(/DNI o número de cuenta/).fill('99999999');
  await page.getByRole('button', { name: /Buscar cliente/ }).click();
  await expect(page.locator('.flash.error')).toContainText('No pudimos encontrar un cliente de Norte con esos datos');

  await page.getByLabel(/DNI o número de cuenta/).fill('001234567890');
  await page.getByRole('button', { name: /Buscar cliente/ }).click();
  await expect(page.locator('.flash.error')).toContainText('tus propias cuentas');

  await page.getByLabel(/DNI o número de cuenta/).fill('abc');
  await page.getByRole('button', { name: /Buscar cliente/ }).click();
  await expect(page.locator('.flash.error')).toContainText('solo con números');
});

test('los contactos guardados se pueden eliminar desde la web', async ({ page }) => {
  await page.goto('/contacts');
  const items = page.locator('.ct-list li');
  const before = await items.count();
  expect(before).toBeGreaterThan(0);

  page.once('dialog', (dialog) => dialog.accept());
  await items.first().getByRole('button', { name: /Eliminar/ }).click();
  await expect(page.locator('.flash.success')).toContainText('de tus contactos');
  await expect(page.locator('.ct-list li')).toHaveCount(before - 1);
});
