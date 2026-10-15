/* Optional real-browser smoke: needs Playwright and an installed Chromium.
 * Uses only synthetic contact data. No model is called by the rule greeting.
 */
"use strict";
const { chromium } = require("playwright");
(async () => {
  const browser = await chromium.launch({headless: true,
    executablePath: process.env.CHROMIUM_PATH || "/usr/bin/chromium", args: ["--no-sandbox"]});
  try {
    const page = await browser.newPage();
    await page.goto((process.env.SUPPORT_BROWSER_URL || "http://127.0.0.1:8080") + "/chat");
    await page.locator("#notice").check();
    await page.getByRole("button", {name: "开始咨询", exact: true}).click();
    await page.locator("#workspace").waitFor({state: "visible"});
    await page.locator("#message").fill("你好");
    await page.getByRole("button", {name: "发送", exact: true}).click();
    await page.locator("#messages").getByText("您好，我是网站 AI 客服。", {exact: false}).waitFor();
    await page.getByRole("button", {name: "申请人工接管", exact: true}).click();
    await page.getByRole("status").filter({hasText: "已申请人工接管"}).waitFor();
    await page.locator("#contact").fill("browser-demo@example.invalid");
    await page.locator("#need").fill("合成浏览器验收需求");
    await page.locator("#consent").check();
    await page.getByRole("button", {name: "提交联系线索", exact: true}).click();
    await page.getByRole("status").filter({hasText: "明确同意下保存"}).waitFor();
    await page.getByRole("button", {name: "开始新会话", exact: true}).click();
    await page.locator("#welcome").waitFor({state: "visible"});
    if (await page.locator("#consent").isChecked()) throw new Error("Consent was not reset");
    console.log("PASS: real browser notice/session/rule greeting/handoff/consented lead/reset. Real model NOT_CALLED.");
  } finally { await browser.close(); }
})().catch(() => { console.error("Browser smoke FAILED; no credentials or customer content logged."); process.exit(1); });
