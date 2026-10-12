const { test, expect } = require('@playwright/test');
const { execFileSync } = require('child_process');

const KEY = 'nori:v1:conversation';

function authenticatedSessionCookie() {
  const script = [
    'from flask import Flask',
    'from flask.sessions import SecureCookieSessionInterface',
    "app = Flask('e2e-nori')",
    "app.config['SECRET_KEY'] = 'playwright-test-session-only'",
    "print(SecureCookieSessionInterface().get_signing_serializer(app).dumps({'_user_id': '1', '_fresh': True, 'login_2fa_verified': True}))",
  ].join('; ');
  return execFileSync(process.env.PYTHON || 'python', ['-c', script], { encoding: 'utf8' }).trim();
}

async function openNori(page) {
  await page.locator('.nori__mascot').click();
  await expect(page.locator('#nori-panel')).toBeVisible();
}
const stored = (page) => page.evaluate((key) => JSON.parse(window.sessionStorage.getItem(key) || 'null'), KEY);
async function say(page, text) {
  const before = await page.locator('.nori-msg--user').count();
  await page.getByLabel('Tu mensaje para Nori').fill(text);
  await page.getByRole('button', { name: 'Enviar' }).click();
  // el envío desactiva el botón antes de mostrar el mensaje: se espera la burbuja del usuario y luego el fin de la respuesta
  await expect(page.locator('.nori-msg--user')).toHaveCount(before + 1, { timeout: 15_000 });
  await expect(page.locator('.nori__send')).toBeEnabled({ timeout: 20_000 });
}
const botMessages = (page) => page.locator('.nori-msg--bot:not(.nori-msg--typing)');

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => window.sessionStorage.setItem('nori:v1:tip', '1'));
});

test('una pregunta documentada se responde con la documentación y se citan sus fuentes', async ({ page }) => {
  await page.goto('/herramientas');
  await openNori(page);
  await say(page, '¿Cuántos intentos tengo para el código de acceso?');
  await expect(botMessages(page).last()).toContainText('admite hasta 5 intentos');
  await expect(botMessages(page).last()).toContainText('Según la documentación de Norte («Acceso a Norte»)');
  await expect(page.locator('.nori-kind').last()).toHaveText('Según la documentación de Norte');
  const source = page.locator('.nori-chip--link', { hasText: 'Acceso a Norte' });
  await expect(source).toHaveAttribute('href', '/login');
  await source.click();
  await expect(page).toHaveURL(/\/login$/);
});

test('las respuestas predefinidas y las documentadas se distinguen en la interfaz', async ({ page }) => {
  await page.goto('/acerca');
  await openNori(page);
  await say(page, '¿Cómo ingreso?');
  await expect(page.locator('.nori-kind').last()).toHaveText('Respuesta predefinida');
  await say(page, '¿Cuál es el monto máximo por transferencia?');
  await expect(page.locator('.nori-kind').last()).toHaveText('Según la documentación de Norte');
  await expect(botMessages(page).last()).toContainText('S/ 10,000.00');
});

test('sin documentación suficiente Nori lo reconoce, no inventa y ofrece páginas oficiales', async ({ page }) => {
  await page.goto('/seguridad');
  await openNori(page);
  for (const question of ['receta de lomo saltado', '¿Quién ganó el mundial?']) {
    await say(page, question);
    await expect(botMessages(page).last()).toContainText('No encontré información suficiente');
    await expect(page.locator('.nori-kind')).toHaveCount(0);
  }
  await expect(page.locator('.nori-chip--link')).toHaveText(['Personas', 'Herramientas', 'Seguridad', 'Acerca de Norte']);   // los de la última respuesta
});

test('«más información» continúa con el siguiente apartado del documento y la etiqueta sobrevive a la recarga', async ({ page }) => {
  await page.goto('/privacidad');
  await openNori(page);
  await say(page, '¿Cuántos intentos tengo para el código de acceso?');
  await say(page, 'más información');
  await expect(botMessages(page).last()).toContainText('bloqueado durante 15 minutos');
  expect((await stored(page)).turns.map((turn) => turn.kind).filter(Boolean)).toEqual(['documentacion', 'documentacion']);
  await page.reload();
  await openNori(page);
  await expect(page.locator('.nori-kind')).toHaveText(['Según la documentación de Norte', 'Según la documentación de Norte']);
});

