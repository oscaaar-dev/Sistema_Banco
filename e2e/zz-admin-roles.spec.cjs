const { test, expect } = require('@playwright/test');
const { execFileSync } = require('child_process');

// Roles administrativos en navegador real. Cuentas de PRUEBA de la base en memoria (e2e/support/serve_e2e.py): ids 1 cliente, 2 admin de muestra,
// 3 ADMIN_GENERAL, 4 ADMINISTRADOR sin permisos, 5 ADMINISTRADOR con permisos. Esta prueba suspende al 5, por eso corre antes que zz-fraude-admin (que usa el 2).

const E2E_PASSWORD = 'E2E-solo-pruebas.1';

function sessionCookie(userId) {
  const script = [
    'from flask import Flask',
    'from flask.sessions import SecureCookieSessionInterface',
    "app = Flask('e2e-roles')",
    "app.config['SECRET_KEY'] = 'playwright-test-session-only'",
    `print(SecureCookieSessionInterface().get_signing_serializer(app).dumps({'_user_id': '${userId}', '_fresh': True, 'login_2fa_verified': True}))`,
  ].join('; ');
  return execFileSync(process.env.PYTHON || 'python', ['-c', script], { encoding: 'utf8' }).trim();
}

async function loginAs(page, userId) {
  await page.context().addCookies([{ name: 'session', value: sessionCookie(userId), domain: '127.0.0.1', path: '/' }]);
}

test('un cliente y un administrador sin permisos no entran a las cuentas administrativas', async ({ page }) => {
  await loginAs(page, 1);                                               // cliente de muestra
  for (const path of ['/admin/cuentas', '/admin/cuentas/nueva', '/admin/cuentas/historial', '/admin']) {
    expect((await page.goto(path)).status(), path).toBe(403);
  }
  await expect(page.getByText('No tienes acceso a esta sección.')).toBeVisible();
  await page.context().clearCookies();
  await loginAs(page, 4);                                               // ADMINISTRADOR sin ningún permiso
  expect((await page.goto('/admin')).status()).toBe(200);
  await expect(page.getByText('Tu cuenta aún no tiene permisos asignados')).toBeVisible();
  await expect(page.getByRole('link', { name: /Cuentas administrativas/ })).toHaveCount(0);
  await expect(page.getByRole('link', { name: /Detección de fraude/ })).toHaveCount(0);
  for (const path of ['/admin/cuentas', '/admin/fraude', '/admin/fraude/auditoria', '/admin/capturas']) {
    expect((await page.goto(path)).status(), path).toBe(403);
  }
});

test('el Administrador General ve las cuentas, los permisos y el formulario de alta sin desbordes', async ({ page }) => {
  await loginAs(page, 3);
  await page.goto('/admin');
  await page.getByRole('link', { name: /Cuentas administrativas/ }).click();
  await expect(page).toHaveURL(/\/admin\/cuentas$/);
  await expect(page.getByRole('heading', { name: 'Cuentas administrativas', level: 1 })).toBeVisible();
  for (const username of ['admin', 'e2e_general', 'e2e_sinpermisos', 'e2e_admin']) {
    await expect(page.getByRole('cell', { name: username, exact: false }).first()).toBeVisible();
  }
  await page.getByRole('link', { name: 'Crear cuenta ADMINISTRADOR' }).click();
  await expect(page.getByRole('heading', { name: 'Crear cuenta ADMINISTRADOR', level: 1 })).toBeVisible();
  await expect(page.locator('input[name="permisos"]')).toHaveCount(8);
  await expect(page.locator('input[name="password"]')).toHaveAttribute('type', 'password');
  // una confirmación incorrecta no crea nada
  await page.fill('input[name="full_name"]', 'Persona de Prueba');
  await page.fill('input[name="email"]', 'persona.prueba@example.test');
  await page.fill('input[name="password"]', 'contraseña-equivocada');
  await page.fill('input[name="confirmacion"]', 'persona.prueba@example.test');
  await page.getByRole('button', { name: 'Crear y enviar enlace' }).click();
  await expect(page.getByText('La confirmación no coincide')).toBeVisible();
  await page.goto('/admin/cuentas');
  await expect(page.getByText('persona.prueba@example.test')).toHaveCount(0);
  await page.setViewportSize({ width: 360, height: 800 });
  for (const path of ['/admin/cuentas', '/admin/cuentas/nueva', '/admin/cuentas/5', '/admin/cuentas/historial']) {
    await page.goto(path);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow, path).toBeLessThanOrEqual(0);
  }
});

test('suspender una cuenta administrativa revoca su sesión abierta y queda en el historial', async ({ browser, page }) => {
  const adminContext = await browser.newContext();
  const adminPage = await adminContext.newPage();
  await adminContext.addCookies([{ name: 'session', value: sessionCookie(5), domain: '127.0.0.1', path: '/' }]);
  expect((await adminPage.goto('/admin')).status()).toBe(200);          // sesión abierta del ADMINISTRADOR de prueba
  await expect(adminPage.getByRole('link', { name: /Detección de fraude/ })).toBeVisible();

  await loginAs(page, 3);
  await page.goto('/admin/cuentas/5');
  await expect(page.getByRole('heading', { level: 1 })).toContainText('Prueba e2e_admin');
  const form = page.locator('form[action$="/admin/cuentas/5/estado"]');
  await form.locator('select[name="accion"]').selectOption('suspender');
  await form.locator('input[name="password"]').fill(E2E_PASSWORD);
  await form.locator('input[name="confirmacion"]').fill('e2e_admin');
  await form.getByRole('button', { name: 'Aplicar' }).click();
  await expect(page.getByText('Estado actualizado')).toBeVisible();
  await expect(page.locator('.fraud-badge').first()).toHaveText('SUSPENDIDA');

  const after = await adminPage.goto('/admin');                          // la sesión que estaba abierta ya no sirve
  expect(new URL(adminPage.url()).pathname).toBe('/login');
  expect(after.status()).toBe(200);
  await adminContext.close();

  await page.goto('/admin/cuentas/historial');
  await expect(page.getByRole('cell', { name: 'ESTADO_SUSPENDIDA', exact: true })).toBeVisible();          // historial de cambios
  await expect(page.getByRole('cell', { name: 'ADMIN_ESTADO_SUSPENDIDA', exact: true })).toBeVisible();    // actividad auditada
  const mutating = await page.request.post('/admin/cuentas/historial', { data: {} });
  expect([400, 405]).toContain(mutating.status());                       // el historial no se puede modificar desde la interfaz
});

test('un enlace de activación inválido no revela nada y el inicio de sesión no tiene alta pública de administradores', async ({ page }) => {
  const response = await page.goto('/admin/activar/' + 'a'.repeat(43));
  expect(response.status()).toBe(400);
  await expect(page.getByText('No pudimos validar el enlace')).toBeVisible();
  await page.goto('/register');
  await expect(page.locator('select[name="role"], input[name="role"]')).toHaveCount(0);
});
