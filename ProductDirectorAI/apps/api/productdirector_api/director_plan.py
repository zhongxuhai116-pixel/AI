"""目标 3D 合同（`contracts/director-plan.v1.schema.json`）的运行时模型与桥接。

V1 的运行时快照一直是简化形状（`camera` 枚举 + `focal_length_mm`），而根
Schema 描述的是带相机轨迹、产品位姿和场景的目标合同。本模块把两者显式连起来：

- `to_target_document()`：运行时快照 → 符合根 Schema 的 3D 文档（未提供的
  部分用当前渲染器的默认取景补齐）。
- `from_target_document()`：3D 文档 → 运行时可接受的字段（下转换）。

这样"文档说的合同"和"代码做的事"之间有可测试的映射，而不是两套互不相干的定义。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, TypeAdapter

REPO_ROOT = Path(__file__).resolve().parents[3]
DIRECTOR_PLAN_SCHEMA_PATH = REPO_ROOT / "contracts" / "director-plan.v1.schema.json"

Vector3 = tuple[float, float, float]

# 当前 Blender 渲染器的默认取景；目标文档未提供时用它补齐，
# 保证"没有显式 3D 字段"的计划渲染结果与历史一致。
DEFAULT_CAMERA_TARGET: Vector3 = (0.0, 0.0, 0.7)
DEFAULT_ORBIT_RADIUS_M = 3.45
DEFAULT_ORBIT_HEIGHT_M = 1.28
DEFAULT_ORBIT_START_DEG = -34.0
DEFAULT_ORBIT_END_DEG = 40.0
# 目标合同当前把这些值写死；运行时已支持更大范围，超出时不能伪装成"符合合同"。
TARGET_CONTRACT_SENSOR_WIDTH_MM = 36
TARGET_CONTRACT_PRODUCT_SCALE = 1


class TargetContractError(RuntimeError):
    """运行时快照超出目标 3D 合同允许的范围。"""


class OrbitPath(BaseModel):
    type: Literal["hero_orbit"] = "hero_orbit"
    radius_m: float = Field(default=DEFAULT_ORBIT_RADIUS_M, gt=0, le=1000)
    height_m: float = Field(default=DEFAULT_ORBIT_HEIGHT_M, ge=0, le=1000)
    start_angle_deg: float = Field(default=DEFAULT_ORBIT_START_DEG, ge=-360, le=360)
    end_angle_deg: float = Field(default=DEFAULT_ORBIT_END_DEG, ge=-360, le=360)


class DollyPath(BaseModel):
    type: Literal["dolly_in"] = "dolly_in"
    start_m: Vector3 = (0.0, -4.8, 1.35)
    end_m: Vector3 = (0.0, -3.0, 1.12)


class TrackPath(BaseModel):
    type: Literal["side_track"] = "side_track"
    start_m: Vector3 = (-1.35, -3.65, 1.15)
    end_m: Vector3 = (1.35, -3.65, 1.15)


class StaticPath(BaseModel):
    type: Literal["static"] = "static"
    position_m: Vector3 = (0.0, -3.55, 1.2)


CameraPath = Annotated[
    Union[OrbitPath, DollyPath, TrackPath, StaticPath],
    Field(discriminator="type"),
]
CAMERA_PATH_ADAPTER = TypeAdapter(CameraPath)


class ProductPose(BaseModel):
    """产品相对归一化底座的位姿；默认即当前渲染器行为（居中、落地、等比）。"""

    position_m: Vector3 = (0.0, 0.0, 0.0)
    rotation_xyz_deg: Vector3 = (0.0, 0.0, 0.0)
    scale: float = Field(default=1.0, gt=0, le=100)


class SceneSpec(BaseModel):
    template: Literal["studio_product"] = "studio_product"
    background_color: str = Field(default="#0A0A0C", pattern=r"^#[0-9A-Fa-f]{6}$")
    lighting_preset: Literal["softbox", "three_point"] = "softbox"


def hex_to_rgb(value: str) -> tuple[float, float, float]:
    """把 #RRGGBB 转成 0–1 的线性分量（渲染器用）。"""
    text = value.lstrip("#")
    return tuple(int(text[index:index + 2], 16) / 255 for index in (0, 2, 4))  # type: ignore[return-value]


def _as_lists(value):
    """把元组递归转成列表，保证产出的文档是纯 JSON 形状。"""
    if isinstance(value, (list, tuple)):
        return [_as_lists(item) for item in value]
    if isinstance(value, dict):
        return {key: _as_lists(item) for key, item in value.items()}
    return value


