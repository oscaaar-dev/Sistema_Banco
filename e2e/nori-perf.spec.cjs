const { test, expect } = require('@playwright/test');

const median = (values) => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)];

test('el peso de los recursos de Nori es acotado y el script no bloquea la página', async ({ page }) => {
  const sizes = [];
  page.on('response', async (response) => {
    if (response.url().includes('/static/nori/')) sizes.push({ url: response.url(), bytes: (await response.body().catch(() => Buffer.alloc(0))).length });
  });
  await page.goto('/personas');
  await page.waitForLoadState('networkidle');
  const total = sizes.reduce((sum, item) => sum + item.bytes, 0);
  test.info().annotations.push({ type: 'recursos de Nori', description: `${sizes.length} archivos, ${(total / 1024).toFixed(0)} KB` });
  expect(sizes.length).toBeGreaterThan(5);
  expect(total).toBeLessThan(320 * 1024);
  const scripts = await page.evaluate(() => [...document.querySelectorAll('script[src*="nori/nori.js"]')].map((script) => ({ defer: script.defer, async: script.async })));
  expect(scripts).toEqual([{ defer: true, async: false }]);                   // se ejecuta tras analizar la página
  expect(await page.locator('link[href*="nori/nori.css"]').count()).toBe(1);
});

test('el widget no retrasa de forma apreciable la primera pintura de la web pública', async ({ browser }) => {
  test.setTimeout(120_000);
  async function firstPaint(withNori) {
    const context = await browser.newContext();                               // contexto limpio: sin caché
    const page = await context.newPage();
    if (!withNori) await page.route('**/static/nori/**', (route) => route.abort());
    await page.goto('/personas', { waitUntil: 'load' });
    await page.waitForTimeout(300);
    const timing = await page.evaluate(() => ({
      fcp: performance.getEntriesByName('first-contentful-paint')[0]?.startTime ?? 0,
      load: performance.getEntriesByType('navigation')[0].loadEventEnd,
    }));
    await context.close();
    return timing;
  }
  await firstPaint(true);                                                     // calentamiento del servidor
  const withNori = [], without = [];
  for (let run = 0; run < 5; run += 1) {
    withNori.push(await firstPaint(true));
    without.push(await firstPaint(false));
  }
  const fcpDelta = median(withNori.map((t) => t.fcp)) - median(without.map((t) => t.fcp));
  const loadDelta = median(withNori.map((t) => t.load)) - median(without.map((t) => t.load));
  test.info().annotations.push({ type: 'rendimiento', description:
    `FCP con Nori ${median(withNori.map((t) => t.fcp)).toFixed(0)} ms, sin Nori ${median(without.map((t) => t.fcp)).toFixed(0)} ms (diferencia ${fcpDelta.toFixed(0)} ms); ` +
    `carga completa +${loadDelta.toFixed(0)} ms` });
  expect(fcpDelta).toBeLessThan(150);
  expect(loadDelta).toBeLessThan(400);
});

test('Nori no provoca cambios de diseño (CLS) al cargar, abrirse, responder y desplazarse', async ({ page }) => {
  await page.addInitScript(() => {
    window.__cls = 0;
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) if (!entry.hadRecentInput) window.__cls += entry.value;
    }).observe({ type: 'layout-shift', buffered: true });
    window.sessionStorage.setItem('nori:v1:tip', '1');
  });
  const results = {};
  for (const path of ['/', '/personas', '/privacidad']) {
    await page.goto(path);
    await page.evaluate(() => window.sessionStorage.removeItem('nori:v1:conversation'));
    await page.reload();
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(1200);                                          // termina la animación de entrada de Nori
    const afterLoad = await page.evaluate(() => window.__cls);
    await page.locator('.nori__mascot').click();
    await page.getByLabel('Tu mensaje para Nori').fill('¿Cómo ingreso?');
    await page.getByRole('button', { name: 'Enviar' }).click();
    await expect(page.locator('.nori-msg--user')).toHaveCount(1);
    await expect(page.locator('.nori__send')).toBeEnabled({ timeout: 20_000 });
    await page.evaluate(() => window.scrollTo(0, 800));
    await page.waitForTimeout(900);
    results[path] = { load: afterLoad, total: await page.evaluate(() => window.__cls) };
  }
  test.info().annotations.push({ type: 'CLS', description: JSON.stringify(results) });
  for (const [path, value] of Object.entries(results)) {
    expect(value.total, `CLS total en ${path}`).toBeLessThan(0.1);            // «bueno» según Core Web Vitals
  }
});
