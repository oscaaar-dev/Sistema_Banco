const { defineConfig } = require('@playwright/test');

const chromeExecutable = process.env.CHROME_EXECUTABLE_PATH ||
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';

module.exports = defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  timeout: 25_000,
  expect: { timeout: 8_000 },
  reporter: 'list',
  use: {
    baseURL: 'http://127.0.0.1:5010',
    browserName: 'chromium',
    headless: true,
    launchOptions: { executablePath: chromeExecutable },
    screenshot: 'only-on-failure',
  },
  webServer: {
    command: "powershell -NoProfile -Command \"$env:APP_ENV='testing'; $env:DATABASE_URL='sqlite://'; $env:PORT='5010'; python app.py\"",
    url: 'http://127.0.0.1:5010',
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
