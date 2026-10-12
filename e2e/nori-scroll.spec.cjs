const { test, expect } = require('@playwright/test');

const PAGES = ['/', '/personas', '/herramientas', '/seguridad'];
const VIEWPORTS = [{ width: 1280, height: 900 }, { width: 390, height: 844 }];

// Enlaces y botones de la página (fuera de Nori) cuyo punto visible bajo Nori recibe el clic de Nori en lugar del suyo.
function blockedByNori() {
  const mascot = document.querySelector('.nori__mascot').getBoundingClientRect();
  const blocked = [];
  document.querySelectorAll('a[href], button, input, summary, select, textarea, [role="button"]').forEach((element) => {
    if (element.closest('#nori')) return;
    const r = element.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0 || r.bottom <= 0 || r.top >= innerHeight) return;
    const w = Math.min(r.right, mascot.right) - Math.max(r.left, mascot.left);
    const h = Math.min(r.bottom, mascot.bottom) - Math.max(r.top, mascot.top);
    if (w <= 0 || h <= 0) return;
    // se prueban varios puntos de la zona compartida, no solo el centro
    for (const fx of [0.1, 0.5, 0.9]) {
      for (const fy of [0.1, 0.5, 0.9]) {
        const x = Math.max(r.left, mascot.left) + w * fx;
        const y = Math.max(r.top, mascot.top) + h * fy;
        const top = document.elementFromPoint(x, y);
        if (top && top.closest('#nori')) {
          blocked.push((element.getAttribute('aria-label') || element.textContent || element.getAttribute('href') || element.tagName).trim().replace(/\s+/g, ' ').slice(0, 40));
          return;
        }
      }
    }
  });
  return blocked;
}

async function scrollPositions(page, viewport) {
  const total = await page.evaluate(() => document.documentElement.scrollHeight);
  const maximum = Math.max(0, total - viewport.height);
  const steps = 14;
  return Array.from({ length: steps }, (_, index) => Math.round(((index + 1) / steps) * maximum));
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => window.sessionStorage.setItem('nori:v1:tip', '1'));   // sin el globo de saludo (es temporal)
});

test('durante el scroll Nori no bloquea ningún enlace ni botón (escritorio y móvil)', async ({ page }) => {
  test.setTimeout(240_000);
  for (const viewport of VIEWPORTS) {
    await page.setViewportSize(viewport);
    for (const path of PAGES) {
      await page.goto(path);
      await expect(page.locator('.nori__mascot')).toBeVisible();
      await page.waitForTimeout(900);                                     // termina la animación de entrada
      for (const top of await scrollPositions(page, viewport)) {
        await page.evaluate((y) => window.scrollTo(0, y), top);
        await page.waitForTimeout(320);                                    // Nori ya terminó de apartarse y sigue en pleno scroll
        await expect(page.locator('#nori')).toHaveClass(/is-tucked/);
        const blocked = await page.evaluate(blockedByNori);
        expect(blocked, `${path} a ${viewport.width}px en scroll=${top}`).toEqual([]);
      }
    }
  }
});

test('al detenerse el scroll Nori vuelve y solo captura clics sobre su cuerpo', async ({ page }) => {
  for (const viewport of VIEWPORTS) {
    await page.setViewportSize(viewport);
    await page.goto('/personas');
    await page.waitForTimeout(900);
    await page.evaluate(() => window.scrollTo(0, 600));
    await page.waitForTimeout(100);
    await expect(page.locator('#nori')).toHaveClass(/is-tucked/);
    await expect(page.locator('#nori')).not.toHaveClass(/is-tucked/, { timeout: 3000 });
    await page.waitForTimeout(400);                                        // termina la transición de regreso

    const probe = await page.evaluate(() => {
      const mascot = document.querySelector('.nori__mascot').getBoundingClientRect();
      const hit = document.querySelector('.nori__hit').getBoundingClientRect();
      const inNori = (x, y) => { const e = document.elementFromPoint(x, y); return !!(e && e.closest('#nori')); };
      const corners = [[2, 2], [mascot.width - 3, 2], [2, mascot.height - 3], [mascot.width - 3, mascot.height - 3]]
        .map(([dx, dy]) => inNori(mascot.left + dx, mascot.top + dy));
      return {
        visible: mascot.right <= innerWidth + 1 && mascot.left >= 0 && mascot.bottom <= innerHeight + 1,
        corners,
        centre: inNori(hit.left + hit.width / 2, hit.top + hit.height / 2),
        hitShare: Math.round((hit.width * hit.height) / (mascot.width * mascot.height) * 100),
      };
    });
    expect(probe.visible, `Nori visible otra vez a ${viewport.width}px`).toBe(true);
    expect(probe.corners, 'las esquinas transparentes dejan pasar el clic').toEqual([false, false, false, false]);
    expect(probe.centre, 'el cuerpo de Nori sí recibe el clic').toBe(true);
    expect(probe.hitShare).toBeLessThan(70);
  }
});

