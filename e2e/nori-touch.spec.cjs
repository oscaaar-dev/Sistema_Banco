const { test, expect } = require('@playwright/test');

// Dispositivo táctil de verdad: eventos de toque (tap), pantalla móvil y gestos de desplazamiento por CDP.
test.use({ hasTouch: true, isMobile: true, viewport: { width: 390, height: 844 } });

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => window.sessionStorage.setItem('nori:v1:tip', '1'));
});

test('con toques se abre, se conversa, se sigue un enlace de fuente, se inicia una conversación nueva y se cierra', async ({ page }) => {
  await page.goto('/herramientas');
  await expect(page.locator('.nori__mascot')).toBeVisible();
  await page.waitForTimeout(800);
  await page.locator('.nori__mascot').tap();
  await expect(page.locator('#nori-panel')).toBeVisible();
  await page.getByLabel('Tu mensaje para Nori').fill('¿Cuántos intentos tengo para el código de acceso?');
  await page.getByRole('button', { name: 'Enviar' }).tap();
  await expect(page.locator('.nori-msg--user')).toHaveCount(1);
  await expect(page.locator('.nori__send')).toBeEnabled({ timeout: 20_000 });
  await expect(page.locator('.nori-msg--bot').last()).toContainText('admite hasta 5 intentos');
  await page.getByRole('button', { name: 'Nueva conversación' }).tap();
  await expect(page.locator('.nori-msg--user')).toHaveCount(0);
  await page.getByLabel('Tu mensaje para Nori').fill('¿Cuántos intentos tengo para el código de acceso?');
  await page.getByRole('button', { name: 'Enviar' }).tap();
  await expect(page.locator('.nori__send')).toBeEnabled({ timeout: 20_000 });
  await page.locator('.nori-chip--link', { hasText: 'Acceso a Norte' }).tap();
  await expect(page).toHaveURL(/\/login$/);
  await page.goBack();
  await page.waitForTimeout(500);
  await page.locator('.nori__mascot').tap();
  await expect(page.locator('#nori-panel')).toBeVisible();
  await page.getByRole('button', { name: 'Cerrar chat', exact: true }).tap();
  await expect(page.locator('#nori-panel')).toBeHidden();
});

test('los controles táctiles tienen un tamaño suficiente (WCAG 2.5.8: al menos 24×24 px; el botón de Nori, 44 px)', async ({ page }) => {
  await page.goto('/seguridad');
  await page.waitForTimeout(800);
  const hit = await page.locator('.nori__hit').boundingBox();
  expect(hit.width).toBeGreaterThanOrEqual(44);
  expect(hit.height).toBeGreaterThanOrEqual(44);
  await page.locator('.nori__mascot').tap();
  const small = await page.evaluate(() => [...document.querySelectorAll('#nori-panel button, #nori-panel input, #nori-panel a')]
    .map((element) => ({ name: element.className, w: element.getBoundingClientRect().width, h: element.getBoundingClientRect().height }))
    .filter((item) => item.w < 24 || item.h < 24));
  expect(small).toEqual([]);
});

test('un gesto táctil de desplazamiento aparta a Nori y vuelve al terminar; los enlaces siguen siendo tocables', async ({ page, context }) => {
  await page.goto('/personas');
  await page.waitForTimeout(900);
  // Se registra si Nori llegó a apartarse durante el gesto (la clase dura solo 700 ms tras el último desplazamiento).
  await page.evaluate(() => {
    window.__tucked = { seen: false, offscreen: false };
    const root = document.getElementById('nori');
    new MutationObserver(() => {
      if (!root.classList.contains('is-tucked')) return;
      window.__tucked.seen = true;
      requestAnimationFrame(() => setTimeout(() => {
        if (document.querySelector('.nori__mascot').getBoundingClientRect().left >= innerWidth - 1) window.__tucked.offscreen = true;
      }, 500));
    }).observe(root, { attributes: true, attributeFilter: ['class'] });
  });
  const cdp = await context.newCDPSession(page);
  // Dedo real: toca, arrastra hacia arriba y suelta (synthesizeScrollGesture no desplaza en este navegador).
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: 200, y: 700 }] });
  for (let step = 1; step <= 12; step += 1) {
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: 200, y: 700 - step * 30 }] });
    await page.waitForTimeout(16);
  }
  expect(await page.evaluate(() => window.scrollY)).toBeGreaterThan(100);        // el gesto desplazó la página de verdad
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  await expect.poll(() => page.evaluate(() => window.__tucked.seen)).toBe(true);
  await expect.poll(() => page.evaluate(() => window.__tucked.offscreen), { timeout: 4000 }).toBe(true);   // fuera de pantalla: no tapa nada
  await expect(page.locator('#nori')).not.toHaveClass(/is-tucked/, { timeout: 3000 });
  await page.waitForTimeout(400);
  await expect(page.locator('.nori__mascot')).toBeVisible();
  const link = page.getByRole('link', { name: /Explorar herramientas/ });
  await link.scrollIntoViewIfNeeded();
  await page.waitForTimeout(1000);
  await link.tap();
  await expect(page).toHaveURL(/\/herramientas$/);
});

test('la zona transparente de Nori deja pasar el toque a la página', async ({ page }) => {
  await page.goto('/acerca');
  await page.waitForTimeout(900);
  const probe = await page.evaluate(() => {
    const mascot = document.querySelector('.nori__mascot').getBoundingClientRect();
    const hit = document.querySelector('.nori__hit').getBoundingClientRect();
    const inNori = (x, y) => { const e = document.elementFromPoint(x, y); return !!(e && e.closest('#nori')); };
    return {
      corner: inNori(mascot.left + 2, mascot.top + 2),
      body: inNori(hit.left + hit.width / 2, hit.top + hit.height / 2),
    };
  });
  expect(probe).toEqual({ corner: false, body: true });
});
