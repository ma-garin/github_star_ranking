const { test, expect } = require("@playwright/test");
const AxeBuilder = require("@axe-core/playwright").default;
const JA = /[ぁ-んァ-ヶ一-龠]/;

async function ready(page, hash = "") {
  const errors = [];
  page.on("pageerror", e => errors.push(e.message));
  await page.goto("/" + hash);
  await expect(page.locator("#rows .rrow").first()).toBeVisible();
  return errors;
}

test("ランキング: 表示・横スクロールなし・概要は日本語", async ({ page }) => {
  const errors = await ready(page);
  expect(await page.locator("#rows .rrow").count()).toBeGreaterThan(5);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  for (const t of await page.locator("#rows .ds").allTextContents()) expect(t).toMatch(JA);
  expect(errors).toEqual([]);
});

test("ランキング: 前日比・日付移動・絞り込み・詳細と戻る", async ({ page }) => {
  await ready(page);
  await page.click("[data-k=daily]");
  await expect(page.locator("[data-k=daily]")).toHaveAttribute("aria-pressed", "true");
  await page.click("[data-k=weekly]");
  if (await page.locator("#prev").isEnabled()) { await page.click("#prev"); await page.click("#next"); }
  await page.fill("#q", "zzzz-存在しない");
  await expect(page.locator("#rows")).toContainText("該当するリポジトリはありません");
  await page.fill("#q", "");
  await page.locator("#rows .rrow").first().click();
  await expect(page.locator("#sheet")).toBeVisible();
  await page.goBack();
  await expect(page.locator("#sheet")).toBeHidden();
});

test("トレンドとAIニュース: 表示と詳細", async ({ page }) => {
  await ready(page);
  await page.click("nav button[data-v=trend]");
  await expect(page.locator("#bump svg")).toBeVisible();
  await page.click("nav button[data-v=news]");
  await expect(page.locator(".hero")).toBeVisible();
  await page.locator(".hero").click();
  await expect(page.locator(".nd h3")).toBeVisible();
});

test("設定: テーマと起動画面が再読み込み後も保たれる", async ({ page }) => {
  await ready(page);
  await page.click("nav button[data-v=set]");
  await page.click("[data-sp=look]");
  await page.click("[data-set=theme] [data-val=dark]");
  await page.click("[data-set=start] [data-val=news]");
  await page.reload();
  await expect(page.locator(".hero")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.dataset.theme)).toBe("dark");
});

test("データ取得失敗時は再試行ボタンを出す", async ({ page }) => {
  await page.route("**/data/app.json", r => r.abort());
  await page.goto("/");
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  await expect(page.locator("#retry")).toBeVisible();
});

for (const theme of ["light", "dark"]) {
  test(`アクセシビリティ（${theme}）: 重大・深刻な違反なし`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: theme });
    await ready(page);
    for (const v of ["rank", "news", "set"]) {
      await page.click(`nav button[data-v=${v}]`);
      const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
      const bad = r.violations.filter(x => ["critical", "serious"].includes(x.impact));
      expect(bad.map(x => `${v}: ${x.id} (${x.nodes.length}) ${x.nodes[0]?.target}`)).toEqual([]);
    }
  });
}

test("今すぐ集計: 未連携なら連携シート、連携済みなら実行→反映", async ({ page }) => {
  const errors = await ready(page);
  await page.click("#runNow");
  await expect(page.locator("#loginBox")).toBeVisible();
  await page.click("#sheet .x");
  await page.evaluate(() => localStorage.setItem("gh_token", "ghu_test"));
  let dispatched = false;
  await page.route("https://api.github.com/**", async route => {
    const u = route.request().url();
    if (u.endsWith("/dispatches")) { dispatched = true; return route.fulfill({ status: 204, body: "" }); }
    if (u.includes("/runs?")) return route.fulfill({ json: { workflow_runs: [{ id: 7, created_at: new Date().toISOString() }] } });
    if (u.endsWith("/runs/7")) return route.fulfill({ json: { status: "completed", conclusion: "success" } });
    return route.abort();
  });
  await page.route(/data\/app\.json\?t=/, async route => {
    const r = await route.fetch(); const j = await r.json(); j.generated_at = "2099-01-01T06:50:00+09:00";
    return route.fulfill({ json: j });
  });
  await page.click("#runNow");
  await expect(page.locator("#runmsg")).toHaveText("最新の集計を反映しました");
  expect(dispatched).toBe(true);
  expect(errors).toEqual([]);
});
