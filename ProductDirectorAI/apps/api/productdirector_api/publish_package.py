"""V6-05 发布包：包结构、Manifest、许可与审批、ZIP 打包与校验。

诚实边界（对应主规划 12.7）：
- 每个 Profile/语言版本一个独立包；批次可再生成聚合 ZIP。包先 build → verify → approve，
  **审批后不可变**（内容哈希绑定）。
- 包内不得包含 API Key、平台 token、用户隐私或过期临时下载链接；`Publish/request.template.json`
  是**无凭证**的发布请求模板，不是可自动执行的脚本。
- 素材许可遵守：有权用某首 BGM 做成片，不等于有权单独分发原始音乐 → 音乐原文件
  **默认不入包**，只保留许可引用与混音记录。
- 字幕/配音关闭时 Manifest 标 `disabled`，清单显示"不启用"，不能显示"已生成"。
- ZIP 条目防目录穿越；内容哈希校验必须通过。
"""
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

SCHEMA_VERSION = "1.0"
PACKAGE_ROLES = (
    "video", "clean_master", "subtitle", "audio", "image", "copy", "metadata", "publish_template",
)
FORBIDDEN_MANIFEST_KEYS = (
    "api_key", "apikey", "access_token", "refresh_token", "client_secret", "password",
    "authorization", "cookie", "session_token", "csrf", "owner_token", "worker_token",
)
SECRET_PATTERNS = (
    "sk-", "Bearer ", "-----BEGIN", "xoxb-", "ghp_", "ya29.",
)


class PackageError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    def as_detail(self) -> dict:
        return {"code": self.code, "detail": self.message}


def file_entry(path: Path, *, role: str, arcname: str, mime: str = "") -> dict:
    """Manifest 中的文件条目：路径/hash/大小/mime/role。"""
    data = path.read_bytes()
    return {
        "path": arcname,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
        "mime": mime or guess_mime(path),
        "role": role,
    }


def guess_mime(path: Path) -> str:
    return {
        ".mp4": "video/mp4", ".mov": "video/quicktime", ".srt": "application/x-subrip",
        ".vtt": "text/vtt", ".wav": "audio/wav", ".mp3": "audio/mpeg", ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg", ".png": "image/png", ".json": "application/json",
        ".txt": "text/plain", ".md": "text/markdown", ".zip": "application/zip",
    }.get(path.suffix.lower(), "application/octet-stream")


def check_manifest_secrets(manifest: dict) -> list[str]:
    """Manifest 不得携带凭证/隐私/临时链接（构建时与验证时都要查）。"""
    problems: list[str] = []

    def walk(node, path: str = "$"):
        if isinstance(node, dict):
            for key, value in node.items():
                lowered = str(key).lower()
                if any(bad in lowered for bad in FORBIDDEN_MANIFEST_KEYS):
                    problems.append(f"{path}.{key}：Manifest 不允许包含凭证类字段")
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")
        elif isinstance(node, str):
            for pattern in SECRET_PATTERNS:
                if pattern.lower() in node.lower():
                    problems.append(f"{path}：疑似凭证内容（命中 {pattern!r}）")
            if node.startswith(("http://", "https://")) and any(
                token in node.lower() for token in ("x-amz-signature", "expires=", "token=", "sig=")
            ):
                problems.append(f"{path}：疑似带签名的临时下载链接，不得入包")

    walk(manifest)
    return problems


