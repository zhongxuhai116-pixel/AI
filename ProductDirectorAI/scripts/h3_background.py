#!/usr/bin/env python3
"""V3-05：H3（自托管 ComfyUI，不收费）真实 AI 背景帧序列生成。

规格：Strict 合成的背景接真实 AI 生成（H3，自托管不收费）。
本脚本复用 apps/api 的 ComfyUI 客户端（提交/轮询/下载三段式，见
apps/api/productdirector_api/providers/comfyui.py），按 DirectorPlan 的镜头
描述生成一段**无产品**背景视频，再解码为 PNG 帧序列供 strict_composite.py 使用。

关键约束：
- 参考视频/参考图默认程序化合成中性影棚画面（**不含任何产品影像**），
  避免 H3 ref2v 把产品渗入背景；也可用 --reference-video / --reference-image 覆盖；
- H3 输出 576×1024，与 540×960 渲染同宽高比，ffmpeg 直接缩放解码到目标尺寸；
- 任何失败（服务不可达 / 节点校验拒绝 / 超时 / 帧数不足）如实记 BLOCKED，不产出假帧。

用法（云端，ComfyUI 在 localhost:8188）：
    PYTHONPATH=apps/api .venv/bin/python scripts/h3_background.py \
        --plan director-plan-72.json --frames 72 --width 540 --height 960 --out <dir>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from productdirector_api.providers import comfyui  # noqa: E402

# H3 视频模型的原生工作尺寸（与 540×960 同宽高比 0.5625）
H3_WIDTH, H3_HEIGHT = 576, 1024
# 镜头模板 → 背景视频的运动描述（供 prompt 组装）
CAMERA_MOTION_PHRASES = {
    "dolly_in": "slow camera push-in",
    "side_track": "gentle lateral tracking move",
    "hero_orbit": "slow orbital camera move",
    "static": "locked-off static camera",
}


def build_prompt(plan_path: Path | None, override: str = "") -> str:
    """按镜头描述组装背景 prompt；--prompt 显式给出时直接使用。"""
    if override:
        return override
    motions: list[str] = []
    if plan_path:
        try:
            payload = json.loads(plan_path.read_text(encoding="utf-8"))
            for shot in payload.get("shots", []):
                phrase = CAMERA_MOTION_PHRASES.get(str(shot.get("camera")))
                if phrase and phrase not in motions:
                    motions.append(phrase)
        except (OSError, json.JSONDecodeError):
            motions = []
    motion_text = ", ".join(motions) if motions else "slow subtle camera drift"
    return (
        "Empty professional product photography studio background, seamless dark backdrop, "
        f"soft cinematic lighting, {motion_text}. "
        "No product, no objects, no people, no text, no logo."
    )


def h3_length(frames: int) -> int:
    """H3 潜帧长度取 4k+1 且 ≥ frames，上限 124（V2 实测产能口径）。"""
    length = ((max(frames, 2) - 2) // 4 + 1) * 4 + 1
    return min(length, 124)


def make_reference_video(target: Path, length: int, width: int = H3_WIDTH, height: int = H3_HEIGHT) -> Path:
    """程序化中性参考视频：竖向柔光渐变 + 缓慢水平漂移，不含任何产品影像。"""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("生成参考视频需要 ffmpeg（未在 PATH 中找到）")
    frames_dir = target / "ref_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    yy, xx = np.mgrid[0:height, 0:width]
    for frame in range(length):
        shift = frame * 0.6  # 每帧 0.6px 的缓慢漂移，配合 prompt 的 subtle drift
        array = np.zeros((height, width, 3), dtype=np.float32)
        array[:, :, 0] = 0.06 + 0.05 * (yy / (height - 1))
        array[:, :, 1] = 0.06 + 0.05 * (yy / (height - 1))
        array[:, :, 2] = 0.08 + 0.07 * (yy / (height - 1))
        glow = np.exp(-(((xx - width / 2 - shift) / (width * 0.45)) ** 2 + ((yy - height * 0.35) / (height * 0.35)) ** 2))
        array += (0.10 * glow)[:, :, None]
        Image.fromarray((np.clip(array, 0, 1) * 255 + 0.5).astype(np.uint8)).save(frames_dir / f"ref_{frame:04d}.png")
    video = target / "reference.mp4"
    subprocess.run(
        [ffmpeg, "-y", "-framerate", "24", "-i", str(frames_dir / "ref_%04d.png"),
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)],
        check=True, capture_output=True,
    )
    return video


def decode_frames(video: Path, out_dir: Path, width: int, height: int, frames: int) -> list[Path]:
    """ffmpeg 解码并缩放到目标尺寸，命名 bg_XXXX.png（与 strict_composite 的排序兼容）。"""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("解码背景帧需要 ffmpeg（未在 PATH 中找到）")
    subprocess.run(
        [ffmpeg, "-y", "-i", str(video), "-vf", f"scale={width}:{height}",
         "-frames:v", str(frames), str(out_dir / "bg_%04d.png")],
        check=True, capture_output=True,
    )
    return sorted(out_dir.glob("bg_*.png"))


def main() -> int:
    parser = argparse.ArgumentParser(description="V3-05：H3 真实 AI 背景帧序列（自托管 ComfyUI，不收费）")
    parser.add_argument("--plan", default="", help="DirectorPlan JSON（按镜头描述组装 prompt）")
    parser.add_argument("--prompt", default="", help="显式背景描述（覆盖 --plan 组装结果）")
    parser.add_argument("--frames", type=int, default=72)
    parser.add_argument("--width", type=int, default=540)
    parser.add_argument("--height", type=int, default=960)
    parser.add_argument("--out", required=True, help="背景帧输出目录（bg_XXXX.png + background_manifest.json）")
    parser.add_argument("--seed", type=int, default=20260912)
    parser.add_argument("--steps", type=int, default=4, help="H3 turbo LoRA 采样步数（现场口径 4 步）")
    parser.add_argument("--reference-video", default="", help="显式参考视频（默认程序化生成中性影棚漂移画面）")
    parser.add_argument("--reference-image", default="", help="显式参考图（默认取程序化参考视频首帧）")
    parser.add_argument("--timeout", type=int, default=1800, help="生成超时秒数")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict = {
        "task": "V3-05 H3 真实 AI 背景",
        "provider": "h3-comfyui（自托管，不产生外部费用）",
        "base_url": comfyui.base_url(),
        "frames_requested": args.frames,
        "output_size": [args.width, args.height],
        "seed": args.seed,
        "blocked": False,
        "blocked_reason": "",
    }

    health = comfyui.health()
    manifest["comfyui_health"] = health
    if not health.get("reachable"):
        manifest["blocked"] = True
        manifest["blocked_reason"] = f"ComfyUI 不可达: {health.get('error')}"
        (out_dir / "background_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(manifest, ensure_ascii=False))
        return 1

    prompt = build_prompt(Path(args.plan) if args.plan else None, args.prompt)
    manifest["prompt"] = prompt
    length = h3_length(args.frames)
    manifest["h3_length"] = length

    with tempfile.TemporaryDirectory(prefix="pd_h3_bg_") as tmp:
        tmp_dir = Path(tmp)
        try:
            if args.reference_video:
                reference_video = Path(args.reference_video)
            else:
                reference_video = make_reference_video(tmp_dir, length)
            if args.reference_image:
                reference_image = Path(args.reference_image)
            else:
                reference_image = sorted((tmp_dir / "ref_frames").glob("ref_*.png"))[0]
        except (RuntimeError, IndexError, subprocess.CalledProcessError) as exc:
            manifest["blocked"] = True
            manifest["blocked_reason"] = f"参考素材准备失败: {exc}"
            (out_dir / "background_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(manifest, ensure_ascii=False))
            return 1

        try:
            video_name = comfyui.upload_image(reference_video.name, reference_video.read_bytes())
            image_name = comfyui.upload_image(reference_image.name, reference_image.read_bytes())
        except comfyui.ComfyUIError as exc:
            manifest["blocked"] = True
            manifest["blocked_reason"] = f"参考素材上传失败: {exc}"
            (out_dir / "background_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(manifest, ensure_ascii=False))
            return 1

        graph = comfyui.build_h3_video_graph(
            reference_video=video_name,
            product_image=image_name,
            prompt=prompt,
            prefix="pd_v305_bg",
            width=H3_WIDTH,
            height=H3_HEIGHT,
            length=length,
            steps=args.steps,
            seed=args.seed,
            fps=24,
        )
        try:
            prompt_id = comfyui.submit(graph)
        except comfyui.ComfyUIError as exc:
            manifest["blocked"] = True
            manifest["blocked_reason"] = f"H3 工作流提交被拒绝: {exc}"
            (out_dir / "background_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(manifest, ensure_ascii=False))
            return 1
        manifest["prompt_id"] = prompt_id

        deadline = time.time() + args.timeout
        record = None
        while time.time() < deadline:
            record = comfyui.history(prompt_id)
            if record is not None and comfyui.is_completed(record):
                break
            time.sleep(5)
        if record is None or not comfyui.is_completed(record):
            manifest["blocked"] = True
            manifest["blocked_reason"] = f"生成超时（>{args.timeout}s）或记录缺失"
            (out_dir / "background_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(manifest, ensure_ascii=False))
            return 1
        status = comfyui.status_text(record)
        manifest["status"] = status
        if status != "SUCCEEDED":
            manifest["blocked"] = True
            manifest["blocked_reason"] = f"H3 生成失败: {status}"
            (out_dir / "background_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(manifest, ensure_ascii=False))
            return 1

        videos = [item for item in comfyui.outputs(record) if item["filename"].lower().endswith((".mp4", ".webm"))]
        if not videos:
            manifest["blocked"] = True
            manifest["blocked_reason"] = "H3 输出中没有视频文件"
            (out_dir / "background_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(manifest, ensure_ascii=False))
            return 1
        video_bytes = comfyui.download(videos[0])
        video_path = out_dir / "background_source.mp4"
        video_path.write_bytes(video_bytes)
        manifest["source_video_sha256"] = hashlib.sha256(video_bytes).hexdigest()

    try:
        frames = decode_frames(video_path, out_dir, args.width, args.height, args.frames)
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        manifest["blocked"] = True
        manifest["blocked_reason"] = f"背景帧解码失败: {exc}"
        (out_dir / "background_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(manifest, ensure_ascii=False))
        return 1

    manifest["frames_written"] = len(frames)
    if len(frames) < args.frames:
        manifest["blocked"] = True
        manifest["blocked_reason"] = f"解码帧数 {len(frames)} < 请求 {args.frames}"
    (out_dir / "background_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False))
    return 1 if manifest["blocked"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
