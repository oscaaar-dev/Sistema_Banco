const { test, expect } = require('@playwright/test');
const { execFileSync } = require('child_process');
const fs = require('fs');

const captureDir = 'C:\\Users\\hola\\.codex\\visualizations\\2026\\10\\07\\01a1179a-7f9a-71d1-8cca-48632c2f9aaa\\norte-audit\\current';

function authenticatedSessionCookie() {
  const script = [
    'from flask import Flask',
    'from flask.sessions import SecureCookieSessionInterface',
    "app = Flask('visual-capture')",
    "app.config['SECRET_KEY'] = 'playwright-test-session-only'",
    "print(SecureCookieSessionInterface().get_signing_serializer(app).dumps({'_user_id': '1', '_fresh': True, 'login_2fa_verified': True}))",
  ].join('; ');

  return execFileSync(process.env.PYTHON || 'python', ['-c', script], {
    encoding: 'utf8',
  }).trim();
}

async function pauseHero(page) {
  const toggle = page.locator('[data-hero-toggle]');
  if (await toggle.getAttribute('aria-pressed') === 'false') {
    await toggle.evaluate((element) => element.click());
  }
}

async function resetScroll(page) {
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}

test('captura portada, login y dashboard en escritorio y móvil', async ({ page }) => {
  fs.mkdirSync(captureDir, { recursive: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto('/');
  await expect(page.locator('[data-hero-carousel]')).toBeVisible();
  await resetScroll(page);
  await pauseHero(page);
  await resetScroll(page);
  await page.screenshot({ path: `${captureDir}\\home-desktop.png` });

  for (const section of ['personas', 'herramientas', 'seguridad', 'acerca']) {
    await page.goto(`/${section}`);
    await expect(page.locator('.info-card').first()).toBeVisible();
    await page.waitForTimeout(500);
    await resetScroll(page);
    await page.screenshot({ path: `${captureDir}\\info-${section}-desktop.png` });
  }

  await page.goto('/login');
  await expect(page.locator('.auth-panel')).toBeVisible();
  await resetScroll(page);
  await page.screenshot({ path: `${captureDir}\\login-desktop.png` });

  await page.context().addCookies([{
    name: 'session',
    value: authenticatedSessionCookie(),
    domain: '127.0.0.1',
    path: '/',
  }]);
  await page.goto('/dashboard');
  await expect(page.locator('.welcome-row')).toBeVisible();
  await resetScroll(page);
  await page.screenshot({ path: `${captureDir}\\dashboard-desktop.png` });

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await expect(page.locator('[data-hero-carousel]')).toBeVisible();
  await resetScroll(page);
  await pauseHero(page);
  await resetScroll(page);
  await page.screenshot({ path: `${captureDir}\\home-mobile.png` });

  for (const section of ['personas', 'herramientas', 'seguridad', 'acerca']) {
    await page.goto(`/${section}`);
    await expect(page.locator('.info-card').first()).toBeVisible();
    await page.waitForTimeout(500);
    await resetScroll(page);
    await page.screenshot({ path: `${captureDir}\\info-${section}-mobile.png` });
  }

  await page.context().clearCookies();
  await page.goto('/login');
  await expect(page.locator('.auth-panel')).toBeVisible();
  await resetScroll(page);
  await page.screenshot({ path: `${captureDir}\\login-mobile.png` });

  await page.context().addCookies([{
    name: 'session',
    value: authenticatedSessionCookie(),
    domain: '127.0.0.1',
    path: '/',
  }]);
  await page.goto('/dashboard');
  await expect(page.locator('.welcome-row')).toBeVisible();
  await resetScroll(page);
  await page.screenshot({ path: `${captureDir}\\dashboard-mobile.png` });
});
