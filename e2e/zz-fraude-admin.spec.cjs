const { test, expect } = require('@playwright/test');
const { execFileSync } = require('child_process');
const fs = require('fs');

// Panel antifraude en navegador real. Solo lectura: no modifica datos, por lo que puede ir en cualquier orden.

function sessionCookie(userId) {
  const script = [
    'from flask import Flask',
    'from flask.sessions import SecureCookieSessionInterface',
    "app = Flask('e2e-fraud')",
    "app.config['SECRET_KEY'] = 'playwright-test-session-only'",
    `print(SecureCookieSessionInterface().get_signing_serializer(app).dumps({'_user_id': '${userId}', '_fresh': True, 'login_2fa_verified': True}))`,
  ].join('; ');
  return execFileSync(process.env.PYTHON || 'python', ['-c', script], { encoding: 'utf8' }).trim();
}

async function loginAs(page, userId) {
  await page.context().addCookies([{ name: 'session', value: sessionCookie(userId), domain: '127.0.0.1', path: '/' }]);
}

test('un cliente no puede abrir el panel antifraude ni descargar informes', async ({ page }) => {
  await loginAs(page, 1);                                       // cliente de muestra
  const panel = await page.goto('/admin/fraude');
  expect(panel.status()).toBe(403);
  await expect(page.getByText('No tienes acceso a esta sección.')).toBeVisible();
  const report = await page.request.get('/admin/fraude/exportar/evaluaciones.csv');
  expect(report.status()).toBe(403);
});

test('un administrador ve el modo de observación, las versiones del modelo y los formularios de descarga', async ({ page }) => {
  await loginAs(page, 2);                                       // administrador de muestra
  await page.goto('/admin');
  await page.getByRole('link', { name: /Detección de fraude/ }).click();
  await expect(page).toHaveURL(/\/admin\/fraude$/);
  await expect(page.getByRole('heading', { name: 'Detección de fraude', level: 1 })).toBeVisible();
  await expect(page.getByText('Modo actual:')).toContainText('shadow');
  await expect(page.getByText('observación: se registran evaluaciones e incidentes')).toBeVisible();
  await expect(page.getByText('no una probabilidad de fraude')).toBeVisible();
  await expect(page.getByText('Todavía no se entrenó ningún modelo')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Descargar CSV' })).toHaveCount(7);       // 6 informes + la auditoría
  await expect(page.getByRole('link', { name: 'Restricciones activas y vencidas' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Auditoría antifraude' })).toBeVisible();
  await page.getByRole('link', { name: 'Operaciones retenidas' }).click();
  await expect(page.getByRole('heading', { name: 'Operaciones retenidas', level: 1 })).toBeVisible();
  await expect(page.getByText('no se ejecutó')).toBeVisible();
});

test('la descarga CSV llega en UTF-8 con cabeceras claras y sin datos personales', async ({ page }) => {
  await loginAs(page, 2);
  await page.goto('/admin/fraude');
  const form = page.locator('form[action$="/exportar/evaluaciones.csv"]');
  await form.locator('select[name="nivel"]').selectOption('NORMAL');
  const [download] = await Promise.all([page.waitForEvent('download'), form.getByRole('button', { name: 'Descargar CSV' }).click()]);
  expect(download.suggestedFilename()).toMatch(/^fraude-evaluaciones-\d{8}-\d{6}\.csv$/);
  const bytes = fs.readFileSync(await download.path());
  expect(bytes.slice(0, 3).toString('hex')).toBe('efbbbf');       // BOM de UTF-8 para Excel
  const header = bytes.toString('utf8').replace(/^﻿/, '').split(/\r?\n/)[0];
  expect(header).toBe('id,fecha_utc,cliente_id,contexto,evento,nivel,puntuacion,confianza,senales,version_reglas,version_modelo,puntuacion_modelo,modo,accion_recomendada,accion_aplicada,estado_evaluacion,codigo_error,duracion_ms,movimiento_id');
  expect(header).not.toMatch(/correo|email|nombre|dni|contrase|clave/i);
});

test('los filtros inválidos se rechazan y el panel no desborda en móvil', async ({ page }) => {
  await loginAs(page, 2);
  const bad = await page.request.get('/admin/fraude/exportar/evaluaciones.csv?nivel=NADA');
  expect(bad.status()).toBe(400);
  expect((await page.request.get('/admin/fraude?estado=HACKEADO')).status()).toBe(400);
  expect((await page.request.get('/admin/fraude/exportar/usuarios.csv')).status()).toBe(404);
  await page.setViewportSize({ width: 360, height: 800 });
  await page.goto('/admin/fraude');
  await expect(page.getByRole('heading', { name: 'Detección de fraude', level: 1 })).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});
