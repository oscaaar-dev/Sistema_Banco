const { test, expect } = require('@playwright/test');
const { execFileSync } = require('child_process');

function authenticatedSessionCookie() {
  const script = [
    'from flask import Flask',
    'from flask.sessions import SecureCookieSessionInterface',
    "app = Flask('e2e')",
    "app.config['SECRET_KEY'] = 'playwright-test-session-only'",
    "print(SecureCookieSessionInterface().get_signing_serializer(app).dumps({'_user_id': '1', '_fresh': True, 'login_2fa_verified': True}))",
  ].join('; ');

  return execFileSync(process.env.PYTHON || 'python', ['-c', script], {
    encoding: 'utf8',
  }).trim();
}

test.beforeEach(async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('[data-hero-carousel]')).toBeVisible();
});

test('el carrusel principal avanza automáticamente', async ({ page }) => {
  const status = page.locator('.home-hero-slide-status');
  await expect(status).toHaveText('01 / 03');
  await expect(status).toHaveText('02 / 03', { timeout: 7_000 });
});

test('el slider principal responde a sus controles', async ({ page }) => {
  const track = page.locator('.home-hero-track');
  const status = page.locator('.home-hero-slide-status');

  await page.locator('[data-hero-to="2"]').click();
  await expect(status).toHaveText('03 / 03');
  await expect(track).toHaveAttribute('style', /translateX\(-200%\)/);
});

test('el parallax responde al desplazamiento y las secciones se revelan', async ({ page }) => {
  const parallax = page.locator('.home-parallax[data-parallax]').first();
  const reveal = page.locator('#promociones[data-reveal]');

  await page.evaluate(() => window.scrollTo(0, 450));
  await expect.poll(() => parallax.evaluate((element) =>
    element.style.getPropertyValue('--home-parallax-y')
  )).not.toBe('');

  await reveal.scrollIntoViewIfNeeded();
  await expect(reveal).toHaveClass(/home-is-visible/);
});

test('el parallax del inicio de sesión responde al puntero', async ({ page }) => {
  await page.goto('/login');
  const hero = page.locator('.auth-hero');
  const layer = page.locator('.auth-orbit-one[data-parallax]');
  await expect(hero).toBeVisible();
  const bounds = await hero.boundingBox();
  await page.mouse.move(bounds.x + bounds.width - 12, bounds.y + 18);
  await expect.poll(() => layer.evaluate((element) =>
    element.style.getPropertyValue('--parallax-x')
  )).not.toBe('');
});

test('el menú móvil de la intranet se abre y se cierra', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.context().addCookies([{
    name: 'session',
    value: authenticatedSessionCookie(),
    domain: '127.0.0.1',
    path: '/',
  }]);
  await page.goto('/dashboard');

  const toggle = page.locator('#menuToggle');
  const navigation = page.locator('#intranetNav');
  await expect(toggle).toBeVisible();
  await expect(navigation).not.toHaveClass(/open/);
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  await expect(navigation).toHaveClass(/open/);
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await expect(navigation).not.toHaveClass(/open/);
});

test('la cámara requiere consentimiento explícito antes de habilitarse', async ({ page }) => {
  await page.context().addCookies([{
    name: 'session',
    value: authenticatedSessionCookie(),
    domain: '127.0.0.1',
    path: '/',
  }]);
  await page.goto('/operation/payment');
  const consent = page.locator('#cameraConsent');
  const start = page.locator('#startCamera');
  await expect(consent).not.toBeChecked();
  await expect(start).toBeDisabled();
  await consent.check();
  await expect(start).toBeEnabled();
  await consent.uncheck();
  await expect(start).toBeDisabled();
});

test('los diálogos de cookies y ayuda se abren y cierran', async ({ page }) => {
  await page.goto('/');

  const cookieDialog = page.locator('#cookieDialog');
  const helpDialog = page.locator('#helpDialog');

  await page.locator('[data-open-cookies]').click();
  await expect(cookieDialog).toHaveAttribute('open', '');
  await cookieDialog.locator('[data-close-dialog]').click();
  await expect(cookieDialog).not.toHaveAttribute('open', '');

  await page.locator('[data-open-help]').click();
  await expect(helpDialog).toHaveAttribute('open', '');
  await helpDialog.locator('[data-close-dialog]').click();
  await expect(helpDialog).not.toHaveAttribute('open', '');
});