test('Nori solo contacta al propio servidor: ninguna petición a servicios externos o de IA', async ({ page }) => {
  await page.goto('/personas');
  await page.waitForLoadState('networkidle');
  const requests = [];
  page.on('request', (request) => requests.push(request.url()));
  await openNori(page);
  await say(page, '¿Qué servicios puedo pagar?');
  await say(page, 'receta de lomo saltado');
  expect(requests.length).toBeGreaterThan(0);
  const outside = requests.filter((url) => !url.startsWith('http://127.0.0.1:5010/'));
  expect(outside, `peticiones fuera del servidor: ${outside.join(', ')}`).toEqual([]);
  expect(requests.some((url) => /anthropic|openai|api\.claude/i.test(url))).toBe(false);
});

test('un fallo de conexión permite reintentar sin duplicar el mensaje', async ({ page }) => {
  await page.goto('/acerca');
  await openNori(page);
  await page.route('**/api/nori/chat', (route) => route.abort());
  await say(page, '¿Qué servicios puedo pagar?');
  await expect(botMessages(page).last()).toContainText('No pude conectarme');
  const retry = page.getByRole('button', { name: 'Reintentar' });
  await expect(retry).toBeVisible();
  expect(await stored(page)).toBeNull();                                  // un error no se recuerda
  await page.unroute('**/api/nori/chat');
  await retry.click();
  await expect(botMessages(page).last()).toContainText('Electricidad, Agua, Internet y Tarjeta académica', { timeout: 15_000 });
  await expect(page.locator('.nori-msg--user')).toHaveCount(1);          // el mensaje del usuario no se duplicó
  await expect(page.getByRole('button', { name: 'Reintentar' })).toHaveCount(0);
});

test('enviar dos veces seguidas no duplica la consulta', async ({ page }) => {
  await page.goto('/herramientas');
  await openNori(page);
  await expect(botMessages(page).first()).toContainText('Soy Nori');
  const sent = [];
  page.on('request', (request) => { if (request.url().endsWith('/api/nori/chat') && request.method() === 'POST') sent.push(1); });
  const input = page.getByLabel('Tu mensaje para Nori');
  await input.fill('¿Cómo agrego un contacto?');
  await input.press('Enter');
  await input.press('Enter');
  await page.getByRole('button', { name: 'Enviar' }).click({ force: true }).catch(() => {});
  await expect(botMessages(page).last()).toContainText('Agregar contacto', { timeout: 15_000 });
  expect(sent).toHaveLength(1);
  await expect(page.locator('.nori-msg--user')).toHaveCount(1);
});

test('al cerrar sesión en la misma pestaña la conversación anterior no se hereda', async ({ page, context }) => {
  await context.addCookies([{ name: 'session', value: authenticatedSessionCookie(), domain: '127.0.0.1', path: '/' }]);
  await page.goto('/personas');                                           // página pública vista con sesión iniciada
  await openNori(page);
  await say(page, '¿Cómo ingreso?');
  expect((await stored(page)).turns).toHaveLength(2);
  await page.reload();
  await openNori(page);
  await expect(page.locator('.nori-msg--user')).toHaveText(['¿Cómo ingreso?']);      // misma sesión: se conserva

  const loggedOut = await page.evaluate(async () => {
    const { csrf } = await (await fetch('/api/nori/csrf')).json();
    const response = await fetch('/logout', { method: 'POST', headers: { 'X-CSRFToken': csrf }, redirect: 'manual' });
    return response.type === 'opaqueredirect' || response.status === 302;
  });
  expect(loggedOut).toBe(true);
  await page.reload();                                                    // misma pestaña, ahora sin sesión
  await expect.poll(() => stored(page)).toBeNull();                       // el navegador descarta la memoria anterior
  await openNori(page);
  await expect(page.locator('.nori-msg--user')).toHaveCount(0);
  await expect(botMessages(page).first()).toContainText('Soy Nori');
});

test('un historial manipulado con el scope de otra sesión se rechaza en el servidor', async ({ page }) => {
  await page.goto('/acerca');
  const result = await page.evaluate(async () => {
    const { csrf } = await (await fetch('/api/nori/csrf')).json();
    const response = await fetch('/api/nori/chat', {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
      body: JSON.stringify({ message: 'más información', scope: 'a'.repeat(24),
        history: [{ role: 'user', text: '¿Cómo ingreso?' }, { role: 'bot', text: 'x' }] }),
    });
    return response.json();
  });
  expect(result.reset).toBe(true);
  expect(result.reply).toContain('¿Sobre qué tema quieres saber?');
});
