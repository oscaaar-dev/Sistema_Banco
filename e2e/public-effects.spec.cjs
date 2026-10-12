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

test('las ilustraciones de las tarjetas de inicio no cubren el texto en escritorio ni móvil', async ({ page }) => {
  const cards = page.locator('.home-promo-card');
  await expect(cards).toHaveCount(4);

  for (const viewport of [{ width: 1280, height: 900 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport);
    for (const card of await cards.all()) {
      const art = await card.locator('.home-promo-art').boundingBox();
      const copy = await card.locator('.home-promo-copy').boundingBox();
      expect(art, 'la ilustración debe tener un área visible').not.toBeNull();
      expect(copy, 'el texto debe tener un área visible').not.toBeNull();
      const overlaps = art.x < copy.x + copy.width && art.x + art.width > copy.x &&
        art.y < copy.y + copy.height && art.y + art.height > copy.y;
      expect(overlaps, `ilustración y texto separados en ${viewport.width}px`).toBe(false);
    }
  }
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
  await cards.nth(0).hover();
  await expect.poll(() => cards.nth(0).evaluate((element) => getComputedStyle(element).cursor)).toBe('pointer');
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

async function signIn(page) {
  await page.context().addCookies([{
    name: 'session',
    value: authenticatedSessionCookie(),
    domain: '127.0.0.1',
    path: '/',
  }]);
}

test('la cámara es obligatoria: el aviso no se puede rechazar y confirmar sigue bloqueado', async ({ page }) => {
  await signIn(page);
  await page.goto('/operation/payment');
  const dialog = page.locator('#operationConsentDialog');
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText('Verificación con cámara obligatoria');
  await expect(dialog).toContainText('asociada a tu usuario, a la operación y a la fecha');
  await expect(dialog.getByRole('button', { name: 'Entendido, activar cámara' })).toBeVisible();
  await expect(dialog.getByRole('link', { name: 'Cancelar operación' })).toHaveAttribute('href', '/dashboard');
  await expect(page.locator('#declineCameraConsent')).toHaveCount(0);

  // Esc no cierra el aviso: no hay camino para continuar sin cámara.
  await page.keyboard.press('Escape');
  await expect(dialog).toBeVisible();
  await expect(page.locator('#cameraConsent')).toHaveValue('no');

  const submit = page.locator('#operationForm button[type="submit"]');
  await expect(submit).toBeDisabled();
});

test('"Enviar código" se habilita solo con servicio y monto y copia los datos al formulario del código', async ({ page }) => {
  await signIn(page);
  await page.goto('/operation/payment');
  // El aviso de cámara es modal: hay que aceptarlo antes de usar el formulario.
  await page.locator('#acceptCameraConsent').click();
  await expect(page.locator('#operationConsentDialog')).not.toBeVisible();
  const sendCode = page.locator('#transferCodeForm button[type="submit"]');
  await expect(sendCode).toBeDisabled();
  await page.locator('#operationForm select[name="service"]').selectOption('Agua');
  await expect(sendCode).toBeDisabled();
  await page.locator('#operationForm input[name="amount"]').fill('55.25');
  await expect(sendCode).toBeEnabled();
  await page.locator('#transferCodeForm').evaluate((form) =>
    form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
  );
  await expect(page.locator('#transferCodeForm input[name="amount"]')).toHaveValue('55.25');
  await expect(page.locator('#transferCodeForm input[name="service"]')).toHaveValue('Agua');
  await expect(page.locator('#transferCodeForm input[name="idempotency_key"]')).toHaveValue(/^[A-Za-z0-9_-]{22,64}$/);
  await page.locator('#operationForm input[name="amount"]').fill('');
  await expect(sendCode).toBeDisabled();
});

test.describe('con cámara simulada de Chromium', () => {
  test.use({ permissions: ['camera'] });

  test('aceptar el aviso activa la cámara y al confirmar se envía una sola foto JPEG', async ({ page }) => {
    await signIn(page);
    let cameraFrameRequests = 0;
    await page.route('**/camera/analyze', async (route) => {
      cameraFrameRequests += 1;
      await route.fulfill({ status: 410, contentType: 'application/json', body: '{"error":"disabled"}' });
    });
    let upload = null;
    await page.route('**/operation/payment', async (route) => {
      if (route.request().method() === 'POST') {
        upload = route.request().postDataBuffer();
        await route.fulfill({ status: 200, contentType: 'text/html', body: '<!doctype html><title>ok</title><p>recibido</p>' });
      } else {
        await route.continue();
      }
    });
    await page.goto('/operation/payment');
    await page.locator('#acceptCameraConsent').click();
    await expect(page.locator('#operationConsentDialog')).not.toBeVisible();
    await expect(page.locator('#cameraConsent')).toHaveValue('yes');
    await expect(page.locator('#cameraIndicator')).toBeVisible();

    const submit = page.locator('#operationForm button[type="submit"]');
    await expect(submit).toBeDisabled();            // falta el código por correo
    await page.locator('#operationForm select[name="service"]').selectOption('Agua');
    await page.locator('#operationForm input[name="amount"]').fill('5');
    await page.locator('#operationForm input[name="otp_code"]').fill('123456');
    await expect(submit).toBeEnabled();             // cámara activa + código

    await submit.click();
    await expect.poll(() => upload !== null).toBeTruthy();
    const body = upload.toString('latin1');
    expect(body).toContain('name="capture"');
    expect(body).toContain('filename="capture.jpg"');
    expect(upload.includes(Buffer.from([0xff, 0xd8, 0xff]))).toBeTruthy();    // firma JPEG
    expect((body.match(/name="capture"/g) || []).length).toBe(1);              // una sola foto
    expect(upload.length).toBeLessThan(300 * 1024 + 4096);
    expect(cameraFrameRequests).toBe(0);
  });

  test('si el navegador bloquea la cámara se muestra la ayuda y no se puede confirmar', async ({ page }) => {
    await signIn(page);
    await page.addInitScript(() => {
      navigator.mediaDevices.getUserMedia = () => Promise.reject(new DOMException('denied', 'NotAllowedError'));
    });
    await page.goto('/operation/payment');
    await page.locator('#acceptCameraConsent').click();
    await expect(page.locator('#cameraHelp')).toBeVisible();
    await expect(page.locator('#cameraStatus')).toContainText('Sin cámara no se puede confirmar la operación');
    await page.locator('#operationForm input[name="otp_code"]').fill('123456');
    await expect(page.locator('#operationForm button[type="submit"]')).toBeDisabled();
  });
});

test('el diálogo de cookies se abre y cierra y Nori ocupa el lugar del botón de ayuda', async ({ page }) => {
  await page.goto('/');

  const cookieDialog = page.locator('#cookieDialog');

  await page.locator('[data-open-cookies]').click();
  await expect(cookieDialog).toHaveAttribute('open', '');
  await cookieDialog.locator('[data-close-dialog]').click();
  await expect(cookieDialog).not.toHaveAttribute('open', '');

  await expect(page.locator('[data-open-help]')).toHaveCount(0);
  await expect(page.locator('#helpDialog')).toHaveCount(0);
  await expect(page.locator('.nori__mascot')).toBeVisible();
});
