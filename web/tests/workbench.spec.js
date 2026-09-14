import { test, expect } from "@playwright/test";
import fs from "node:fs/promises";
const ready = (page) =>
  expect(page.getByTestId("status")).toContainText("已生成", {
    timeout: 30000,
  });
async function change(page, action) {
  const response = page.waitForResponse(
    (r) => r.url().endsWith("/api/run") && r.status() === 200,
  );
  await action();
  const data = await (await response).json();
  await ready(page);
  return data;
}
async function start(page) {
  const response = page.waitForResponse(
    (r) => r.url().endsWith("/api/run") && r.status() === 200,
  );
  await page.goto("/");
  const run = await (await response).json();
  await ready(page);
  return run;
}

test("拖拽、固定基线、筛选与统计窗口不改变 trace，导出与 API 一致", async ({
  page,
  request,
}) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  let runRequests = 0;
  page.on("request", (r) => {
    if (r.url().endsWith("/api/run")) runRequests++;
  });
  const initial = await start(page);
  await expect(page.getByTestId("metrics")).toContainText("526 requests");
  await page
    .getByRole("button", { name: "固定为对比基线", exact: true })
    .click();
  await ready(page);
  const marker = page.getByTestId("editable-curve").getByTestId("handle-1");
  const box = await marker.boundingBox();
  const pending = page.waitForResponse(
    (r) => r.url().endsWith("/api/run") && r.status() === 200,
  );
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + 25, box.y - 20, { steps: 5 });
  await expect(page.getByTestId("status")).toContainText("拖动中");
  await page.waitForTimeout(600);
  expect(runRequests).toBe(1);
  await page.mouse.up();
  const edited = await (await pending).json();
  await ready(page);
  expect(edited.sha256).not.toBe(initial.sha256);
  expect(edited.config.seed).toBe(initial.config.seed);
  await page.mouse.move(10, 10);
  await page.screenshot({ path: "../docs/assets/workbench.png" });
  const frozen = await (
    await request.post("/api/analyze", {
      data: { run_id: initial.run_id, window: 10 },
    })
  ).json();
  expect(frozen.sha256).toBe(initial.sha256);
  const before = runRequests;
  await page.getByLabel("统计窗口", { exact: true }).selectOption("1");
  await ready(page);
  await page.getByLabel("任务筛选", { exact: true }).selectOption("chat");
  await ready(page);
  await page
    .getByLabel("Client 筛选", { exact: true })
    .selectOption("client-000000");
  await ready(page);
  await page.getByLabel("平滑窗口数").selectOption("3");
  await page.getByLabel("对数轴", { exact: true }).check();
  await page.getByLabel("放大开始").fill("50");
  await page.getByLabel("放大结束").fill("100");
  expect(runRequests).toBe(before);
  const download = page.waitForEvent("download");
  await page.getByRole("link", { name: "导出完整 Trace" }).click();
  const file = await (await download).path();
  const apiTrace = await request.get(`/api/files/${edited.run_id}/trace.jsonl`);
  expect(await fs.readFile(file)).toEqual(await apiTrace.body());
  const configDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出配置", exact: true }).click();
  const configFile = await (await configDownload).path();
  expect(JSON.parse(await fs.readFile(configFile, "utf8"))).toEqual(
    edited.config,
  );
  const svgDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出当前 SVG 图表" }).click();
  expect(await fs.readFile(await (await svgDownload).path(), "utf8")).toContain(
    "Serving 请求到达率",
  );
  expect(errors).toEqual([]);
});

