import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { chromium } from "playwright-core";
import { preview } from "vite";

const browserPaths = [
  process.env.AC_BROWSER_PATH,
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
].filter(Boolean);
const executablePath = browserPaths.find((path) => existsSync(path));
if (!executablePath) throw new Error("Set AC_BROWSER_PATH to Chrome or Edge.");

const server = await preview({
  preview: { host: "127.0.0.1", port: 5180, strictPort: false },
});
let browser;
try {
  browser = await chromium.launch({
    executablePath,
    headless: true,
    args: ["--no-sandbox"],
  });
  const page = await browser.newPage({ viewport: { width: 420, height: 700 } });
  await page.goto(`${server.resolvedUrls.local[0]}?view=panel`, {
    waitUntil: "networkidle",
  });
  const labels = page.locator(".signal-item strong");
  assert.deepEqual(await labels.allTextContents(), ["复杂或不明", "结束话题"]);
  await page.locator(".signal-details summary").click();
  assert.match(await page.locator(".signal-details").innerText(), /情绪：所选类别相对概率 61%/);
  assert.match(await page.locator(".signal-details").innerText(), /意图：所选类别相对概率 78%/);
  assert.match(await page.locator(".signal-source").textContent(), /Jev Choice/);
  await page.locator(".signal-details summary").click();

  await page.getByRole("button", { name: /^分析/ }).click();
  await page.waitForFunction(() =>
    [...document.querySelectorAll(".signal-item strong")].every(
      (element) => element.textContent === "尚未判断",
    ),
  );
  await page.waitForFunction(() =>
    document.querySelector(".stage.running")?.textContent?.includes("写回复") &&
    document.querySelector(".signal-item strong")?.textContent === "复杂或不明",
  );
  assert.deepEqual(await labels.allTextContents(), ["复杂或不明", "结束话题"]);
  console.log("Jev 情绪与意图在判断完成后、写回复期间已显示。");
} finally {
  await browser?.close();
  await server.httpServer.close();
}
