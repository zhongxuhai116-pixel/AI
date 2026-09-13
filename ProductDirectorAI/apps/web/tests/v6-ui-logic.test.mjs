/**
 * V6-16 前端逻辑测试（node:test）：独立复核 BUG-01/02/03/06/07/08/09 的可复现验证。
 * 运行：node --test tests/v6-ui-logic.test.mjs
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  PREVIEW_DURATION_OPTIONS, PREVIEW_SHOT_COUNT, batchFormFingerprint, buildPlanUpdateBody,
  buildProductionPlanBody, buildTemplatePlanBody, detectUnsupportedRequirements, durationLabel,
  navAccessibility, planStaleness, platformLabel, previewState, profileOptionLabel,
} from "../src/lib/v6ui.js";

test("BUG-01 时长选项来自后端支持范围（5–8 秒），不是写死的假下拉", () => {
  assert.deepEqual(PREVIEW_DURATION_OPTIONS, [5, 6, 7, 8]);
  assert.equal(PREVIEW_SHOT_COUNT, 3);
  assert.equal(durationLabel(5), "5 秒 · 120 帧 @ 24fps");
  assert.equal(durationLabel(8), "8 秒 · 192 帧 @ 24fps");
  const body = buildTemplatePlanBody({
    assetId: "asset-1", intent: "展示", durationSeconds: 5,
    output: { width: 540, height: 960, fps: 24, duration_seconds: 6 }, cropAnchor: "center",
  });
  assert.equal(body.duration_seconds, 5);
  assert.equal(body.output.duration_seconds, 5, "输出规格必须跟随所选时长，不能固定 6 秒");
});

test("BUG-01 生产计划按真实镜头数与帧数下发（2–8 镜头，不再固定 3×48）", () => {
  const shots = [72, 96, 72, 72, 48].map((frames, index) => ({
    name: `场景 ${index + 1}`, camera: "dolly_in", duration_frames: frames,
  }));
  const body = buildProductionPlanBody({
    assetId: "asset-1", profileId: "tiktok-mx-9x16-esmx", intent: "宇航员广告",
    shots, locale: "es-MX", voiceoverText: "Hola", acceptUnverifiedAppearance: true,
  });
  assert.equal(body.shots.length, 5);
  assert.equal(body.shots.reduce((sum, shot) => sum + shot.duration_frames, 0), 360);
  assert.equal(body.accept_unverified_appearance, true);
  assert.equal(body.shots[0].id, "shot_01");
  assert.equal(body.shots[4].duration_frames, 48);
});

test("BUG-02 描述变更使计划待更新，旧确认不能沿用", () => {
  const plan = { intent: "原始描述" };
  assert.equal(planStaleness({ intent: "原始描述", plan }).stale, false);
  const changed = planStaleness({ intent: "原始描述 + 加一句", plan });
  assert.equal(changed.stale, true);
  assert.match(changed.reason, /旧分镜与旧确认/);
  assert.equal(planStaleness({ intent: "有描述", plan: null }).stale, true);
});

test("BUG-02 不支持的要求必须可见（人物互动/投影/口播/拆解）", () => {
  const hints = detectUnsupportedRequirements("一只手拿着台灯按下按钮，天花板出现星空投影");
  const capabilities = hints.map((item) => item.capability);
  assert.ok(capabilities.includes("人物/手部互动"));
  assert.ok(capabilities.includes("投影/光效"));
  assert.equal(detectUnsupportedRequirements("产品在纯色背景上旋转展示").length, 0);
});

test("BUG-03 保存计划必须带上输出规格（分辨率改动不再丢失）", () => {
  const body = buildPlanUpdateBody({
    intent: "x", shots: [{ id: "shot_01", duration_frames: 120 }], cropAnchor: "center",
    output: { width: 1080, height: 1920, fps: 24 }, durationSeconds: 5,
  });
  assert.equal(body.output.width, 1080);
  assert.equal(body.output.height, 1920);
  assert.equal(body.duration_seconds, 5);
  assert.equal(body.output.duration_seconds, 5);
});

test("BUG-06 参数变动或预览失败都使预览失效，创建按钮必须禁用", () => {
  const base = { planId: "p1", profileIds: ["tiktok"], variations: 2, maxConcurrent: 2,
                 productVersionIds: ["v1"] };
  const fingerprint = batchFormFingerprint(base);
  const preview = { expanded_count: 2 };
  assert.equal(previewState({ preview, previewFingerprint: fingerprint,
                              currentFingerprint: fingerprint, loading: false, error: null }).valid, true);
  const changedVariations = batchFormFingerprint({ ...base, variations: 3 });
  const stale = previewState({ preview, previewFingerprint: fingerprint,
                               currentFingerprint: changedVariations, loading: false, error: null });
  assert.equal(stale.valid, false);
  assert.equal(stale.reason, "stale");
  assert.equal(previewState({ preview, previewFingerprint: fingerprint,
                              currentFingerprint: fingerprint, loading: false, error: "不相容" }).valid, false);
  assert.equal(previewState({ preview: null, previewFingerprint: null,
                              currentFingerprint: fingerprint, loading: false, error: null }).reason, "missing");
  const changedConcurrency = batchFormFingerprint({ ...base, maxConcurrent: 4 });
  assert.notEqual(changedConcurrency, fingerprint, "并发上限变化也必须使预览失效");
  const changedPlan = batchFormFingerprint({ ...base, planId: "p2" });
  assert.notEqual(changedPlan, fingerprint, "切换计划必须使预览失效（BUG-07）");
});

test("BUG-07 计划切换后指纹变化，用于丢弃旧产品版本与旧预览", () => {
  const first = batchFormFingerprint({ planId: "p1", profileIds: ["tiktok"], variations: 1,
                                       maxConcurrent: 2, productVersionIds: ["v1"] });
  const second = batchFormFingerprint({ planId: "p2", profileIds: ["tiktok"], variations: 1,
                                        maxConcurrent: 2, productVersionIds: ["v2"] });
  assert.notEqual(first, second);
});

test("BUG-09 Profile 选项必须能区分平台/名称/版本/比例/语言", () => {
  const tiktok = profileOptionLabel({ profile_key: "tiktok-mx-9x16-esmx", platform: "tiktok",
                                      name: "TikTok 墨西哥 9:16 西语", version: 3,
                                      aspect_ratio: "9:16", locale: "es-MX" });
  const youtube = profileOptionLabel({ profile_key: "youtube-shorts-9x16-en", platform: "youtube",
                                       name: "YouTube Shorts 9:16 英语", version: 1,
                                       aspect_ratio: "9:16", locale: "en-US" });
  assert.notEqual(tiktok, youtube, "两个 9:16 Profile 必须能区分");
  assert.match(tiktok, /TikTok/);
  assert.match(tiktok, /v3/);
  assert.match(tiktok, /es-MX/);
  assert.match(youtube, /YouTube/);
  assert.equal(platformLabel("facebook_page"), "Facebook Page");
  const draft = profileOptionLabel({ platform: "marketplace", name: "Marketplace 1:1",
                                     aspect_ratio: "1:1", locale: "es-MX", status: "DRAFT" });
  assert.match(draft, /草稿/, "草稿配置必须显式标记");
});

test("BUG-08 导航项在窄窗口仍有可访问名称与悬浮提示", () => {
  const items = [["console", "总控台"], ["batch", "批次生产"], ["publish", "发布与审批"]];
  const nav = navAccessibility(items, "batch");
  assert.equal(nav.length, 3);
  for (const item of nav) {
    assert.equal(item.ariaLabel, item.label);
    assert.equal(item.title, item.label);
  }
  assert.equal(nav[1].ariaCurrent, "page");
  assert.equal(nav[0].ariaCurrent, undefined);
});
