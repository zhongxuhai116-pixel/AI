"""V4-01：交互锚点的坐标合同与纯几何变换。

约定（主规划 §10.4/§10.5）：
- 锚点存**产品本地规范坐标**：`position_m` 为归一化包围盒三轴比例（0..1，bbox 最小角→最大角）；
  `normal` 为 bbox 本地系（轴与包围盒轴对齐）单位向量；`radius_m` 以归一化包围盒最长边为 1 的比例。
- 这样锚点不随产品重新归一化/缩放而漂移；世界空间变换只在渲染/预演侧用
  产品实际包围盒执行（render_product.py 的归一化包围盒即该约定）。
- 本模块不依赖 Blender，纯数学可单测。
"""
from __future__ import annotations

import math


def bbox_local_to_world(bbox_min: tuple[float, float, float],
                        bbox_size: tuple[float, float, float],
                        position: tuple[float, float, float]) -> tuple[float, float, float]:
    """bbox 本地归一化坐标 → 世界坐标：world = bbox_min + position * bbox_size。"""
    if len(bbox_min) != 3 or len(bbox_size) != 3 or len(position) != 3:
        raise ValueError("bbox_min/bbox_size/position 都必须是三维")
    return (
        bbox_min[0] + position[0] * bbox_size[0],
        bbox_min[1] + position[1] * bbox_size[1],
        bbox_min[2] + position[2] * bbox_size[2],
    )


def anchor_to_world(anchor: dict, bbox_min: tuple[float, float, float],
                    bbox_size: tuple[float, float, float]) -> dict:
    """把一条锚点合同映射到世界空间，返回 {position_m, normal, radius_m}。"""
    position = tuple(float(v) for v in anchor["position_m"])
    normal = tuple(float(v) for v in anchor["normal"])
    radius_fraction = float(anchor["radius_m"])
    largest = max(abs(v) for v in bbox_size)
    if largest <= 0:
        raise ValueError("产品包围盒尺寸无效")
    return {
        "position_m": list(bbox_local_to_world(bbox_min, bbox_size, position)),
        "normal": list(normal),
        "radius_m": radius_fraction * largest,
    }


def is_unit_vector(vector: tuple[float, float, float], tolerance: float = 1e-4) -> bool:
    norm = math.sqrt(sum(float(v) * float(v) for v in vector))
    return abs(norm - 1.0) <= tolerance


def anchor_payload_ok(payload: dict) -> list[str]:
    """锚点合同校验（供 API 与测试共用），返回失败原因列表。"""
    failures: list[str] = []
    position = payload.get("position_m")
    if not isinstance(position, (list, tuple)) or len(position) != 3:
        failures.append("position_m 必须是三个数字")
    else:
        for value in position:
            if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                failures.append("position_m 包含非有限数值")
                break
            if not 0.0 <= float(value) <= 1.0:
                failures.append(f"position_m 必须在 [0,1] 范围内，收到 {value}")
    normal = payload.get("normal")
    if not isinstance(normal, (list, tuple)) or len(normal) != 3:
        failures.append("normal 必须是三个数字")
    elif not all(isinstance(v, (int, float)) and math.isfinite(float(v)) for v in normal):
        failures.append("normal 包含非有限数值")
    elif not is_unit_vector(tuple(float(v) for v in normal)):
        failures.append("normal 必须是单位向量")
    radius = payload.get("radius_m")
    if not isinstance(radius, (int, float)) or not math.isfinite(float(radius)):
        failures.append("radius_m 必须是有限数值")
    elif not 0.0 < float(radius) <= 1.0:
        failures.append("radius_m 必须在 (0,1] 范围内（归一化包围盒最长边比例）")
    allowed = payload.get("allowed_actions")
    if not isinstance(allowed, list) or not allowed:
        failures.append("allowed_actions 必须是非空列表")
    return failures
