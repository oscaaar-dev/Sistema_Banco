const { test, expect } = require('@playwright/test');

test('/privacidad explica Nori y el widget convive con el texto sin duplicar identificadores', async ({ page }) => {
  await page.goto('/privacidad');
  const article = page.locator('#privacidad-nori');
  await expect(article.getByRole('heading', { name: '6. Nori, asistente automático de la web pública' })).toBeVisible();
  for (const fragment of ['sessionStorage', 'Nueva conversación', '30 minutos', 'cookie de sesión técnica', 'huella no reversible', '24 horas']) {
    await expect(article).toContainText(fragment);
  }
  const gap = await article.locator('p').nth(1).evaluate((element) => parseFloat(getComputedStyle(element).marginTop));
  expect(gap).toBeGreaterThanOrEqual(12);                                // los párrafos no quedan pegados
  await expect(page.locator('#nori')).toHaveCount(1);                    // el widget conserva su identificador único
  await expect(page.locator('#privacidad-nori')).toHaveCount(1);
});

test('la memoria permanece solo en la pestaña donde se conversó (como afirma el texto)', async ({ context }) => {
  const first = await context.newPage();
  await first.goto('/acerca');
  await first.locator('.nori__mascot').click();
  await first.getByLabel('Tu mensaje para Nori').fill('¿Es seguro?');
  await first.getByRole('button', { name: 'Enviar' }).click();
  await expect.poll(() => first.evaluate(() => JSON.parse(window.sessionStorage.getItem('nori:v1:conversation') || 'null')?.turns.length)).toBe(2);

  const second = await context.newPage();                                // otra pestaña del mismo navegador y sesión
  await second.goto('/acerca');
  expect(await second.evaluate(() => window.sessionStorage.getItem('nori:v1:conversation'))).toBeNull();
  await second.locator('.nori__mascot').click();
  await expect(second.locator('.nori-msg--user')).toHaveCount(0);
  await expect(second.locator('.nori-msg--bot').first()).toContainText('Soy Nori');
});

test('el apartado de Nori se lee bien en móvil', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 740 });
  await page.goto('/privacidad');
  await expect(page.locator('#privacidad-nori')).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  const box = await page.locator('#privacidad-nori').boundingBox();
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(360);
});
