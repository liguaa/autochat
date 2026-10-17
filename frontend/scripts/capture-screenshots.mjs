import { mkdirSync } from "node:fs";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";
import { preview } from "vite";

const browserPaths = [
  process.env.AC_BROWSER_PATH,
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
].filter(Boolean);
const executablePath = browserPaths.find((path) => existsSync(path));
if (!executablePath) {
  throw new Error("Set AC_BROWSER_PATH to a Chrome or Edge executable.");
}

const outputDir = fileURLToPath(new URL("../../design/", import.meta.url));
mkdirSync(outputDir, { recursive: true });

const server = await preview({
  preview: { host: "127.0.0.1", port: 5179, strictPort: false },
});
let browser;
try {
  const baseUrl = server.resolvedUrls.local[0];
  browser = await chromium.launch({
    executablePath,
    headless: true,
    args: ["--no-sandbox"],
  });

  for (const shot of [
    { name: "workbench-demo.png", width: 1280, height: 800, view: "workbench" },
    { name: "panel-demo.png", width: 420, height: 700, view: "panel" },
    { name: "panel-reply-demo.png", width: 420, height: 700, view: "panel", reply: true },
  ]) {
    const page = await browser.newPage({
      viewport: { width: shot.width, height: shot.height },
      deviceScaleFactor: 1,
    });
    await page.goto(`${baseUrl}?view=${shot.view}`, { waitUntil: "networkidle" });
    await page.locator(".demo-ribbon").waitFor();
    await page.evaluate(async () => {
      await document.fonts.ready;
    });
    if (shot.reply) {
      await page.locator(".workspace").evaluate((element) => {
        element.scrollTop = element.scrollHeight;
      });
    }
    const path = fileURLToPath(new URL(`../../design/${shot.name}`, import.meta.url));
    await page.screenshot({ path, animations: "disabled" });
    console.log(path);
    await page.close();
  }
} finally {
  await browser?.close();
  await server.httpServer.close();
}
