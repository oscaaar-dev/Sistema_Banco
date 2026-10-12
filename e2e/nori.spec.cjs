const { test, expect } = require('@playwright/test');

const overlap = (a, b) => !(a.x + a.width <= b.x || b.x + b.width <= a.x || a.y + a.height <= b.y || b.y + b.height <= a.y);

test('Nori aparece en las páginas públicas y no en acceso ni registro', async ({ page }) => {
  for (const path of ['/', '/personas', '/seguridad', '/privacidad']) {
    await page.goto(path);
    await expect(page.locator('#nori')).toBeVisible();
    await expect(page.locator('.nori__mascot')).toBeVisible();
  }
  for (const path of ['/login', '/register', '/recuperar']) {
    await page.goto(path);
    await expect(page.locator('#nori')).toHaveCount(0);
  }
});

test('Nori se abre, conversa, enlaza solo páginas de Norte y pide el token CSRF al abrir', async ({ page }) => {
  const requests = [];
  page.on('request', (request) => requests.push(request.url()));
  await page.goto('/personas');
  await expect(page.locator('.nori__mascot')).toBeVisible();
  expect(requests.filter((url) => url.includes('/api/nori/csrf'))).toHaveLength(0);   // no se pide al cargar la página

  await page.locator('.nori__mascot').click();
  await expect(page.locator('#nori-panel')).toBeVisible();
  await expect(page.locator('.nori-msg--bot').first()).toContainText('Soy Nori', { timeout: 6000 });
  await expect.poll(() => requests.filter((url) => url.includes('/api/nori/csrf')).length).toBe(1);

  await page.getByLabel('Tu mensaje para Nori').fill('¿Cómo ingreso?');
  await page.getByRole('button', { name: 'Enviar' }).click();
  await expect(page.locator('.nori-msg--user').last()).toHaveText('¿Cómo ingreso?');
  await expect(page.locator('.nori-msg--bot').last()).toContainText('Banca por Internet', { timeout: 8000 });
  const link = page.locator('.nori-chip--link').first();
  await expect(link).toHaveAttribute('href', '/login');

  // el servicio no llama a ninguna IA ni servicio externo
  const outside = requests.filter((url) => /anthropic|openai|api\.claude/i.test(url));
  expect(outside).toEqual([]);
  expect(requests.filter((url) => url.includes('/api/nori/chat'))).toHaveLength(1);
});

test('una pregunta sin respuesta se reconoce y orienta a las páginas oficiales', async ({ page }) => {
  await page.goto('/herramientas');
  await page.locator('.nori__mascot').click();
  await page.getByLabel('Tu mensaje para Nori').fill('blablabla xyzzy');
  await page.getByRole('button', { name: 'Enviar' }).click();
  await expect(page.locator('.nori-msg--bot').last()).toContainText('No encontré información suficiente', { timeout: 8000 });
  await expect(page.locator('.nori-chip--link')).toHaveCount(4);
});

test('los datos sensibles se ocultan en el chat y no se recuerdan', async ({ page }) => {
  await page.goto('/acerca');
  await page.locator('.nori__mascot').click();
  await page.getByLabel('Tu mensaje para Nori').fill('mi DNI es 12345678');
  await page.getByRole('button', { name: 'Enviar' }).click();
  await expect(page.locator('.nori-msg--user').last()).toHaveText('Mensaje oculto por contener datos sensibles.', { timeout: 8000 });
  await expect(page.locator('.nori-msg--bot').last()).toContainText('no escribas aquí DNI');
  await expect(page.locator('#nori')).not.toContainText('12345678');
});

test('en la portada Nori ocupa la esquina inferior derecha, sin botón de ayuda y sin tapar el de privacidad', async ({ page }) => {
  for (const viewport of [{ width: 1280, height: 900 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport);
    await page.goto('/');
    await expect(page.locator('.nori__mascot')).toBeVisible();
    await expect(page.locator('[data-open-help]')).toHaveCount(0);
    await page.waitForTimeout(900);                                  // termina la animación de entrada
    const nori = await page.locator('.nori__mascot').boundingBox();
    const cookies = await page.locator('[data-open-cookies]').boundingBox();
    expect(overlap(nori, cookies), `privacidad a ${viewport.width}px`).toBe(false);
    expect(nori.x + nori.width).toBeGreaterThan(viewport.width - 40);   // pegado a la derecha
    expect(nori.y + nori.height).toBeGreaterThan(viewport.height - 40); // y abajo, donde estaba el botón negro
  }
});

test('el panel de Nori cabe en pantallas móviles y la página no desborda', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 640 });
  await page.goto('/');
  await page.locator('.nori__mascot').click();
  const panel = await page.locator('#nori-panel').boundingBox();
  expect(panel.x).toBeGreaterThanOrEqual(0);
  expect(panel.y).toBeGreaterThanOrEqual(0);
  expect(panel.x + panel.width).toBeLessThanOrEqual(360);
  expect(panel.y + panel.height).toBeLessThanOrEqual(640);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});

test('con movimiento reducido Nori sigue visible y usable', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/seguridad');
  await expect(page.locator('.nori__mascot')).toBeVisible();
  await page.locator('.nori__mascot').click();
  await expect(page.locator('#nori-panel')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('#nori-panel')).toBeHidden();
});
