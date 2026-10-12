const { defineConfig } = require('@playwright/test');

// Por defecto Playwright usa el Chrome instalado en el sistema (channel: 'chrome'), sin rutas fijas.
// Con CHROME_EXECUTABLE_PATH se puede forzar un ejecutable concreto; con PYTHON, otro intérprete.
const chromeExecutable = process.env.CHROME_EXECUTABLE_PATH;
const python = process.env.PYTHON || 'python';
const port = '5010';

module.exports = defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  timeout: 25_000,
  expect: { timeout: 8_000 },
  reporter: 'list',
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    browserName: 'chromium',
    channel: chromeExecutable ? undefined : 'chrome',
    headless: true,
    // La cámara es obligatoria al confirmar operaciones: Chromium usa un dispositivo simulado y acepta el permiso solo.
    launchOptions: {
      args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'],
      ...(chromeExecutable ? { executablePath: chromeExecutable } : {}),
    },
    screenshot: 'only-on-failure',
  },
  webServer: {
    command: `${python} run.py`,
    env: {
      ...process.env,
      APP_ENV: 'testing',
      DATABASE_URL: 'sqlite://',
      SECRET_KEY: 'playwright-test-session-only',
      PORT: port,
    },
    url: `http://127.0.0.1:${port}`,
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