def _as_tuples(value):
    """把三元向量递归转成元组，保证运行时模型输入形状稳定。"""
    if isinstance(value, (list, tuple)):
        converted = [_as_tuples(item) for item in value]
        return tuple(converted) if len(converted) == 3 and all(isinstance(item, float) for item in converted) else converted
    if isinstance(value, dict):
        return {key: _as_tuples(item) for key, item in value.items()}
    return value


def load_target_schema() -> dict:
    return json.loads(DIRECTOR_PLAN_SCHEMA_PATH.read_text(encoding="utf-8"))


def _path_from_shot(shot: dict) -> dict:
    """取镜头的相机轨迹；没有显式轨迹时按相机模板生成默认轨迹。"""
    explicit = shot.get("camera_path")
    if isinstance(explicit, dict) and explicit.get("type"):
        return explicit
    camera = shot.get("camera")
    if camera == "hero_orbit":
        return OrbitPath().model_dump()
    if camera == "dolly_in":
        return DollyPath().model_dump()
    if camera == "side_track":
        return TrackPath().model_dump()
    return StaticPath().model_dump()


def _path_as_lists(path: dict) -> dict:
    return _as_lists(path)


def to_target_document(snapshot: dict) -> dict:
    """运行时快照 → 符合根 Schema 的 3D 文档。

    目标合同把 `product_pose.scale` 限定为 1、`sensor_width_mm` 限定为 36。
    运行时已经支持更大范围，因此这里**显式报错**而不是悄悄裁剪或输出一份
    不合规的文档：合同与实现的范围差异必须可见（见 contracts/README.md）。
    """
    scene = SceneSpec.model_validate(snapshot.get("scene") or {})
    pose = ProductPose.model_validate(snapshot.get("product_pose") or {})
    if pose.scale != TARGET_CONTRACT_PRODUCT_SCALE:
        raise TargetContractError(
            f"目标合同要求 product_pose.scale = {TARGET_CONTRACT_PRODUCT_SCALE}，实际 {pose.scale}"
        )
    output = snapshot.get("output") or {}
    shots = []
    for shot in snapshot.get("shots", []):
        sensor_width = float(shot.get("sensor_width_mm", TARGET_CONTRACT_SENSOR_WIDTH_MM) or TARGET_CONTRACT_SENSOR_WIDTH_MM)
        if sensor_width != TARGET_CONTRACT_SENSOR_WIDTH_MM:
            raise TargetContractError(
                f"目标合同要求 sensor_width_mm = {TARGET_CONTRACT_SENSOR_WIDTH_MM}，实际 {sensor_width}"
            )
        shots.append({
            "id": shot["id"],
            "name": shot["name"],
            "duration_frames": shot["duration_frames"],
            "camera": {
                "focal_length_mm": shot["focal_length_mm"],
                "sensor_width_mm": sensor_width,
                "target_m": list(shot.get("camera_target_m") or DEFAULT_CAMERA_TARGET),
                "path": _path_as_lists(_path_from_shot(shot)),
            },
            "product_pose": {
                "position_m": list(pose.position_m),
                "rotation_xyz_deg": list(pose.rotation_xyz_deg),
                "scale": pose.scale,
            },
            "scene": {
                "template": scene.template,
                "background_color": scene.background_color,
                "lighting_preset": scene.lighting_preset,
            },
        })
    return {
        "schema_version": "1.0",
        "product_version_id": snapshot.get("product_version_id", "33333333-3333-4333-8333-333333333333"),
        "intent": snapshot.get("intent", ""),
        "output": {
            "width": output.get("width", 540),
            "height": output.get("height", 960),
            "fps": output.get("fps", 24),
            "container": "mp4",
            "video_codec": "h264",
            "pixel_format": "yuv420p",
        },
        "fidelity_mode": "STRICT",
        "shots": shots,
    }


def from_target_document(document: dict) -> dict:
    """3D 文档 → 运行时字段（下转换；供导入目标合同或测试往返使用）。"""
    shots = []
    for shot in document.get("shots", []):
        camera = shot["camera"]
        shots.append({
            "id": shot["id"],
            "name": shot["name"],
            "duration_frames": shot["duration_frames"],
            "camera": camera["path"]["type"],
            "focal_length_mm": camera["focal_length_mm"],
            "sensor_width_mm": camera.get("sensor_width_mm", 36),
            "camera_target_m": tuple(camera["target_m"]),
            "camera_path": _as_tuples(camera["path"]),
        })
    first_pose = document["shots"][0].get("product_pose") if document.get("shots") else None
    first_scene = document["shots"][0].get("scene") if document.get("shots") else None
    return {
        "intent": document.get("intent", ""),
        "output": {
            "width": document["output"]["width"],
            "height": document["output"]["height"],
            "fps": document["output"]["fps"],
            "duration_seconds": 6,
        },
        "shots": shots,
        "product_pose": first_pose or {},
        "scene": first_scene or {},
    }
