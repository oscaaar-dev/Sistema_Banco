const { test, expect } = require('@playwright/test');

const VIEWPORTS = [{ width: 320, height: 640 }, { width: 360, height: 740 }, { width: 390, height: 844 }, { width: 1280, height: 900 }];
const PAGES = ['/', '/personas', '/seguridad', '/privacidad'];

// Errores de consola que importan: los propios de Nori o de la página. Se ignoran los fallos de red hacia CDN externos del sitio
// (tipografías e iconos) cuando se prueba sin Internet.
function collectErrors(page) {
  const errors = [];
  page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`));
  page.on('console', (message) => {
    if (message.type() !== 'error') return;
    const location = message.location().url || '';
    if (/fonts\.(googleapis|gstatic)|cdn\.jsdelivr/.test(location + message.text())) return;
    errors.push(`console: ${message.text()} (${location})`);
  });
  return errors;
}
async function openNori(page) {
  await page.locator('.nori__mascot').click();
  await expect(page.locator('#nori-panel')).toBeVisible();
}
async function say(page, text) {
  const before = await page.locator('.nori-msg--user').count();
  await page.getByLabel('Tu mensaje para Nori').fill(text);
  await page.getByRole('button', { name: 'Enviar' }).click();
  // el envío desactiva el botón antes de mostrar el mensaje: se espera la burbuja del usuario y luego el fin de la respuesta
  await expect(page.locator('.nori-msg--user')).toHaveCount(before + 1, { timeout: 15_000 });
  await expect(page.locator('.nori__send')).toBeEnabled({ timeout: 20_000 });
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => window.sessionStorage.setItem('nori:v1:tip', '1'));
});

test('el chat funciona y es legible a 320, 360, 390 y 1280 px sin desbordes ni bloqueos', async ({ page }) => {
  test.setTimeout(240_000);
  const errors = collectErrors(page);
  for (const viewport of VIEWPORTS) {
    await page.setViewportSize(viewport);
    for (const path of PAGES) {
      await page.goto(path);
      await expect(page.locator('.nori__mascot')).toBeVisible();
      await page.waitForTimeout(700);
      // la zona de clic recibe el clic real (elementFromPoint) y el resto de la página no queda bloqueada en la esquina
      const click = await page.evaluate(() => {
        const hit = document.querySelector('.nori__hit').getBoundingClientRect();
        const top = document.elementFromPoint(hit.left + hit.width / 2, hit.top + hit.height / 2);
        return !!(top && top.closest('#nori'));
      });
      expect(click, `${path} a ${viewport.width}px`).toBe(true);
      await openNori(page);
      const layout = await page.evaluate(() => {
        const panel = document.querySelector('#nori-panel').getBoundingClientRect();
        const input = document.querySelector('.nori__input').getBoundingClientRect();
        const head = document.querySelector('.nori__head');
        return {
          left: panel.left, right: panel.right, top: panel.top, bottom: panel.bottom,
          inputVisible: input.bottom <= innerHeight && input.top >= 0,
          headOverflow: head.scrollWidth - head.clientWidth,
          pageOverflow: document.documentElement.scrollWidth - innerWidth,
        };
      });
      expect(layout.left, `${path} ${viewport.width}px`).toBeGreaterThanOrEqual(0);
      expect(layout.right).toBeLessThanOrEqual(viewport.width);
      expect(layout.top).toBeGreaterThanOrEqual(0);
      expect(layout.bottom).toBeLessThanOrEqual(viewport.height);
      expect(layout.inputVisible).toBe(true);
      expect(layout.headOverflow).toBeLessThanOrEqual(0);
      expect(layout.pageOverflow).toBeLessThanOrEqual(0);
      await page.keyboard.press('Escape');
      await expect(page.locator('#nori-panel')).toBeHidden();
    }
  }
  expect(errors).toEqual([]);
});

test('se puede usar solo con teclado: abrir, escribir, enviar, recorrer y cerrar devolviendo el foco', async ({ page }) => {
  const errors = collectErrors(page);
  await page.goto('/acerca');
  await page.locator('.nori__mascot').focus();
  await expect(page.locator('.nori__mascot')).toBeFocused();
  await page.keyboard.press('Enter');                                    // abre el chat
  await expect(page.locator('#nori-panel')).toBeVisible();
  await expect(page.getByLabel('Tu mensaje para Nori')).toBeFocused();    // el foco pasa al campo de texto
  await page.keyboard.type('¿Qué servicios puedo pagar?');
  await page.keyboard.press('Enter');                                    // enviar con Enter
  await expect(page.locator('.nori-msg--user')).toHaveCount(1);
  await expect(page.locator('.nori__send')).toBeEnabled({ timeout: 15_000 });
  await expect(page.locator('.nori-msg--bot').last()).toContainText('Electricidad');
  await expect(page.getByLabel('Tu mensaje para Nori')).toBeFocused();    // tras responder, listo para seguir
  // el orden de tabulación recorre: campo → Enviar → (fin del panel)
  await page.keyboard.press('Tab');
  await expect(page.getByRole('button', { name: 'Enviar' })).toBeFocused();
  await page.keyboard.press('Shift+Tab');
  await expect(page.getByLabel('Tu mensaje para Nori')).toBeFocused();
  await page.keyboard.press('Escape');                                   // cerrar con Escape
  await expect(page.locator('#nori-panel')).toBeHidden();
  await expect(page.locator('.nori__mascot')).toBeFocused();              // el foco vuelve al botón de Nori
  expect(errors).toEqual([]);
});

test('los controles tienen nombres accesibles y los estados se anuncian', async ({ page }) => {
  await page.goto('/herramientas');
  const mascot = page.getByRole('button', { name: 'Abrir chat con Nori' });
  await expect(mascot).toHaveAttribute('aria-expanded', 'false');
  await expect(mascot).toHaveAttribute('aria-controls', 'nori-panel');
  await mascot.click();
  await expect(page.getByRole('button', { name: 'Cerrar chat con Nori' })).toHaveAttribute('aria-expanded', 'true');
  const panel = page.getByRole('dialog', { name: 'Chat con Nori' });
  await expect(panel).toBeVisible();
  await expect(page.getByRole('log')).toHaveAttribute('aria-live', 'polite');
  await expect(page.getByRole('button', { name: 'Nueva conversación' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Cerrar chat', exact: true })).toBeVisible();
  const input = page.getByLabel('Tu mensaje para Nori');
  await expect(input).toHaveAttribute('aria-describedby', 'nori-legal');
  await expect(page.locator('#nori-legal')).toContainText('No escribas DNI');
  await expect(page.locator('#nori-legal')).toContainText('La conversación se guarda solo en esta pestaña');
  // mientras responde, el registro se marca como ocupado y el botón no permite enviar dos veces
  await input.fill('¿Cómo ingreso?');
  await page.getByRole('button', { name: 'Enviar' }).click();
  await expect(page.getByRole('log')).toHaveAttribute('aria-busy', 'true');
  await expect(page.locator('.nori__send')).toBeDisabled();
  await expect(page.locator('.nori__send')).toBeEnabled({ timeout: 15_000 });
  await expect(page.getByRole('log')).not.toHaveAttribute('aria-busy', 'true');
  // las imágenes decorativas no se anuncian
  expect(await page.locator('#nori img').evaluateAll((images) => images.every((image) => image.getAttribute('alt') === ''))).toBe(true);
});

test('el indicador de carga aparece mientras se procesa la consulta', async ({ page }) => {
  await page.goto('/privacidad');
  await openNori(page);
  await page.route('**/api/nori/chat', async (route) => { await new Promise((resolve) => setTimeout(resolve, 900)); await route.continue(); });
  await page.getByLabel('Tu mensaje para Nori').fill('¿Cómo ingreso?');
  await page.getByRole('button', { name: 'Enviar' }).click();
  await expect(page.locator('.nori-msg--typing')).toBeVisible();
  await expect(page.getByRole('status').filter({ hasText: 'Nori está escribiendo' })).toBeAttached();
  await expect(page.locator('.nori-msg--typing')).toHaveCount(0, { timeout: 15_000 });
});

test('mensajes largos y sin espacios se ajustan, el último mensaje queda a la vista y distingo usuario de Nori', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 640 });
  await page.goto('/seguridad');
  await openNori(page);
  await say(page, 'x'.repeat(480));
  await say(page, 'hola '.repeat(95).trim());
  await say(page, '¿Cuál es el monto máximo por transferencia?');
  const check = await page.evaluate(() => {
    const log = document.querySelector('.nori__log');
    const bubbles = [...log.querySelectorAll('.nori-msg')];
    const user = log.querySelector('.nori-msg--user').getBoundingClientRect();
    const bot = log.querySelector('.nori-msg--bot').getBoundingClientRect();
    const user_bg = getComputedStyle(log.querySelector('.nori-msg--user')).backgroundColor;
    const bot_bg = getComputedStyle(log.querySelector('.nori-msg--bot')).backgroundColor;
    return {
      overflowing: bubbles.filter((bubble) => bubble.scrollWidth > bubble.clientWidth + 1).length,
      atBottom: Math.abs(log.scrollHeight - log.clientHeight - log.scrollTop) < 4,
      userRight: user.left > bot.left, differentColours: user_bg !== bot_bg,
      logOverflow: log.scrollWidth - log.clientWidth,
    };
  });
  expect(check.overflowing).toBe(0);
  expect(check.logOverflow).toBeLessThanOrEqual(0);
  expect(check.atBottom).toBe(true);
  expect(check.userRight && check.differentColours).toBe(true);
});

test('si sessionStorage no está disponible Nori sigue funcionando sin errores', async ({ page }) => {
  const errors = collectErrors(page);
  await page.addInitScript(() => {
    Object.defineProperty(window, 'sessionStorage', { get() { throw new DOMException('denegado', 'SecurityError'); } });
  });
  await page.goto('/personas');
  await expect(page.locator('.nori__mascot')).toBeVisible();
  await openNori(page);
  await say(page, '¿Qué servicios puedo pagar?');
  await expect(page.locator('.nori-msg--bot').last()).toContainText('Electricidad');
  await say(page, 'más información');                                    // el hilo vive en la memoria de la página: el seguimiento funciona
  await expect(page.locator('.nori-msg--bot').last()).toContainText('recarga');
  await page.reload();                                                   // pero nada persiste entre cargas
  await openNori(page);
  await expect(page.locator('.nori-msg--user')).toHaveCount(0);
  await expect(page.locator('.nori-msg--bot').first()).toContainText('Soy Nori');
  await page.getByRole('button', { name: 'Nueva conversación' }).click();
  await expect(page.locator('.nori-msg--user')).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('la expiración por inactividad y «Nueva conversación» siguen funcionando con respuestas documentadas', async ({ page }) => {
  await page.goto('/acerca');
  await openNori(page);
  await say(page, '¿Cuántos intentos tengo para el código de acceso?');
  await page.evaluate(() => { const now = Date.now; Date.now = () => now() + 31 * 60 * 1000; });
  await page.getByLabel('Tu mensaje para Nori').fill('¿Qué servicios puedo pagar?');
  await page.getByRole('button', { name: 'Enviar' }).click();            // al expirar, el chat se vacía antes de mostrar el mensaje nuevo
  await expect(page.locator('.nori-msg--user')).toHaveText(['¿Qué servicios puedo pagar?'], { timeout: 15_000 });
  await expect(page.locator('.nori__send')).toBeEnabled({ timeout: 20_000 });
  await expect(page.getByText('Pasó un rato sin actividad')).toBeVisible();
  await page.getByRole('button', { name: 'Nueva conversación' }).click();
  await expect(page.locator('.nori-msg--user')).toHaveCount(0);
  expect(await page.evaluate(() => window.sessionStorage.getItem('nori:v1:conversation'))).toBeNull();
});

test('no hay errores de consola en un recorrido completo por las páginas públicas', async ({ page }) => {
  const errors = collectErrors(page);
  for (const path of ['/', '/personas', '/herramientas', '/seguridad', '/acerca', '/terminos', '/privacidad']) {
    await page.goto(path);
    await expect(page.locator('.nori__mascot')).toBeVisible();
  }
  await openNori(page);
  await say(page, '¿Es seguro?');
  await say(page, '¿Cuál es el monto máximo por transferencia?');
  await say(page, 'mi DNI es 12345678');
  await say(page, 'receta de lomo saltado');
  await page.reload();
  await openNori(page);
  await page.getByRole('button', { name: 'Nueva conversación' }).click();
  expect(errors).toEqual([]);
});