test('Nori sigue accesible: teclado, lector de pantalla y chat abierto no se apartan', async ({ page }) => {
  await page.goto('/herramientas');
  const mascotButton = page.getByRole('button', { name: 'Abrir chat con Nori' });
  await expect(mascotButton).toBeAttached();

  // con el foco en Nori no se aparta aunque haya scroll
  await mascotButton.focus();
  await page.evaluate(() => window.scrollTo(0, 500));
  await page.waitForTimeout(200);
  await expect(page.locator('#nori')).not.toHaveClass(/is-tucked/);
  await page.keyboard.press('Enter');
  await expect(page.locator('#nori-panel')).toBeVisible();

  // con el chat abierto tampoco se aparta
  await page.evaluate(() => window.scrollTo(0, 900));
  await page.waitForTimeout(200);
  await expect(page.locator('#nori')).not.toHaveClass(/is-tucked/);
  await expect(page.locator('#nori-panel')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('#nori-panel')).toBeHidden();

  // el botón sigue en el árbol de accesibilidad mientras Nori está apartado
  await page.locator('body').click({ position: { x: 5, y: 5 } });
  await page.evaluate(() => window.scrollTo(0, 1500));
  await page.waitForTimeout(100);
  await expect(page.locator('#nori')).toHaveClass(/is-tucked/);
  await expect(page.getByRole('button', { name: 'Abrir chat con Nori' })).toBeAttached();
  expect(await page.locator('#nori').getAttribute('aria-hidden')).toBeNull();
  expect(await page.locator('.nori__mascot').getAttribute('aria-hidden')).toBeNull();
});

test('si el puntero está sobre Nori no se aparta mientras haces scroll', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto('/personas');
  await page.waitForTimeout(900);
  const hit = await page.locator('.nori__hit').boundingBox();
  await page.mouse.move(hit.x + hit.width / 2, hit.y + hit.height / 2);
  await page.mouse.wheel(0, 500);
  await page.waitForTimeout(250);
  await expect(page.locator('#nori')).not.toHaveClass(/is-tucked/);
});

test('con movimiento reducido Nori se aparta sin animación y vuelve al detenerse el scroll', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/privacidad');
  await page.waitForTimeout(500);
  const duration = await page.locator('#nori').evaluate((element) => parseFloat(getComputedStyle(element).transitionDuration));
  expect(duration).toBeLessThan(0.01);                                   // sin animación (el sitio ya fuerza ~0 s con movimiento reducido)
  await page.evaluate(() => window.scrollTo(0, 700));
  await page.waitForTimeout(80);
  await expect(page.locator('#nori')).toHaveClass(/is-tucked/);
  const box = await page.locator('.nori__mascot').boundingBox();
  expect(box.x).toBeGreaterThanOrEqual(390 - 1);                         // ya está fuera de la pantalla, sin pasar por una animación
  await expect(page.locator('#nori')).not.toHaveClass(/is-tucked/, { timeout: 3000 });
  await expect(page.locator('.nori__mascot')).toBeVisible();
});

test('apartar a Nori no crea scroll horizontal', async ({ page }) => {
  for (const viewport of VIEWPORTS) {
    await page.setViewportSize(viewport);
    await page.goto('/seguridad');
    await page.waitForTimeout(700);
    await page.evaluate(() => window.scrollTo(0, 800));
    await page.waitForTimeout(350);
    await expect(page.locator('#nori')).toHaveClass(/is-tucked/);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow, `desbordamiento a ${viewport.width}px`).toBeLessThanOrEqual(0);
  }
});
