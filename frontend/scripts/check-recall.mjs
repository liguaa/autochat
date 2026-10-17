import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";
import { preview } from "vite";

const executablePath = [process.env.AC_BROWSER_PATH,
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
].filter(Boolean).find((path) => existsSync(path));
if (!executablePath) throw new Error("Set AC_BROWSER_PATH to Chrome or Edge.");

const server = await preview({ preview: { host: "127.0.0.1", port: 5181, strictPort: false } });
let browser;
try {
  browser = await chromium.launch({ executablePath, headless: true, args: ["--no-sandbox"] });
  const page = await browser.newPage({ viewport: { width: 420, height: 700 } });
  await page.goto(`${server.resolvedUrls.local[0]}?view=panel`, { waitUntil: "networkidle" });
  await page.getByLabel("原文草稿").fill("未加入的草稿");
  await page.locator("#menu-demo-m3").click();
  await page.getByRole("menuitem", { name: "撤回这条记录" }).click();
  await page.locator("#restore-demo-m3").waitFor();
  assert.equal(await page.locator(".thread .message-bubble").filter({ hasText: "先不聊工作了" }).count(), 0);
  assert.equal(await page.locator(".reply-empty").count(), 1);
  assert.equal(await page.getByLabel("原文草稿").inputValue(), "未加入的草稿");
  const designDir = fileURLToPath(new URL("../../design/", import.meta.url));
  mkdirSync(designDir, { recursive: true });
  await page.screenshot({ path: fileURLToPath(new URL("../../design/recall-panel-demo.png", import.meta.url)), animations: "disabled" });
  await page.locator("#restore-demo-m3").click();
  await page.locator("#menu-demo-m3").waitFor();
  assert.equal(await page.locator(".thread .message-bubble").filter({ hasText: "先不聊工作了" }).count(), 1);
  assert.equal(await page.locator(".analysis-note").count(), 0);
  assert.equal(await page.getByLabel("原文草稿").inputValue(), "未加入的草稿");
  const workbench = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  await workbench.goto(`${server.resolvedUrls.local[0]}?view=workbench`, { waitUntil: "networkidle" });
  await workbench.locator("#menu-demo-m3").click();
  await workbench.getByRole("menuitem", { name: "撤回这条记录" }).click();
  await workbench.locator("#restore-demo-m3").waitFor();
  await workbench.screenshot({ path: fileURLToPath(new URL("../../design/recall-workbench-demo.png", import.meta.url)), animations: "disabled" });
  await workbench.close();
  const shortPanel = await browser.newPage({ viewport: { width: 420, height: 420 } });
  await shortPanel.goto(`${server.resolvedUrls.local[0]}?view=panel`, { waitUntil: "networkidle" });
  await shortPanel.locator("#menu-demo-m3").click();
  await shortPanel.getByRole("menuitem", { name: "撤回这条记录" }).click();
  await shortPanel.locator("#restore-demo-m3").waitFor();
  for (const height of [420, 500, 540, 600, 700]) {
    await shortPanel.setViewportSize({ width: 420, height });
    const coverage = await shortPanel.locator("#restore-demo-m3").evaluate((button) => {
      const rect = button.getBoundingClientRect();
      const top = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
      return rect.top >= 0 && rect.bottom <= innerHeight && (top === button || button.contains(top));
    });
    assert.equal(coverage, true, `${height}px 高度下恢复按钮应可见且未被遮挡`);
  }
  await shortPanel.close();
  const shortText = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  await shortText.goto(`${server.resolvedUrls.local[0]}?view=workbench`, { waitUntil: "networkidle" });
  await shortText.getByRole("button", { name: "新建会话" }).click();
  await shortText.getByPlaceholder("给会话命名").fill("短消息验收");
  await shortText.getByRole("button", { name: "建立", exact: true }).click();
  await shortText.getByLabel("原文草稿").fill("好");
  await shortText.getByRole("button", { name: "加入消息", exact: true }).click();
  await shortText.locator(".message-menu summary").click();
  const menuFits = await shortText.locator(".message-menu-items").evaluate((menu) => {
    const bounds = menu.getBoundingClientRect();
    const column = document.querySelector(".conversation").getBoundingClientRect();
    const item = menu.querySelector("button").getBoundingClientRect();
    const hit = document.elementFromPoint(item.left + item.width / 2, item.top + item.height / 2);
    return bounds.left >= column.left && bounds.right <= column.right &&
      hit === menu.querySelector("button");
  });
  assert.equal(menuFits, true, "单字消息的菜单应完整留在聊天列且可点击");
  await shortText.screenshot({ path: fileURLToPath(new URL("../../design/recall-short-message-menu-demo.png", import.meta.url)), animations: "disabled" });
  await shortText.keyboard.press("Escape");
  await shortText.getByRole("button", { name: "轻面板" }).click();
  await shortText.setViewportSize({ width: 420, height: 420 });
  await shortText.locator(".message-menu summary").click();
  await shortText.getByRole("menuitem", { name: "撤回这条记录" }).click();
  const shortRestore = shortText.locator(".recall-placeholder button");
  await shortRestore.waitFor();
  assert.equal(await shortRestore.evaluate((button) => {
    const rect = button.getBoundingClientRect();
    const top = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
    return rect.top >= 0 && rect.bottom <= innerHeight && (top === button || button.contains(top));
  }), true, "单字消息的恢复按钮不应被下方区域遮挡");
  await shortText.screenshot({ path: fileURLToPath(new URL("../../design/recall-short-panel-demo.png", import.meta.url)), animations: "disabled" });
  await shortRestore.click();
  assert.equal(await shortText.locator(".message-bubble").filter({ hasText: "好" }).count(), 1);
  await shortText.close();
  console.log("撤回/恢复保留草稿与消息位置，旧分析没有复活。");
} finally {
  await browser?.close();
  await server.httpServer.close();
}
