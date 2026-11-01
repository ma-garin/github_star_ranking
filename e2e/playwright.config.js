module.exports = {
  testDir: ".", timeout: 30000, retries: 0,
  use: { baseURL: "http://127.0.0.1:8799/", launchOptions: process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {} },
  webServer: { command: "python3 -m http.server 8799 --directory ../docs", url: "http://127.0.0.1:8799/", reuseExistingServer: true },
  projects: [
    { name: "phone", use: { viewport: { width: 390, height: 844 } } },
    { name: "fold-closed", use: { viewport: { width: 344, height: 882 } } },
    { name: "fold-open", use: { viewport: { width: 884, height: 1104 } } },
  ],
};