test("任务、分布、显式 client、共享组可编辑，并能导入重放", async ({
  page,
}) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await start(page);
  await page.getByRole("button", { name: "任务", exact: true }).click();
  await page.locator("summary").filter({ hasText: "模型输出 tokens" }).click();
  const preview = page.waitForResponse(
    (r) => r.url().endsWith("/api/distribution") && r.status() === 200,
  );
  await change(page, () =>
    page.getByLabel("output_tokens mean", { exact: true }).fill("250"),
  );
  await preview;
  await expect(
    page
      .locator("details.distribution[open]")
      .getByText("抽样 CDF", { exact: true }),
  ).toBeVisible();
  await change(page, () =>
    page.getByLabel("Client 配置方式").selectOption("explicit"),
  );
  await change(page, () =>
    page.getByLabel("Client key", { exact: true }).fill("head-client"),
  );
  await page.getByRole("button", { name: "前缀", exact: true }).click();
  const edited = await change(page, () =>
    page.getByLabel("组 1 scope", { exact: true }).selectOption("global"),
  );
  expect(edited.config.prefix_groups[0].scope).toBe("global");
  await change(page, () =>
    page
      .getByLabel("导入配置文件")
      .setInputFiles({
        name: "replay.json",
        mimeType: "application/json",
        buffer: Buffer.from(JSON.stringify(edited.config)),
      }),
  );
  await page.getByRole("button", { name: "会话与增长", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "间隔—外部增长联合分布" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "前缀与属性", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "完整 block 重访间隔" }),
  ).toBeVisible();
  expect(errors).toEqual([]);
});

test("JSON 校验失败保留已生成结果，修正后可继续", async ({ page }) => {
  const run = await start(page);
  await page.getByRole("button", { name: "JSON", exact: true }).click();
  await page
    .getByLabel("完整配置", { exact: true })
    .fill('{"version":3,"tasks":[{}],"traffic":{}}');
  await page.getByRole("button", { name: "应用 完整配置" }).click();
  await expect(page.getByRole("alert").first()).toBeVisible();
  await expect(page.getByTestId("metrics")).toContainText("526 requests");
  const fixed = structuredClone(run.config);
  fixed.traffic.session_rate = 0;
  fixed.traffic.bursts = [];
  await page
    .getByLabel("完整配置", { exact: true })
    .fill(JSON.stringify(fixed));
  await change(page, () =>
    page.getByRole("button", { name: "应用 完整配置" }).click(),
  );
  await expect(page.getByTestId("metrics")).toContainText("0 requests");
});

test("移动端无横向溢出，配置与结果均可操作", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await start(page);
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  await page.screenshot({ path: "../docs/assets/workbench-mobile.png" });
  await change(page, () => page.getByLabel("Seed", { exact: true }).fill("99"));
  await page.getByRole("button", { name: "前缀与属性", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "公共前缀与历史复用" }),
  ).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  expect(errors).toEqual([]);
});

test("任务权重、burst 拖拽与周期配置作用于真实生成", async ({ page }) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await start(page);
  await page.getByRole("button", { name: "任务", exact: true }).click();
  await page.getByRole("button", { name: "在主图编辑", exact: true }).click();
  await expect(page.getByTestId("editable-curve")).toContainText(
    "Task 发起权重",
  );
  const result = await change(page, () =>
    page.getByTestId("editable-curve").getByTestId("handle-0").press("ArrowUp"),
  );
  expect(result.config.tasks[0].weight.points[0][1]).toBeGreaterThan(3);
  await page.getByRole("button", { name: "整体", exact: true }).click();
  const burst = page.locator(".burst-editor .handle").first();
  await burst.scrollIntoViewIfNeeded();
  const box = await burst.boundingBox();
  const response = page.waitForResponse(
    (r) => r.url().endsWith("/api/run") && r.status() === 200,
  );
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + 10, box.y - 10, { steps: 4 });
  await page.mouse.up();
  const changed = await (await response).json();
  await ready(page);
  expect(changed.config.traffic.bursts[0].multiplier).not.toBe(3);
  await change(page, () =>
    page.getByLabel("周期 / s（0 关闭）", { exact: true }).fill("300"),
  );
  await change(page, () =>
    page.getByLabel("相位 / s", { exact: true }).fill("30"),
  );
  expect(errors).toEqual([]);
});
