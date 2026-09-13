"""Timed brief -> real reference-conditioned scene videos, never a studio fallback."""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

from .providers import comfyui

HEADER = re.compile(
    r"^\s*(?:ESCENA|SCENE|场景|镜头)\s*(\d+)\s*[—–:：-]\s*"
    r"(\d+(?:\.\d+)?)\s*(?:a|to|到|至|[-–—])\s*(\d+(?:\.\d+)?)"
    r"\s*(?:segundos?|seconds?|secs?|s|秒)?\s*[:：]?[^\n]*$", re.I | re.M)
APPENDIX = re.compile(r"^\s*(?:ESTILO VISUAL|VISUAL STYLE|AUDIO|LOCUCI[ÓO]N[^:\n]*|"
                      r"RESTRICCIONES|RESTRICTIONS|视觉风格|音频|配音|限制)\s*[:：]", re.I | re.M)
CAPTION = re.compile(r"^\s*(?:Texto en pantalla|Texto final|On.screen text|Text on screen|"
                     r"字幕|屏幕文字|最终字幕)\s*[:：]\s*", re.I | re.M)


def parse_brief(intent: str, fps: int = 24) -> list[dict]:
    """Parse explicit time ranges; reject ambiguity instead of inventing equal shots."""
    matches = list(HEADER.finditer(intent))
    if not 2 <= len(matches) <= 8:
        raise ValueError("请提供2–8段带时间的场景，例如：场景 1 — 0 到 3 秒：；不会自动替换为模板镜头")
    context = intent[:matches[0].start()].strip()
    tail_start = APPENDIX.search(intent, matches[-1].end())
    tail = intent[tail_start.start():] if tail_start else ""
    # Keep visual restrictions; audio is handled separately from the scene video.
    sections = list(APPENDIX.finditer(tail))
    visual = "\n".join(tail[s.start():sections[i+1].start() if i+1 < len(sections) else len(tail)]
                       for i, s in enumerate(sections)
                       if not re.search(r"AUDIO|LOCUCI|音频|配音", s.group(), re.I))
    shots, end_frame = [], 0
    for i, match in enumerate(matches):
        start, end = (round(float(match.group(k)) * fps) for k in (2, 3))
        if int(match.group(1)) != i + 1 or start != end_frame or end-start < fps or end-start > 360:
            raise ValueError("场景需从0秒开始、按编号连续且不重叠，每段1–15秒；请修正时间轴")
        body_end = matches[i+1].start() if i+1 < len(matches) else (tail_start.start() if tail_start else len(intent))
        body = intent[match.end():body_end].strip()
        caption_match = CAPTION.search(body)
        description = body[:caption_match.start()].strip() if caption_match else body
        caption = body[caption_match.end():].strip() if caption_match else ""
        caption = "\n".join(line.strip().strip('“”"') for line in caption.splitlines() if line.strip())
        if not description or len(caption) > 300:
            raise ValueError(f"场景{i+1}缺少画面描述，或字幕超过300字符")
        prompt = ("Generate one continuous photorealistic shot. <Picture 0> is the actual product reference. "
                  "Preserve its colors, materials, proportions and component layout. Do not use a gray clay model. "
                  "Follow only the current scene below; do not montage other scenes. "
                  "No subtitles, text, logos or watermark in the generated pixels.\n"
                  f"PRODUCT AND GLOBAL BRIEF:\n{context}\nCURRENT SCENE ({(end-start)/fps:g}s):\n"
                  f"{description}\nVISUAL STYLE AND RESTRICTIONS:\n{visual}")
        shots.append({"id": f"shot_{i+1:02d}", "name": f"场景 {i+1}", "camera": "static",
                      "focal_length_mm": 35, "duration_frames": end-start,
                      "start_frame": start, "end_frame": end, "scene_description": description,
                      "scene_prompt": prompt, "caption_text": caption})
        end_frame = end
    return shots


