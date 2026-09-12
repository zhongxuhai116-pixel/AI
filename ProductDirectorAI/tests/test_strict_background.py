"""V3-03 系统受控背景 Producer：离线合同、隔离与产物原子性正负例。"""
from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "apps" / "api"))

from productdirector_api.strict_background import (  # noqa: E402
    BackgroundProducerError,
    BACKGROUND_ONLY_H3_RUNTIME_STATUS,
    MODE_CONTROLLED_IMPORT,
    MODE_FULL_FRAME_EXTRACT,
    MODE_INDEPENDENT_WORKFLOW,
    background_only_h3_graph_hash,
    build_background_only_h3_graph,
    collect_controlled_comfyui_frames,
    run_controlled_background_workflow,
    run_controlled_import,
    run_background_only_h3_producer,
    verify_background_workflow_graph,
    verify_background_only_h3_contract,
    verify_background_only_h3_graph,
    verify_background_only_h3_graph_schema,
    verify_evidence_binding,
    verify_product_isolation,
)


SIZE = (8, 8)
SAFE_MAP = [{"product_region": "logo", "background_region": "outside_product"}]
UNSAFE_MAP = [{"product_region": "full_product", "background_region": "anywhere"}]
WORKFLOW_HASH = "a" * 64


def _png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _write_rgba(path: Path, rgb: tuple[int, int, int], alpha_value: int = 0) -> None:
    height, width = SIZE
    array = np.zeros((height, width, 4), dtype=np.uint8)
    array[:, :, 0] = rgb[0]
    array[:, :, 1] = rgb[1]
    array[:, :, 2] = rgb[2]
    array[:, :, 3] = alpha_value
    Image.fromarray(array, mode="RGBA").save(path)


def _write_mask(path: Path, pixel_value: int = 255) -> None:
    height, width = SIZE
    array = np.zeros((height, width), dtype=np.uint8)
    array[:, :] = pixel_value
    Image.fromarray(array, mode="L").save(path)


def _spec(frame_count: int = 1, width: int = SIZE[0], height: int = SIZE[1]) -> SimpleNamespace:
    return SimpleNamespace(frame_count=frame_count, width=width, height=height)


def _snapshot(job_id: str = "job-1", *, with_background: bool = True) -> dict:
    value = {
        "job_id": job_id,
        "plan_id": "plan-1",
        "plan_contract_id": "contract-1",
        "product_version_id": "version-1",
        "owner_id": "owner-1",
    }
    if with_background:
        value["background_workflow"] = _workflow()
    return value


def _workflow(**overrides) -> dict:
    value = {
        "name": "local.background",
        "version": "1.0.0",
        "workflow_hash": WORKFLOW_HASH,
        "protection_map": SAFE_MAP,
    }
    value.update(overrides)
    return value


class FakeComfyClient:
    def __init__(self, items: list[dict], pngs: dict[str, bytes] | None = None, *, download_raises=None):
        self.items = items
        self.pngs = pngs or {}
        self.download_raises = download_raises
        self.downloaded: list[str] = []

    def history(self, external_id):
        return {"status": {"completed": True, "status_str": "success"}, "outputs": {}}

    def is_completed(self, record):
        return True

    def status_text(self, record):
        return "SUCCEEDED"

    def outputs(self, record):
        return self.items

    def download(self, item):
        self.downloaded.append(item["filename"])
        if self.download_raises is not None:
            raise self.download_raises
        filename = item["filename"]
        if filename in self.pngs:
            return self.pngs[filename]
        return _png_bytes(Image.new("RGB", SIZE, (12, 34, 56)))


class StrictBackgroundContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pd-bg-test-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.snapshot = _snapshot()
        self.strict_root = self.tmp / "job-1" / "strict"
        self.strict_root.mkdir(parents=True, exist_ok=True)

    def _controlled_root(self) -> Path:
        root = self.tmp / "controlled"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _source_and_mask(self, root: Path, *, background_alpha: int = 0, mask_value: int = 255) -> tuple[Path, Path]:
        source = root / "background" / "frame_0001.png"
        mask = root / "mask" / "frame_0001.png"
        source.parent.mkdir(parents=True, exist_ok=True)
        mask.parent.mkdir(parents=True, exist_ok=True)
        _write_rgba(source, (12, 34, 56), alpha_value=background_alpha)
        _write_mask(mask, pixel_value=mask_value)
        return source, mask

    def test_workflow_contract_rejects_request_mismatch(self) -> None:
        called = []

        def lease_check(job_id, worker_id, epoch):
            called.append((job_id, worker_id, epoch))

        with self.assertRaises(BackgroundProducerError) as ctx:
            run_controlled_background_workflow(
                "job-1", "worker-1", 7, self.snapshot, _workflow(),
                self.strict_root, _spec(), requested_workflow=_workflow(version="9.9"),
                mode=MODE_CONTROLLED_IMPORT, source_dir=self.tmp / "nope",
                lease_check=lease_check, controlled_root=self._controlled_root(),
            )
        self.assertIn("version 与冻结策略不一致", str(ctx.exception))
        self.assertEqual(called, [("job-1", "worker-1", 7)])

    def test_unsafe_protection_map_fails_closed(self) -> None:
        snapshot = _snapshot()
        snapshot["background_workflow"] = _workflow(protection_map=UNSAFE_MAP)
        with self.assertRaises(BackgroundProducerError) as ctx:
            run_controlled_background_workflow(
                "job-1", "worker-1", 7, snapshot, _workflow(protection_map=UNSAFE_MAP),
                self.strict_root, _spec(), requested_workflow=_workflow(protection_map=UNSAFE_MAP),
                mode=MODE_CONTROLLED_IMPORT, source_dir=self.tmp / "nope",
                controlled_root=self._controlled_root(),
            )
        self.assertIn("不能用于背景-only 证据", str(ctx.exception))

    def test_missing_frozen_run_workflow_fails_closed(self) -> None:
        with self.assertRaises(BackgroundProducerError) as ctx:
            run_controlled_background_workflow(
                "job-1", "worker-1", 7, _snapshot(with_background=False), _workflow(),
                self.strict_root, _spec(), mode=MODE_CONTROLLED_IMPORT,
                source_dir=self.tmp / "nope", controlled_root=self._controlled_root(),
            )
        self.assertIn("缺少独立 background_workflow", str(ctx.exception))

    def test_incomplete_frozen_run_workflow_fails_closed(self) -> None:
        snapshot = _snapshot()
        snapshot["background_workflow"] = {"name": "local.background"}
        with self.assertRaises(BackgroundProducerError) as ctx:
            run_controlled_background_workflow(
                "job-1", "worker-1", 7, snapshot, _workflow(),
                self.strict_root, _spec(), mode=MODE_CONTROLLED_IMPORT,
                source_dir=self.tmp / "nope", controlled_root=self._controlled_root(),
            )
        self.assertIn("缺少字段", str(ctx.exception))

    def test_full_frame_extract_mode_never_upgrades_v2_video_graph(self) -> None:
        with self.assertRaises(BackgroundProducerError) as ctx:
            run_controlled_background_workflow(
                "job-1", "worker-1", 7, self.snapshot, _workflow(),
                self.strict_root, _spec(), requested_workflow=_workflow(),
                mode=MODE_FULL_FRAME_EXTRACT,
            )
        self.assertIn("不能从整幅 H3 video graph 直接升格", str(ctx.exception))

    def test_controlled_import_positive_publishes_verified_frames(self) -> None:
        root = self._controlled_root()
        source, mask = self._source_and_mask(root)
        evidence = run_controlled_import(
            "job-1", self.snapshot, _workflow(), source.parent, self.strict_root, _spec(),
            mask_dir=mask.parent, controlled_root=root,
        )
        target = self.strict_root / "background" / "frame_0001.png"
        self.assertTrue(target.exists())
        self.assertEqual(evidence["product_protection"], "IMPORT_ISOLATION_VERIFIED")
        self.assertEqual(evidence["frames"]["files"][0]["sha256"], __import__("hashlib").sha256(target.read_bytes()).hexdigest())

    def test_low_alpha_mask_edge_is_protected(self) -> None:
        root = self._controlled_root()
        source, mask = self._source_and_mask(root, background_alpha=200, mask_value=20)
        with self.assertRaises(BackgroundProducerError) as ctx:
            run_controlled_import(
                "job-1", self.snapshot, _workflow(), source.parent, self.strict_root, _spec(),
                mask_dir=mask.parent, controlled_root=root,
            )
        self.assertIn("覆盖产品保护区", str(ctx.exception))
        self.assertFalse((self.strict_root / "background" / "frame_0001.png").exists())

    def test_low_alpha_mask_edge_direct_isolation_failure(self) -> None:
        root = self._controlled_root()
        source, mask = self._source_and_mask(root, background_alpha=100, mask_value=10)
        failures = verify_product_isolation(source, mask, SAFE_MAP)
        self.assertTrue(any("覆盖产品保护区" in item for item in failures), failures)

    def test_missing_mask_fails_without_publishing(self) -> None:
        root = self._controlled_root()
        source, _ = self._source_and_mask(root)
        with self.assertRaises(BackgroundProducerError) as ctx:
            run_controlled_import(
                "job-1", self.snapshot, _workflow(), source.parent, self.strict_root, _spec(),
                mask_dir=None, controlled_root=root,
            )
        self.assertIn("未提供可信产品 Mask", str(ctx.exception))
        self.assertFalse((self.strict_root / "background").exists())

    def test_source_outside_controlled_root_rejected(self) -> None:
        outside = self.tmp / "outside" / "background"
        outside.mkdir(parents=True)
        _write_rgba(outside / "frame_0001.png", (0, 0, 0))
        with self.assertRaises(BackgroundProducerError) as ctx:
            run_controlled_import(
                "job-1", self.snapshot, _workflow(), outside, self.strict_root, _spec(),
                mask_dir=outside, controlled_root=self._controlled_root(),
            )
        self.assertIn("不在受控目录内", str(ctx.exception))

    def test_missing_frame_rejected(self) -> None:
        root = self._controlled_root()
        source, mask = self._source_and_mask(root)
        with self.assertRaises(BackgroundProducerError) as ctx:
            run_controlled_import(
                "job-1", self.snapshot, _workflow(), source.parent, self.strict_root, _spec(frame_count=2),
                mask_dir=mask.parent, controlled_root=root,
            )
        self.assertIn("缺少", str(ctx.exception))


class ComfyBackgroundCollectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pd-bg-comfy-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.strict_root = self.tmp / "job-1" / "strict"
        self.strict_root.mkdir(parents=True, exist_ok=True)

    def _items(self, *names: str) -> list[dict]:
        return [{"filename": name, "subfolder": "", "type": "output"} for name in names]

    def test_positive_collects_unique_frames(self) -> None:
        png = _png_bytes(Image.new("RGB", SIZE, (10, 20, 30)))
        client = FakeComfyClient(self._items("frame_0001.png", "frame_0002.png"), pngs={
            "frame_0001.png": png, "frame_0002.png": png,
        })
        evidence = collect_controlled_comfyui_frames(
            "job-1", _snapshot(), _workflow(), {"external_id": "prompt-1"},
            self.strict_root, _spec(frame_count=2), client=client, poll_seconds=0,
        )
        self.assertEqual({entry["frame"] for entry in evidence["frames"]["files"]}, {1, 2})
        self.assertTrue((self.strict_root / "background" / "frame_0001.png").exists())
        self.assertTrue((self.strict_root / "background" / "frame_0002.png").exists())

    def test_duplicate_frame_rejected_before_download(self) -> None:
        client = FakeComfyClient(self._items("frame_0001.png", "frame_0001_v2.png"))
        with self.assertRaises(BackgroundProducerError) as ctx:
            collect_controlled_comfyui_frames(
                "job-1", _snapshot(), _workflow(), {"external_id": "prompt-1"},
                self.strict_root, _spec(frame_count=2), client=client, poll_seconds=0,
            )
        self.assertIn("重复帧", str(ctx.exception))
        self.assertEqual(client.downloaded, [])
        self.assertFalse((self.strict_root / "background").exists())

    def test_out_of_range_frame_rejected(self) -> None:
        client = FakeComfyClient(self._items("frame_0001.png", "frame_0003.png"))
        with self.assertRaises(BackgroundProducerError) as ctx:
            collect_controlled_comfyui_frames(
                "job-1", _snapshot(), _workflow(), {"external_id": "prompt-1"},
                self.strict_root, _spec(frame_count=2), client=client, poll_seconds=0,
            )
        self.assertIn("越出冻结帧集", str(ctx.exception))

    def test_invalid_png_payload_rejected_without_publish(self) -> None:
        client = FakeComfyClient(self._items("frame_0001.png"), pngs={"frame_0001.png": b"not-a-png"})
        with self.assertRaises(BackgroundProducerError) as ctx:
            collect_controlled_comfyui_frames(
                "job-1", _snapshot(), _workflow(), {"external_id": "prompt-1"},
                self.strict_root, _spec(), client=client, poll_seconds=0,
            )
        self.assertIn("不是有效 PNG", str(ctx.exception))
        self.assertFalse((self.strict_root / "background").exists())


