"""A02 合同一致性：ImagePreviewSpec 的 Schema、示例、运行时模型必须同步。

这里的目的是**发现漂移**：任何一方单独改动（Schema、示例、Pydantic 模型）
都会让本文件失败，从而阻止"文档说一套、代码做一套"。
"""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

from pydantic import ValidationError

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "apps" / "api"))

from productdirector_api.main import OutputSpec, PlanRequest, PlanUpdate, Shot  # noqa: E402

try:
    from jsonschema import Draft202012Validator
except ModuleNotFoundError:  # pragma: no cover - 依赖缺失时明确跳过而不是静默通过
    Draft202012Validator = None

CONTRACTS = PROJECT / "contracts"
IMAGE_SCHEMA = CONTRACTS / "image-preview.v1.schema.json"
IMAGE_EXAMPLE = CONTRACTS / "image-preview.v1.example.json"
PLAN_SCHEMA = CONTRACTS / "director-plan.v1.schema.json"
PLAN_EXAMPLE = CONTRACTS / "director-plan.v1.example.json"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@unittest.skipUnless(Draft202012Validator, "需要 jsonschema 才能校验 JSON Schema")
class ImagePreviewContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = load(IMAGE_SCHEMA)
        self.example = load(IMAGE_EXAMPLE)
        self.validator = Draft202012Validator(self.schema)

    def errors(self, document: dict) -> list:
        return sorted(self.validator.iter_errors(document), key=lambda error: list(error.path))

    def assertRejected(self, document: dict, note: str) -> None:
        self.assertTrue(self.errors(document), f"应当被拒绝: {note}")

    # --- 正例 ---

    def test_example_matches_the_schema(self) -> None:
        self.assertEqual(self.errors(self.example), [])

    def test_runtime_models_accept_the_example(self) -> None:
        plan = PlanUpdate.model_validate(
            {
                "intent": self.example["intent"],
                "shots": self.example["shots"],
                "crop_anchor": self.example["crop_anchor"],
            }
        )
        self.assertEqual(sum(shot.duration_frames for shot in plan.shots), 144)
        self.assertEqual(plan.crop_anchor.value, "left")
        self.assertEqual(OutputSpec.model_validate(self.example["output"]).frame_count, 144)
        request = PlanRequest.model_validate(
            {
                "product_asset_id": self.example["product_asset_id"],
                "intent": self.example["intent"],
                "output": self.example["output"],
                "crop_anchor": self.example["crop_anchor"],
            }
        )
        self.assertEqual(request.crop_anchor.value, "left")

    # --- 负例：Schema 拒绝 ---

    def test_schema_rejects_unknown_crop_anchor(self) -> None:
        document = copy.deepcopy(self.example)
        document["crop_anchor"] = "diagonal"
        self.assertRejected(document, "非法裁切锚点")

    def test_schema_rejects_unknown_property(self) -> None:
        document = copy.deepcopy(self.example)
        document["unexpected_field"] = True
        self.assertRejected(document, "未知字段")

    def test_schema_rejects_wrong_shot_count(self) -> None:
        document = copy.deepcopy(self.example)
        document["shots"] = document["shots"][:2]
        self.assertRejected(document, "只有两个镜头")

    def test_schema_rejects_out_of_range_duration(self) -> None:
        document = copy.deepcopy(self.example)
        document["shots"][1]["duration_frames"] = 200
        self.assertRejected(document, "单镜时长超上限")

    def test_schema_rejects_landscape_output(self) -> None:
        document = copy.deepcopy(self.example)
        document["output"]["width"] = 1920
        document["output"]["height"] = 1080
        self.assertRejected(document, "非 9:16 输出")

    # --- 负例：运行时同样拒绝 ---

    def test_runtime_rejects_the_same_bad_cases(self) -> None:
        with self.assertRaises(ValidationError):
            PlanUpdate.model_validate({"intent": "x", "shots": self.example["shots"], "crop_anchor": "diagonal"})
        with self.assertRaises(ValidationError):
            PlanUpdate.model_validate({"intent": "x", "shots": self.example["shots"][:2]})
        with self.assertRaises(ValidationError):
            OutputSpec.model_validate({"width": 1920, "height": 1080, "fps": 24, "duration_seconds": 6})
        broken = copy.deepcopy(self.example["shots"])
        broken[1]["duration_frames"] = 200
        with self.assertRaises(ValidationError):
            PlanUpdate.model_validate({"intent": "x", "shots": broken})

    # --- 漂移守卫：字段集合必须逐项对应 ---

    def test_schema_and_runtime_fields_stay_in_sync(self) -> None:
        schema_shot = set(self.schema["$defs"]["shot"]["properties"])
        runtime_shot = set(Shot.model_fields)
        self.assertEqual(schema_shot, runtime_shot, "Shot 字段在 Schema 与运行时之间漂移")

        schema_output = set(self.schema["$defs"]["output"]["properties"])
        runtime_output = set(OutputSpec.model_fields)
        self.assertEqual(schema_output, runtime_output, "OutputSpec 字段在 Schema 与运行时之间漂移")

        schema_top = set(self.schema["properties"])
        snapshot_keys = {"schema_version", "product_asset_id", "intent", "output", "fidelity_mode", "crop_anchor", "shots"}
        self.assertEqual(schema_top, snapshot_keys, "快照顶层字段与冻结快照不一致")

    def test_target_3d_plan_contract_is_still_structurally_valid(self) -> None:
        """目标 3D 合同（尚未实现）本身必须保持结构有效，避免手改坏掉。"""
        schema = load(PLAN_SCHEMA)
        example = load(PLAN_EXAMPLE)
        validator = Draft202012Validator(schema)
        self.assertEqual(sorted(validator.iter_errors(example), key=lambda e: list(e.path)), [])


class ContractDocumentationTests(unittest.TestCase):
    def test_contracts_readme_points_to_the_image_preview_spec(self) -> None:
        text = (CONTRACTS / "README.md").read_text(encoding="utf-8")
        self.assertIn("image-preview.v1.schema.json", text)
        self.assertIn("ImagePreviewSpec", text)


if __name__ == "__main__":
    unittest.main()
