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

test('los campos de contraseña pueden mostrarse y ocultarse con el ojito', async ({ page }) => {
  await page.goto('/register');
  for (const id of ['registerPassword', 'registerPasswordConfirmation']) {
    const input = page.locator(`#${id}`);
    const toggle = page.locator(`[data-password-toggle="${id}"]`);
    await input.fill('NorteSeguro9!');
    await toggle.click();
    await expect(input).toHaveAttribute('type', 'text');
    await expect(toggle).toHaveAttribute('aria-pressed', 'true');
    await toggle.click();
    await expect(input).toHaveAttribute('type', 'password');
    await expect(toggle).toHaveAttribute('aria-pressed', 'false');
  }

  await page.goto('/login');
  const loginPassword = page.locator('#loginPassword');
  await loginPassword.fill('NorteSeguro9!');
  await page.locator('[data-password-toggle="loginPassword"]').click();
  await expect(loginPassword).toHaveAttribute('type', 'text');
});

test('las páginas informativas muestran tarjetas interactivas y accesos claros', async ({ page }) => {
  await page.goto('/personas');
  const cards = page.locator('.info-card');
  await expect(cards).toHaveCount(3);
  const portraits = page.locator('.info-card-photo img');
  await expect(portraits).toHaveCount(3);
  await expect(page.locator('.info-note')).toHaveCount(0);
  await expect(page.locator('.info-card-photo figcaption')).toHaveCount(3);
  await expect(page.locator('.info-card-photo figcaption').first()).toContainText('Fotografía ilustrativa');
  for (const portrait of await portraits.all()) {
    await expect(portrait).toBeVisible();
    await expect.poll(() => portrait.evaluate((image) => image.complete && image.naturalWidth > 0)).toBeTruthy();
  }
  await expect(page.getByRole('link', { name: /Banca por Internet/ })).toHaveAttribute('href', '/login');
  await expect(page.getByRole('link', { name: /Abrir cuenta/ })).toHaveAttribute('href', '/register');
  await expect(cards.nth(0)).toHaveAttribute('href', '/login');
  await expect(cards.nth(1)).toHaveAttribute('href', '/acerca');
  const initialTransform = await cards.nth(0).evaluate((element) => getComputedStyle(element).transform);
  await cards.nth(0).hover();
  await expect.poll(() => cards.nth(0).evaluate((element) => getComputedStyle(element).transform)).not.toBe(initialTransform);
  await cards.nth(1).click();
  await expect(page).toHaveURL(/\/acerca$/);
  await page.getByRole('link', { name: /Abrir cuenta/ }).click();
  await expect(page).toHaveURL(/\/register$/);

  await page.goto('/herramientas');
  await expect(page.locator('.info-card')).toHaveCount(5);
  await expect(page.locator('.info-card').first()).toHaveAttribute('href', '/login');

  await page.goto('/seguridad');
  await expect(page.getByRole('link', { name: /Leer privacidad/ })).toHaveAttribute('href', '/privacidad');
  await expect(page.getByRole('link', { name: /Leer términos/ })).toHaveAttribute('href', '/terminos');

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/personas');
  await expect(page.getByRole('link', { name: /Abrir cuenta/ })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
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
  const dialog = page.locator('#operationConsentDialog');
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText('Los fotogramas no se envían ni se guardan');
  const consent = page.locator('#cameraConsent');
  const start = page.locator('#startCamera');
  await expect(consent).toHaveValue('no');
  await expect(start).toBeDisabled();
  const submit = page.locator('#operationForm button[type="submit"]');
  const code = page.locator('#operationForm input[name="otp_code"]');
  await page.locator('#declineCameraConsent').click();
  await expect(dialog).not.toBeVisible();
  await expect(consent).toHaveValue('no');
  await expect(start).toBeDisabled();
  await code.fill('123456');
  await expect(submit).toBeEnabled();
  await code.fill('');
  await expect(submit).toBeDisabled();
  await page.locator('#operationForm select[name="service"]').selectOption('Agua');
  await page.locator('#operationForm input[name="amount"]').fill('55.25');
  await page.locator('#transferCodeForm').evaluate((form) =>
    form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
  );
  await expect(page.locator('#transferCodeForm input[name="amount"]')).toHaveValue('55.25');
  await expect(page.locator('#transferCodeForm input[name="service"]')).toHaveValue('Agua');
  await expect(page.locator('#transferCodeForm input[name="idempotency_key"]')).toHaveValue(/^[A-Za-z0-9_-]{22,64}$/);
});

test('la aceptación de cámara queda registrada y no se envían fotogramas', async ({ page }) => {
  await page.context().addCookies([{
    name: 'session',
    value: authenticatedSessionCookie(),
    domain: '127.0.0.1',
    path: '/',
  }]);
  let cameraFrameRequests = 0;
  await page.route('**/camera/analyze', async (route) => {
    cameraFrameRequests += 1;
    await route.fulfill({ status: 410, contentType: 'application/json', body: '{"error":"disabled"}' });
  });
  await page.goto('/operation/payment');
  await page.locator('#acceptCameraConsent').click();
  await expect(page.locator('#operationConsentDialog')).not.toBeVisible();
  await expect(page.locator('#cameraConsent')).toHaveValue('yes');
  expect(cameraFrameRequests).toBe(0);
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
