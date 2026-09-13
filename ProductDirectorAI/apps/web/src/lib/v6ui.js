/**
 * V6-16 前端可测试逻辑（独立复核 BUG-01/02/06/07/08/09）。
 *
 * 这些函数刻意与 React 组件分离：复核要求的"参数变动使预览失效""时长选项来自后端支持"
 * "Profile 标签可区分平台""导航在窄窗口仍有可访问名称"都能用 node:test 直接验证，
 * 而不是只靠人工点击。
 */

/** 基础预演的时长选项：后端 PlanRequest/PlanUpdate 支持的 Literal[5,6,7,8]。 */
export const PREVIEW_DURATION_OPTIONS = [5, 6, 7, 8];

/** 基础预演固定 3 镜头（V1 产品合同）。 */
export const PREVIEW_SHOT_COUNT = 3;

export function durationLabel(seconds, fps = 24) {
  return `${seconds} 秒 · ${seconds * fps} 帧 @ ${fps}fps`;
}

/** 生成/更新计划时把时长与输出规格一起下发（BUG-01/03：不再写死 6 秒/不丢 output）。 */
export function buildTemplatePlanBody({ assetId, intent, durationSeconds, output, cropAnchor }) {
  const seconds = Number(durationSeconds) || 6;
  return {
    product_asset_id: assetId,
    intent,
    ratio: "9:16",
    duration_seconds: seconds,
    output: { ...output, duration_seconds: seconds },
    crop_anchor: cropAnchor,
  };
}

/** 保存计划时必须带上 output（BUG-03：否则分辨率改动在启动时丢失）。 */
export function buildPlanUpdateBody({ intent, shots, cropAnchor, output, durationSeconds }) {
  return {
    intent,
    shots,
    crop_anchor: cropAnchor,
    output: { ...output, duration_seconds: Number(durationSeconds) || 6 },
    duration_seconds: Number(durationSeconds) || 6,
  };
}

/** 描述词是否与当前计划一致（不一致 ⇒ 计划待更新，旧确认失效，BUG-02）。 */
export function planStaleness({ intent, plan }) {
  const text = (intent || "").trim();
  if (!plan) return { stale: text.length > 0, reason: text ? "尚未生成计划" : "" };
  const saved = (plan.intent || "").trim();
  if (saved !== text) {
    return { stale: true, reason: "描述已修改：旧分镜与旧确认不再对应当前要求，必须重新生成或保存" };
  }
  return { stale: false, reason: "" };
}

/** 明确说明当前管线不支持的要求（BUG-02：不支持要可见，不静默降级）。 */
const UNSUPPORTED_HINTS = [
  { pattern: /人物|演员|模特|手部|拿着|握住|接触|按压|按下/u, capability: "人物/手部互动",
    detail: "需要 V4 人物层与互动几何（contact/interaction 预置），基础三镜头预演不包含" },
  { pattern: /星空投影|投影效果|光斑|氛围灯/u, capability: "投影/光效",
    detail: "需要 H3 背景或后期合成层；产品本体渲染不产生投影光斑" },
  { pattern: /开口说话|口播|真人出镜/u, capability: "真人出镜/口播",
    detail: "需要真人素材或授权人物层；本系统只做产品渲染与配音" },
  { pattern: /拆解|爆炸图|内部结构/u, capability: "结构拆解",
    detail: "需要可分离的部件模型；单网格模型无法拆解" },
];

export function detectUnsupportedRequirements(intent) {
  const text = intent || "";
  return UNSUPPORTED_HINTS.filter((item) => item.pattern.test(text))
    .map(({ capability, detail }) => ({ capability, detail }));
}

/** 批次预览指纹：与后端 _batch_preview_hash 覆盖同样字段，用于前端判断"预览是否仍有效"（BUG-06）。 */
export function batchFormFingerprint({ planId, profileIds, variations, maxConcurrent, productVersionIds }) {
  return JSON.stringify({
    plan_id: planId || "",
    profile_ids: [...(profileIds || [])].sort(),
    variations: Number(variations) || 1,
    max_concurrent: Number(maxConcurrent) || 1,
    product_version_ids: [...(productVersionIds || [])].sort(),
  });
}

export function previewState({ preview, previewFingerprint, currentFingerprint, loading, error }) {
  if (loading) return { valid: false, reason: "loading", message: "正在按新参数重新预览…" };
  if (error) return { valid: false, reason: "error", message: `预览失败：${error}` };
  if (!preview) return { valid: false, reason: "missing", message: "请先预览展开" };
  if (previewFingerprint !== currentFingerprint) {
    return { valid: false, reason: "stale", message: "参数已变更：旧预览已失效，请重新预览" };
  }
  return { valid: true, reason: "ok", message: `预览有效：将展开 ${preview.expanded_count} 项` };
}

/** Profile 选项标签：必须能区分平台（BUG-09）。 */
export function profileOptionLabel(profile) {
  const platform = profile.platform || profile.profile_key || "unknown";
  const name = profile.name || profile.profile_key || "";
  const version = profile.version ? `v${profile.version}` : "";
  const status = profile.status === "DRAFT" ? "（草稿）" : "";
  const bits = [platformLabel(platform), name, version, profile.aspect_ratio, profile.locale, status]
    .filter(Boolean);
  return bits.join(" · ");
}

export function platformLabel(platform) {
  return {
    tiktok: "TikTok", youtube: "YouTube", instagram: "Instagram", facebook_page: "Facebook Page",
    marketplace: "Marketplace", pinterest: "Pinterest",
  }[platform] || platform;
}

/** 发布计划（生产计划）请求体：镜头数与总帧数来自真实输入，不再固定 3 镜头/144 帧（BUG-01）。 */
export function buildProductionPlanBody({ assetId, profileId, intent, shots, locale, voiceoverText,
                                          acceptUnverifiedAppearance }) {
  return {
    product_asset_id: assetId,
    profile_id: profileId,
    intent,
    locale: locale || "es-MX",
    voiceover_text: voiceoverText || "",
    shots: shots.map((shot, index) => ({
      id: shot.id || `shot_${String(index + 1).padStart(2, "0")}`,
      name: shot.name || `场景 ${index + 1}`,
      camera: shot.camera || "static",
      focal_length_mm: Number(shot.focal_length_mm) || 35,
      duration_frames: Number(shot.duration_frames) || 72,
      caption_text: shot.caption_text || "",
    })),
    accept_unverified_appearance: Boolean(acceptUnverifiedAppearance),
  };
}

/** 导航项的可访问名称（BUG-08：窄窗口隐藏文字后仍要能读屏与悬浮提示）。 */
export function navAccessibility(items, activeId) {
  return items.map(([id, label]) => ({
    id,
    label,
    ariaLabel: label,
    title: label,
    ariaCurrent: id === activeId ? "page" : undefined,
  }));
}
