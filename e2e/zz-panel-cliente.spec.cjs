const { test, expect } = require('@playwright/test');
const { execFileSync } = require('child_process');

// Estas pruebas modifican la cuenta de muestra (depósitos, cuentas y tarjetas nuevas) y todas las especificaciones comparten
// la misma base en memoria. Playwright ejecuta los archivos por orden alfabético: el prefijo «zz-» hace que esta vaya la
// última y no altere los saldos y cuentas que esperan las demás. Se ejecutan en serie porque cada una parte del estado anterior.
test.describe.configure({ mode: 'serial' });

function authenticatedSessionCookie() {
  const script = [
    'from flask import Flask',
    'from flask.sessions import SecureCookieSessionInterface',
    "app = Flask('e2e-panel')",
    "app.config['SECRET_KEY'] = 'playwright-test-session-only'",
    "print(SecureCookieSessionInterface().get_signing_serializer(app).dumps({'_user_id': '1', '_fresh': True, 'login_2fa_verified': True}))",
  ].join('; ');
  return execFileSync(process.env.PYTHON || 'python', ['-c', script], { encoding: 'utf8' }).trim();
}

test.beforeEach(async ({ page }) => {
  await page.context().addCookies([{ name: 'session', value: authenticatedSessionCookie(), domain: '127.0.0.1', path: '/' }]);
});

const amountOf = async (card) => Number((await card.locator('.balance-amount').innerText()).replace(/[^0-9.]/g, ''));

test('el resumen del panel muestra el saldo total y los totales del mes sin contar traslados', async ({ page }) => {
  await page.goto('/dashboard');
  const summary = page.locator('.dash-summary');
  await expect(summary).toBeVisible();
  await expect(summary).toContainText('Saldo total');
  await expect(summary).toContainText('Ingresos del mes');
  await expect(summary).toContainText('Egresos del mes');
  await expect(summary).toContainText('No incluye traslados entre tus cuentas');
  await expect(page.getByRole('link', { name: /Estado de cuenta/ }).first()).toHaveAttribute('href', /\/statements\/\d+\.pdf/);
});

test('depositar desde el panel suma al saldo y abre el comprobante', async ({ page }) => {
  await page.goto('/dashboard');
  const account = page.locator('.balance-card').first();
  const before = await amountOf(account);
  await account.locator('summary', { hasText: 'Depositar' }).click();
  await account.getByLabel('Monto (S/)').fill('250.00');
  await account.getByRole('button', { name: 'Depositar', exact: true }).click();
  await expect(page).toHaveURL(/\/receipt\/\d+$/);
  await expect(page.getByText('Depósito de demostración').first()).toBeVisible();
  await page.goto('/dashboard');
  expect(await amountOf(page.locator('.balance-card').first())).toBeCloseTo(before + 250, 2);
});

test('un depósito fuera de rango se rechaza con un mensaje claro y no cambia el saldo', async ({ page }) => {
  await page.goto('/dashboard');
  const account = page.locator('.balance-card').first();
  const before = await amountOf(account);
  await account.locator('summary', { hasText: 'Depositar' }).click();
  await account.getByLabel('Monto (S/)').evaluate((input) => { input.removeAttribute('max'); input.value = '5000.01'; });   // salta el límite del navegador
  await account.getByRole('button', { name: 'Depositar', exact: true }).click();
  await expect(page.getByText('El depósito debe estar entre S/ 0.01 y S/ 5,000.00.')).toBeVisible();
  expect(await amountOf(page.locator('.balance-card').first())).toBeCloseTo(before, 2);
});

test('se pueden abrir cuentas nuevas hasta el máximo y el botón desaparece', async ({ page }) => {
  await page.goto('/dashboard');
  const open = page.getByRole('button', { name: /Abrir otra cuenta de ahorros/ });
  while (await open.count()) {
    const count = await page.locator('.balance-card').count();
    await open.click();
    await expect(page.getByText('Abrimos una nueva cuenta de ahorros de demostración')).toBeVisible();
    await expect(page.locator('.balance-card')).toHaveCount(count + 1);
  }
  await expect(page.locator('.balance-card')).toHaveCount(3);
  await expect(open).toHaveCount(0);
});

test('se solicita una tarjeta para una cuenta sin tarjeta y se bloquea y desbloquea desde el panel', async ({ page }) => {
  await page.goto('/dashboard');
  const request = page.getByRole('button', { name: 'Solicitar tarjeta de débito' }).first();
  const cards = await page.locator('.mini-card').count();
  await request.click();
  await expect(page.getByText('Tu tarjeta de débito de muestra ya está activa.')).toBeVisible();
  await expect(page.locator('.mini-card')).toHaveCount(cards + 1);
  await page.getByRole('button', { name: 'Bloquear tarjeta' }).last().click();             // la tarjeta nueva es la última
  await expect(page).toHaveURL(/\/dashboard#tarjetas$/);
  await expect(page.getByText('Tarjeta bloqueada.')).toBeVisible();
  await expect(page.locator('.mini-card.is-locked')).toHaveCount(1);
  await page.getByRole('button', { name: 'Desbloquear tarjeta' }).click();
  await expect(page.getByText('Tarjeta desbloqueada.')).toBeVisible();
  await expect(page.locator('.mini-card.is-locked')).toHaveCount(0);
});

test('el panel no desborda en móvil con tres cuentas, tarjetas y formularios', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 });
  await page.goto('/dashboard');
  await page.locator('.balance-card').first().locator('summary', { hasText: 'Depositar' }).click();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});
