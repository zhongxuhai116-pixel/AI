"""V5-06 重演生成驱动器：把 from-reference 冻结计划走完整 V3 生产链真实渲染成片。

步骤：Blender 五通道渲染 → 独立层差分 → 五通道校验 → 真实 H3 背景 → Strict 合成
（可选 V4 互动人物遮挡层）→ 双产品检测 → FFmpeg 成片 → Strict QA → 重演证据报告。

CLI 证据工具，与 API 严格管线共用同一套组件（render_product / validate_fidelity_passes /
h3_background / strict_composite / dual_product_check / strict_qa）。
引用追踪：recreate_report.json 记录参考片/分析/映射的溯源与哈希；参考片的
品牌/水印/音乐/人物不默认复制到成片。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

COLOR_CONTRACT = {
    "product_color_space": "display-srgb",
    "background_color_space": "display-srgb",
    "layer_color_space": "display-srgb",
    "working_space": "display-linear",
    "output_color_space": "display-srgb",
    "blender_view_transform": "AgX",
}


def run(command: list[str], log, extra_env: dict | None = None):
    completed = subprocess.run(command, capture_output=True, text=True, env={**os.environ, **(extra_env or {})})
    log.write(f"$ {' '.join(map(str, command))}\n")
    if completed.stdout:
        log.write(completed.stdout + "\n")
    if completed.stderr:
        log.write(completed.stderr + "\n")
    log.flush()
    return completed


def require(completed: subprocess.CompletedProcess, step: str, log) -> None:
    if completed.returncode != 0:
        raise SystemExit(f"{step} 失败 (exit={completed.returncode})，日志见 {log.name}")


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan-json", required=True, help="from-reference DirectorPlan JSON")
    parser.add_argument("--product-glb", required=True, help="产品 GLB 路径")
    parser.add_argument("--evidence", required=True, help="证据输出目录")
    parser.add_argument("--width", type=int, default=540)
    parser.add_argument("--height", type=int, default=960)
    parser.add_argument("--citation-json", default="", help="溯源引用块 JSON（reference/analysis/mapping）")
    parser.add_argument("--interaction-json", default="", help="V4 互动 JSON（action/prepare/contact/end/anchor/character）")
    parser.add_argument("--occlusion-range", default="", help="occlusion 必需帧区间 start,end（含）")
    parser.add_argument("--h3-background-dir", default="", help="复用已有 H3 背景帧目录（跳过真实生成）")
    args = parser.parse_args()

    plan = json.loads(Path(args.plan_json).read_text(encoding="utf-8"))
    shots = plan.get("shots") or []
    total_frames = sum(int(shot.get("duration_frames") or 0) for shot in shots)
    explicit = (plan.get("output") or {}).get("frame_count")
    if explicit:
        total_frames = int(explicit)
    if total_frames <= 0:
        raise SystemExit("计划没有有效镜头帧数")
    evidence = Path(args.evidence)
    evidence.mkdir(parents=True, exist_ok=True)
    log = (evidence / "recreate.log").open("w", encoding="utf-8")
    blender = shutil.which("blender") or "/usr/local/bin/blender"
    ffmpeg = shutil.which("ffmpeg") or "/usr/bin/ffmpeg"
    python = sys.executable

    plan_path = evidence / "plan.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    citation = read_json(Path(args.citation_json), None) if args.citation_json else None

    print("step 1/8 Blender 五通道渲染", flush=True)
    render = run([blender, "-b", "-P", str(REPO / "blender/scripts/render_product.py"), "--",
                  "--input", args.product_glb, "--output", str(evidence / "render"),
                  "--width", str(args.width), "--height", str(args.height),
                  "--frames", str(total_frames), "--plan", str(plan_path),
                  "--passes", "--layers"], log)
    require(render, "五通道渲染", log)

    print("step 2/8 独立层差分", flush=True)
    build = run([python, str(REPO / "scripts/build_layers.py"),
                 "--beauty", str(evidence / "render/passes/layers/plate_full"),
                 "--plate", str(evidence / "render/passes/layers/plate"),
                 "--mask", str(evidence / "render/passes/mask"),
                 "--occlusion", str(evidence / "render/passes/layers/occlusion"),
                 "--out", str(evidence / "layers")], log)
    require(build, "独立层差分", log)

    print("step 3/8 五通道与独立层校验", flush=True)
    layers_root = evidence / "layers_root"
    layers_root.mkdir(exist_ok=True)
    for name in ("plate_full", "plate", "occlusion"):
        target = evidence / "render/passes/layers" / name
        link = layers_root / name
        if link.exists() or link.is_symlink():
            link.unlink()
        if target.exists():
            link.symlink_to(target)
    for name in ("shadow", "reflection"):
        target = evidence / "layers" / name
        link = layers_root / name
        if link.exists() or link.is_symlink():
            link.unlink()
        if target.exists():
            link.symlink_to(target)
    validate = run([python, str(REPO / "blender/scripts/validate_fidelity_passes.py"),
                    "--passes", str(evidence / "render/passes"),
                    "--frames", str(total_frames), "--start-frame", "1",
                    "--layers", str(layers_root),
                    "--json", str(evidence / "passes_report.json")], log)
    require(validate, "五通道校验", log)

    occlusion_frames: list[int] = []
    interaction_qa = None
    if args.interaction_json:
        print("step 4/8 V4 互动预演与接触 QA", flush=True)
        previz = run([blender, "-b", "-P", str(REPO / "blender/scripts/render_interaction_previz.py"), "--",
                      "--input", args.product_glb, "--plan", str(plan_path),
                      "--interaction", args.interaction_json,
                      "--output", str(evidence / "previz"),
                      "--width", str(args.width), "--height", str(args.height)], log)
        require(previz, "互动预演", log)
        interaction_qa = read_json(evidence / "previz" / "contact_qa.json")
        if args.occlusion_range:
            start, end = (int(part) for part in args.occlusion_range.split(","))
            occlusion_frames = list(range(start, end + 1))
        else:
            interaction = json.loads(Path(args.interaction_json).read_text(encoding="utf-8"))
            occlusion_frames = list(range(int(interaction["prepare_frame"]), int(interaction["end_frame"]) + 1))
    else:
        print("step 4/8 跳过（无互动镜头）", flush=True)

    print("step 5/8 真实 H3 背景", flush=True)
    h3_dir = Path(args.h3_background_dir) if args.h3_background_dir else evidence / "h3_bg"
    h3_evidence = None
    if args.h3_background_dir:
        h3_evidence = {"reused": True, "dir": str(h3_dir)}
    else:
        h3 = run([python, str(REPO / "scripts/h3_background.py"),
                  "--plan", str(plan_path), "--frames", str(total_frames),
                  "--width", str(args.width), "--height", str(args.height),
                  "--out", str(h3_dir)],
                 log, extra_env={"PYTHONPATH": str(REPO / "apps" / "api")})
        require(h3, "H3 背景生成", log)
        for line in h3.stdout.splitlines() + h3.stderr.splitlines():
            if line.strip().startswith("{") and "frames_written" in line:
                try:
                    h3_evidence = json.loads(line.strip())
                except json.JSONDecodeError:
                    pass
        if h3_evidence is None and (h3_dir / "evidence.json").exists():
            h3_evidence = read_json(h3_dir / "evidence.json")

    print("step 6/8 Strict 合成", flush=True)
    strict_plan = {
        "frame_count": total_frames,
        "start_frame": 1,
        "background_frame_offset": 0,
        "required_layers": {
            "shadow": list(range(1, total_frames + 1)),
            "reflection": list(range(1, total_frames + 1)),
        },
        "color_contract": COLOR_CONTRACT,
    }
    if occlusion_frames:
        strict_plan["required_layers"]["occlusion"] = occlusion_frames
    (evidence / "strict_plan.json").write_text(json.dumps(strict_plan, ensure_ascii=False), encoding="utf-8")
    composite_cmd = [python, str(REPO / "scripts/strict_composite.py"),
                     "--product", str(evidence / "render/product"),
                     "--mask", str(evidence / "render/mask"),
                     "--background", str(h3_dir),
                     "--out", str(evidence / "composite"),
                     "--dilate", "2",
                     "--plan", str(evidence / "strict_plan.json"),
                     "--layers",
                     f"shadow={evidence / 'layers/shadow'}",
                     f"reflection={evidence / 'layers/reflection'}"]
    if occlusion_frames:
        composite_cmd.append(f"occlusion={evidence / 'previz/proxy_only'}")
    composite = run(composite_cmd, log)
    require(composite, "Strict 合成", log)

    print("step 7/8 双产品检测 + 成片编码", flush=True)
    dual = run([python, str(REPO / "scripts/dual_product_check.py"),
                "--frames", str(evidence / "composite"),
                "--product", str(evidence / "render/passes/beauty"),
                "--mask", str(evidence / "render/passes/mask"),
                "--dilate", "2", "--out", str(evidence / "dual_check")], log)
    require(dual, "双产品检测", log)
    output = evidence / "recreate.mp4"
    encode = run([ffmpeg, "-y", "-framerate", "24", "-start_number", "1",
                  "-i", str(evidence / "composite/composite_%04d.png"),
                  "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output)], log)
    require(encode, "FFmpeg 成片", log)

    print("step 8/8 Strict QA 与证据报告", flush=True)
    qa = run([python, str(REPO / "scripts/strict_qa.py"),
              "--beauty", str(evidence / "render/passes/beauty"),
              "--mask", str(evidence / "render/passes/mask"),
              "--plan", str(plan_path), "--frames", str(total_frames),
              "--out", str(evidence / "qa")], log)
    require(qa, "Strict QA", log)

    probe = subprocess.run([shutil.which("ffprobe") or "/usr/bin/ffprobe", "-v", "error",
                            "-select_streams", "v:0", "-show_entries",
                            "stream=width,height,nb_frames,r_frame_rate",
                            "-of", "json", str(output)], capture_output=True, text=True)
    probe_payload = read_json_text(probe.stdout)
    report = {
        "task": "V5-06 参考重演真实成片",
        "pipeline": "render_product --passes --layers → build_layers → validate → H3 background → strict_composite → dual_product_check → ffmpeg → strict_qa",
        "reference_recreation_citation": citation,
        "plan": {
            "intent": plan.get("intent"),
            "shots": [
                {"id": shot.get("id"), "camera": shot.get("camera"),
                 "duration_frames": shot.get("duration_frames")}
                for shot in shots
            ],
            "total_frames": total_frames,
            "from_reference": plan.get("from_reference"),
        },
        "checks": {
            "passes_report": read_json(evidence / "passes_report.json"),
            "composite_report": read_json(evidence / "composite/composite_report.json"),
            "dual_product_check": read_json(evidence / "dual_check/report.json"),
            "strict_qa": read_json(evidence / "qa/report.json"),
            "interaction_contact_qa": interaction_qa,
            "h3_background": h3_evidence,
        },
        "output": {
            "path": str(output),
            "sha256": _sha256(output),
            "size_bytes": output.stat().st_size if output.exists() else 0,
            "ffprobe": probe_payload,
        },
        "notes": [
            "重演计划来自冻结 ReferenceMapping；保留时长逐镜头量化整帧，误差在映射中公开记录",
            "参考片内容（品牌/水印/音乐/人物）未复制到成片；背景为真实 H3 生成，与参考片无关",
            "构图采用适配规划的确定性取景（V3-07 校准机位族），属于 changed_dimensions",
        ],
    }
    (evidence / "recreate_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"重演成片: {output}")
    print(f"证据报告: {evidence / 'recreate_report.json'}")
    log.close()
    return 0


def read_json_text(text: str):
    try:
        return json.loads(text) if text else None
    except json.JSONDecodeError:
        return None


def _sha256(path: Path) -> str:
    import hashlib
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