def build_layout(
    *, package_id: str, version: int, has_clean_master: bool, subtitle_locale: str | None,
    subtitle_formats: list[str], include_voice_file: bool, include_mixed_audio: bool,
    include_thumbnail: bool,
) -> list[str]:
    """按主规划 12.7 生成包内路径清单（未启用的内容不出现，也不假装已生成）。"""
    files = [f"publish-package_{package_id}_v{version}/manifest.json",
             f"publish-package_{package_id}_v{version}/video/final.mp4",
             f"publish-package_{package_id}_v{version}/copy/caption.txt",
             f"publish-package_{package_id}_v{version}/copy/hashtags.txt",
             f"publish-package_{package_id}_v{version}/copy/product_facts.json",
             f"publish-package_{package_id}_v{version}/metadata/platform_profile.json",
             f"publish-package_{package_id}_v{version}/metadata/lineage.json",
             f"publish-package_{package_id}_v{version}/metadata/qa_report.json",
             f"publish-package_{package_id}_v{version}/metadata/rights_manifest.json",
             f"publish-package_{package_id}_v{version}/publish/request.template.json",
             f"publish-package_{package_id}_v{version}/publish/README.md"]
    if has_clean_master:
        files.append(f"publish-package_{package_id}_v{version}/video/clean_master.mp4")
    if subtitle_locale and subtitle_formats:
        for fmt in subtitle_formats:
            files.append(f"publish-package_{package_id}_v{version}/subtitles/{subtitle_locale}.{fmt}")
    if include_voice_file:
        files.append(f"publish-package_{package_id}_v{version}/audio/voice.wav")
    if include_mixed_audio:
        files.append(f"publish-package_{package_id}_v{version}/audio/mixed.wav")
    if include_thumbnail:
        files.append(f"publish-package_{package_id}_v{version}/images/thumbnail.jpg")
    return files


def build_manifest(
    *, package_id: str, version: int, project_id: str, product_version_id: str,
    plan_id: str, profile_id: str, profile_version: int, locale: str, batch_id: str | None,
    run_id: str, files: list[dict], duration_s: float, fps: int, resolution: str,
    qa: dict, approval_ref: dict, rights_refs: dict, warnings: list[str],
    subtitles_state: dict, voice_state: dict, music_state: dict,
    publish_template_ref: str, cost_summary: dict | None = None, created_at: str = "",
) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "package_id": package_id,
        "package_version": version,
        "project_id": project_id,
        "product_version_id": product_version_id,
        "plan_id": plan_id,
        "profile": {"profile_id": profile_id, "profile_version": profile_version},
        "batch_id": batch_id,
        "run_id": run_id,
        "locale": locale,
        "video": {"duration_s": duration_s, "fps": fps, "resolution": resolution},
        "files": files,
        "qa": qa,
        "approval_ref": approval_ref,
        "rights_refs": rights_refs,
        "subtitles": subtitles_state,
        "voice": voice_state,
        "music": music_state,
        "publish_template": publish_template_ref,
        "cost_summary": cost_summary or {"note": "成本账本属于 V6-06；此处不编造数字"},
        "warnings": warnings,
        "created_at": created_at,
    }


def disabled_state(enabled: bool, reason: str) -> dict:
    """关闭态必须明确写 disabled 并说明原因，清单不允许显示成"已生成"。"""
    return {"enabled": enabled, "status": "enabled" if enabled else "disabled", "reason": reason}


def rights_manifest(*, profile: dict, music: dict, voice: dict, fonts: list[dict], materials: list[dict]) -> dict:
    """许可清单：明确哪些内容可入包、哪些只保留引用。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "profile_rules": {
            "platform": profile.get("platform"),
            "rules_source_url": profile.get("rules_source_url", ""),
            "rules_verified_at": profile.get("rules_verified_at", ""),
            "note": "未核验的规则不得当作平台事实；发布前必须按官方资料重新核验",
        },
        "music": music,
        "voice": voice,
        "fonts": fonts,
        "materials": materials,
        "packaging_policy": {
            "music_original_file_included": False,
            "reason": "有权用某首 BGM 制作成片，不等于有权单独分发原始音乐；只保留许可引用与混音记录",
        },
    }


def publish_request_template(
    *, package_id: str, package_version: int, platform: str, locale: str,
    output_target: str, suggested_visibility: str, disclosure_flags: list[str],
) -> dict:
    """无凭证发布请求模板：用户或 Agent 提交给本系统接口，不含任何平台 token。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "note": "这是无凭证的发布请求模板；需要平台授权与有效审批后才可提交，不能直接执行",
        "package": {"package_id": package_id, "package_version": package_version},
        "target": {"platform": platform, "output_target": output_target,
                   "suggested_visibility": suggested_visibility},
        "locale": locale,
        "disclosure_flags": disclosure_flags,
        "requires": ["platform_authorization", "valid_approval", "visibility_confirmation"],
        "endpoint": "POST /api/v1/publishing/jobs",
    }