def reference_graph(image_name: str, prompt: str, prefix: str, frames: int, seed: int) -> dict:
    # The deployed node accepts image references without any reference-video input.
    graph = comfyui.build_h3_video_graph("", image_name, prompt, prefix,
                                        width=576, height=1024, length=max(124, frames), seed=seed)
    for key in ("10", "11", "13", "18"):
        graph.pop(key, None)
    graph["7"]["inputs"].pop("ref_videos.ref_video_0", None)
    graph["7"]["inputs"]["ref_images.ref_image_0"] = ["12", 0]
    graph["19"]["inputs"].pop("audio", None)
    return graph


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def generate_scenes(snapshot: dict, reference_bytes: bytes, run_dir: Path, *, ffmpeg: str,
                    run_command, check_active, progress) -> dict:
    """Lease/cancel-aware worker; persist provider IDs and resume only matching evidence."""
    from PIL import Image
    import io
    reference = snapshot["scene_generation"]["reference"]
    if hashlib.sha256(reference_bytes).hexdigest() != reference["sha256"]:
        raise RuntimeError("原始产品参考图已变更，不能执行旧计划")
    picture = Image.open(io.BytesIO(reference_bytes)).convert("RGB")
    if reference.get("crop"):
        x, y, w, h = reference["crop"]
        if min(x,y) < 0 or min(w,h) < 1 or x+w > picture.width or y+h > picture.height:
            raise RuntimeError("参考图裁切超出原图范围")
        picture = picture.crop((x,y,x+w,y+h))
    buffer = io.BytesIO(); picture.save(buffer, format="PNG")
    folder = run_dir / "scenes"; folder.mkdir(exist_ok=True)
    check_active()
    uploaded = folder / "reference.json"
    if uploaded.exists():
        image_name = json.loads(uploaded.read_text())["name"]
    else:
        image_name = comfyui.upload_image(f"{run_dir.name}-product-reference.png", buffer.getvalue())
        uploaded.write_text(json.dumps({"name":image_name}), encoding="utf-8")
    entries = []
    output = snapshot["output"]
    for index, shot in enumerate(snapshot["shots"]):
        check_active()
        scene_id = f"scene-{index+1:02d}"
        state_path, raw = folder / f"{scene_id}.json", folder / f"{scene_id}-source.mp4"
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        seed = int(hashlib.sha256(f'{run_dir.name}:{scene_id}'.encode()).hexdigest()[:8],16)
        graph = reference_graph(image_name, shot["scene_prompt"], f"productdirector/{run_dir.name}/{scene_id}",
                                shot["duration_frames"], seed)
        graph_hash = hashlib.sha256(json.dumps(graph,sort_keys=True).encode()).hexdigest()
        if state and state.get("graph_sha256") != graph_hash:
            raise RuntimeError("恢复场景的冻结工作流不一致，拒绝复用旧生成结果")
        if not state.get("external_id"):
            state = {"external_id": comfyui.submit(graph, client_id=run_dir.name), "graph_sha256": graph_hash,
                     "prompt_sha256": hashlib.sha256(shot["scene_prompt"].encode()).hexdigest()}
            state_path.write_text(json.dumps(state), encoding="utf-8")
        deadline = time.monotonic()+2400
        if not (raw.exists() and state.get("source_sha256") == _sha(raw)):
            while True:
                check_active()
                progress(f"SCENE_{index+1}_OF_{len(snapshot['shots'])}", 5+int(index/len(snapshot['shots'])*80))
                record = comfyui.history(state["external_id"])
                status = comfyui.status_text(record)
                if status == "FAILED":
                    raise RuntimeError(f"场景{index+1} H3生成失败；不会降级成摄影棚或灰模")
                if status == "SUCCEEDED":
                    videos = [item for item in comfyui.outputs(record) if item["filename"].lower().endswith(".mp4")]
                    if not videos: raise RuntimeError(f"场景{index+1}未返回真实视频")
                    raw.write_bytes(comfyui.download(videos[0]))
                    state["source_sha256"] = _sha(raw)
                    state_path.write_text(json.dumps(state), encoding="utf-8")
                    break
                if time.monotonic()>deadline: raise RuntimeError(f"场景{index+1}生成超时，可重试；原Provider任务ID已记录")
                time.sleep(2)
        check_active()
        clip = folder / f"{scene_id}.mp4"
        vf = (f"fps={output['fps']},scale={output['width']}:{output['height']}:force_original_aspect_ratio=decrease,"
              f"pad={output['width']}:{output['height']}:(ow-iw)/2:(oh-ih)/2,setsar=1")
        run_command([ffmpeg,"-y","-i",str(raw),"-an","-vf",vf,"-frames:v",str(shot["duration_frames"]),
                     "-c:v","libx264","-preset","fast","-pix_fmt","yuv420p",str(clip)])
        entries.append({"shot_id":shot["id"],"description":shot["scene_description"],
                        "duration_frames":shot["duration_frames"],"external_id":state["external_id"],
                        "prompt_sha256":state["prompt_sha256"],"source_sha256":state["source_sha256"],
                        "clip_sha256":_sha(clip),"clip":clip.name})
    # Concatenate actual generated clips, not repeated images or a fixed studio shot.
    concat = folder / "concat.txt"
    concat.write_text("".join(f"file '{entry['clip']}'\n" for entry in entries),encoding="utf-8")
    run_command([ffmpeg,"-y","-f","concat","-safe","1","-i",str(concat),"-c","copy",
                 "-movflags","+faststart",str(run_dir/"preview.mp4")])
    return {"engine":"H3_REFERENCE_SCENES","reference_asset_id":reference["asset_id"],
            "reference_sha256":reference["sha256"],"reference_crop":reference.get("crop"),
            "native_width":576,"native_height":1024,"scenes":entries,
            "visual_verification":"NOT_VERIFIED","audio":"DISABLED",
            "note":"逐场景真实生成；参考图引导不等于像素锁定，人物接触和产品细节需视觉复核。当前输出为无声场景视频。"}