class BackgroundWorkflowGraphTests(unittest.TestCase):
    def _ui_graph(self, node_types: list[str]) -> dict:
        return {
            "nodes": [{"id": str(i + 1), "type": node_type} for i, node_type in enumerate(node_types)],
        }

    def test_product_replacement_graph_rejected(self) -> None:
        graph = {"1": {"class_type": "ProductBlenderRender", "inputs": {}}}
        failures = verify_background_workflow_graph(graph)
        self.assertTrue(any("产品替换渲染节点" in item for item in failures), failures)

    def test_full_frame_h3_reference_graph_rejected(self) -> None:
        graph = {
            "1": {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {}},
            "2": {"class_type": "ImageCompositeMasked", "inputs": {}},
        }
        failures = verify_background_workflow_graph(graph)
        self.assertTrue(any("整幅 H3 视频生成节点" in item for item in failures), failures)
        self.assertTrue(any("不能作为独立背景工作流" in item for item in failures), failures)

    def test_full_frame_h3_speed_cache_graph_rejected(self) -> None:
        graph = {"1": {"class_type": "MiniMaxH3SpeedCache", "inputs": {}}}
        failures = verify_background_workflow_graph(graph)
        self.assertTrue(any("整幅 H3 视频生成节点" in item for item in failures), failures)

    def test_simple_non_h3_background_graph_passes_classification_only(self) -> None:
        graph = self._ui_graph(["LoadImage", "ImageColorMask", "SaveImage"])
        self.assertEqual(verify_background_workflow_graph(graph), [])

    def test_empty_graph_fails_closed(self) -> None:
        self.assertTrue(verify_background_workflow_graph({}))


class BackgroundOnlyH3SchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pd-bg-h3-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.strict_root = self.tmp / "job-1" / "strict"
        self.strict_root.mkdir(parents=True, exist_ok=True)

    def _candidate_workflow(self) -> dict:
        workflow = {
            **_workflow(),
            "background_only": True,
            "prompt": "empty background plate, no product, no logo, no foreground subject",
            "width": 64,
            "height": 64,
            "length": 5,
            "steps": 1,
            "seed": 0,
        }
        graph = build_background_only_h3_graph(workflow)
        workflow["workflow_hash"] = background_only_h3_graph_hash(graph)
        return workflow

    def test_candidate_graph_matches_cloud_object_info_schema(self) -> None:
        workflow = self._candidate_workflow()
        graph = build_background_only_h3_graph(workflow)
        self.assertEqual(verify_background_only_h3_graph_schema(graph), [])
        self.assertEqual(verify_background_only_h3_contract(graph, workflow), [])

    def test_reference_to_video_node_is_rejected(self) -> None:
        graph = {"1": {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {}}}
        failures = verify_background_only_h3_graph(graph)
        self.assertTrue(any("禁止节点" in item or "整幅 H3" in item for item in failures), failures)

    def test_keyframe_inputs_are_rejected(self) -> None:
        workflow = self._candidate_workflow()
        graph = build_background_only_h3_graph(workflow)
        image_node = graph["6"]
        image_node["inputs"]["first_frame"] = ["10", 0]
        failures = verify_background_only_h3_graph(graph)
        self.assertTrue(any("不得连接 first_frame/last_frame" in item for item in failures), failures)

    def test_schema_rejects_wrong_output_index(self) -> None:
        workflow = self._candidate_workflow()
        graph = build_background_only_h3_graph(workflow)
        graph["7"]["inputs"]["conditioning"] = ["6", 1]
        failures = verify_background_only_h3_graph_schema(graph)
        self.assertTrue(any("期望 CONDITIONING" in item for item in failures), failures)

    def test_producer_fails_closed_not_verified_and_does_not_submit(self) -> None:
        workflow = self._candidate_workflow()
        snapshot = _snapshot()
        snapshot["background_workflow"] = workflow

        class NeverSubmitClient:
            def submit(self, graph, client_id=None):
                raise AssertionError("NOT_VERIFIED 候选 graph 不得提交 ComfyUI")

        with self.assertRaises(BackgroundProducerError) as ctx:
            run_background_only_h3_producer(
                "job-1", "worker-1", 7, snapshot, workflow, self.strict_root, _spec(),
                client=NeverSubmitClient(),
            )
        self.assertIn(BACKGROUND_ONLY_H3_RUNTIME_STATUS, str(ctx.exception))


class BackgroundEvidenceBindingTests(unittest.TestCase):
    def test_cross_job_owner_and_hash_binding_failures(self) -> None:
        evidence = {
            "job_id": "job-other",
            "plan_id": "plan-1",
            "plan_contract_id": "contract-1",
            "product_version_id": "version-1",
            "owner_id": "owner-other",
            "workflow_hash": "b" * 64,
            "producer_control": "CLIENT_CLAIM",
        }
        failures = verify_evidence_binding(evidence, "job-1", _snapshot(), WORKFLOW_HASH)
        self.assertTrue(any("不属于当前 Job" in item for item in failures), failures)
        self.assertTrue(any("owner_id" in item for item in failures), failures)
        self.assertTrue(any("workflow_hash" in item for item in failures), failures)
        self.assertTrue(any("不是系统受控 Worker" in item for item in failures), failures)


if __name__ == "__main__":
    unittest.main()