def publish_readme(platform: str, locale: str, visibility: str) -> str:
    return (
        f"# 发布说明（{platform} · {locale}）\n\n"
        "1. 本包含可播放成片、字幕侧车、文案、封面与 Manifest；**不包含**任何 API Key 或平台 token。\n"
        "2. 发布需要在系统中完成平台授权、预检与审批后才可提交（见 `publish/request.template.json`）。\n"
        f"3. 建议可见性：{visibility}（仅建议，不代替平台要求的逐次选择）。\n"
        "4. 素材许可状态见 `metadata/rights_manifest.json`；音乐原文件按许可策略默认不入包。\n"
        "5. 包内所有文件哈希见 `manifest.json`；修改任何文件都会使审批失效。\n"
    )


def verify_package(directory: Path, manifest: dict) -> list[str]:
    """校验包内容：文件齐备、哈希一致、无多余文件、Manifest 无凭证。"""
    failures: list[str] = []
    failures.extend(check_manifest_secrets(manifest))
    declared = {entry["path"]: entry for entry in manifest.get("files", [])}
    prefix = f"publish-package_{manifest['package_id']}_v{manifest['package_version']}/"
    for relative, entry in declared.items():
        path = directory / relative
        if not path.exists():
            failures.append(f"缺少文件: {relative}")
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != entry["sha256"]:
            failures.append(f"哈希不一致: {relative}")
    actual = {
        path.relative_to(directory).as_posix()
        for path in directory.rglob("*") if path.is_file()
    }
    expected = set(declared) | {f"{prefix}manifest.json"}
    extra = sorted(actual - expected)
    if extra:
        failures.append(f"包含未声明文件: {extra[:5]}")
    missing_manifest = prefix + "manifest.json"
    if missing_manifest not in actual:
        failures.append("缺少 manifest.json")
    return failures


def zip_package(directory: Path, out_path: Path) -> dict:
    """打包 ZIP：条目名防目录穿越，内容哈希在写入后复核。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    members: list[str] = []
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(directory.rglob("*")):
            if not path.is_file():
                continue
            arcname = path.relative_to(directory).as_posix()
            if arcname.startswith("/") or ".." in Path(arcname).parts:
                raise PackageError("unsafe_zip_entry", f"不安全的 ZIP 条目名: {arcname}")
            archive.write(path, arcname)
            members.append(arcname)
    digest = hashlib.sha256(out_path.read_bytes()).hexdigest()
    with zipfile.ZipFile(out_path) as archive:
        if archive.testzip() is not None:
            raise PackageError("zip_corrupt", "ZIP 校验失败")
        names = archive.namelist()
        if set(names) != set(members):
            raise PackageError("zip_mismatch", "ZIP 条目与写入清单不一致")
        for name in names:
            if name.startswith("/") or ".." in Path(name).parts:
                raise PackageError("unsafe_zip_entry", f"不安全的 ZIP 条目名: {name}")
    return {"path": str(out_path), "sha256": digest, "size_bytes": out_path.stat().st_size,
            "members": sorted(members), "member_count": len(members)}


def package_content_hash(files: list[dict]) -> str:
    """包内容哈希：绑定审批（任何文件变化都会使其失效）。"""
    payload = json.dumps([{k: entry[k] for k in ("path", "sha256", "role")} for entry in sorted(
        files, key=lambda item: item["path"])], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_package_request(*, profile_spec: dict, output: dict, qa: dict, subtitle_state: dict) -> list[str]:
    """打包前置校验：审批/QA/规格必须齐备，缺失即拒绝而不是生成半成品包。"""
    problems: list[str] = []
    if not qa.get("passed"):
        problems.append("QA 未通过：不能生成发布包（blocked/失败项需先修复或复核）")
    if not qa.get("approval_ref"):
        problems.append("缺少 QA 审批引用：包审批必须绑定 QA 结果")
    if output.get("aspect_ratio") and profile_spec.get("aspect_ratio") \
            and output["aspect_ratio"] != profile_spec["aspect_ratio"]:
        problems.append(
            f"成片比例 {output['aspect_ratio']} 与 Profile {profile_spec['aspect_ratio']} 不一致："
            "需按 crop_policy 处理，不能盲目裁切"
        )
    if subtitle_state.get("enabled") and not subtitle_state.get("files"):
        problems.append("字幕声明启用但没有侧车文件")
    return problems
