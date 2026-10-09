const { test, expect } = require('@playwright/test');

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
