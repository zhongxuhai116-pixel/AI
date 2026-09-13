"""V6-01：Platform Profile 与后期模板的版本化合同、六个种子 Profile 与规格校验。"""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "tests"))
sys.path.insert(0, str(PROJECT / "apps" / "api"))

import test_job_control as fixtures  # noqa: E402

from productdirector_api import platform_profiles as pp  # noqa: E402

main = fixtures.main

EXPECTED_PROFILES = {
    "tiktok-mx-9x16-esmx": ("tiktok", "9:16", "es-MX", "1080x1920"),
    "youtube-shorts-9x16-en": ("youtube", "9:16", "en-US", "1080x1920"),
    "instagram-reels-9x16-en": ("instagram", "9:16", "en-US", "1080x1920"),
    "facebook-ads-1x1-esmx": ("facebook_ads", "1:1", "es-MX", "1080x1080"),
    "marketplace-1x1-esmx": ("marketplace", "1:1", "es-MX", "1080x1080"),
    "pinterest-2x3-en": ("pinterest", "2:3", "en-US", "1000x1500"),
}


class SeedProfileTests(unittest.TestCase):
    def test_six_seed_profiles_are_valid_and_cover_expected_targets(self) -> None:
        self.assertEqual(len(pp.SEED_PROFILES), 6)
        seen = {}
        for seed in pp.SEED_PROFILES:
            spec = pp.spec_from_payload(seed)
            summary = pp.summarize(pp.validate_spec(spec))
            self.assertTrue(summary["valid"], f"{spec.identity.profile_id}: {summary['blocking']}")
            # 硬限制一律留空（未核验平台规则），规则来源如实未记录
            self.assertEqual(spec.video.hard_limits, [])
            self.assertEqual(pp.rules_status(spec)["status"], "NOT_VERIFIED")
            seen[spec.identity.profile_id] = (
                spec.identity.platform, spec.composition.aspect_ratio, spec.market.locale,
                f"{spec.video.width}x{spec.video.height}",
            )
        self.assertEqual(seen, EXPECTED_PROFILES)
        # 产品默认成片时长（5–8 秒）必须完整落在每个 Profile 允许范围内
        product_min, product_max = pp.PRODUCT_DURATION_RANGE_SECONDS
        for seed in pp.SEED_PROFILES:
            spec = pp.spec_from_payload(seed)
            self.assertLessEqual(spec.video.duration_min_seconds, product_min, spec.identity.profile_id)
            self.assertLessEqual(product_max, spec.video.duration_max_seconds, spec.identity.profile_id)

    def test_seed_profile_subject_roi_inside_safe_area(self) -> None:
        for seed in pp.SEED_PROFILES:
            spec = pp.spec_from_payload(seed)
            self.assertTrue(
                spec.composition.safe_area.contains(spec.composition.subject_roi),
                f"{spec.identity.profile_id} 主体不在安全区内",
            )


class SpecValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = copy.deepcopy(pp.SEED_PROFILES[0])

    def blocking_codes(self, payload: dict) -> set[str]:
        spec = pp.spec_from_payload(payload)
        return {item["code"] for item in pp.validate_spec(spec) if item["severity"] == "BLOCKING"}

    def test_aspect_mismatch_is_blocking(self) -> None:
        payload = copy.deepcopy(self.base)
        payload["composition"]["aspect_ratio"] = "1:1"
        self.assertIn("aspect_mismatch", self.blocking_codes(payload))

    def test_unsupported_resolution_is_blocking(self) -> None:
        payload = copy.deepcopy(self.base)
        payload["video"]["width"], payload["video"]["height"] = 1000, 1000  # 1:1 但不是支持集合内的声明比例
        payload["composition"]["aspect_ratio"] = "9:16"
        self.assertIn("aspect_mismatch", self.blocking_codes(payload))

    def test_subject_outside_safe_area_is_blocking(self) -> None:
        payload = copy.deepcopy(self.base)
        payload["composition"]["subject_roi"] = {"x": 0.0, "y": 0.0, "width": 0.4, "height": 0.4}
        self.assertIn("subject_outside_safe_area", self.blocking_codes(payload))

    def test_hard_limit_without_source_is_blocking(self) -> None:
        payload = copy.deepcopy(self.base)
        payload["video"]["hard_limits"] = [{"name": "max_duration", "value": "60s", "source_url": "", "verified_at": ""}]
        self.assertIn("hard_limit_unverified", self.blocking_codes(payload))

    def test_music_without_license_is_blocking(self) -> None:
        payload = copy.deepcopy(self.base)
        payload["music"].update({"enabled": True, "music_asset_id": "", "license_ref": ""})
        self.assertIn("music_license_missing", self.blocking_codes(payload))

    def test_noncommercial_music_on_ads_profile_is_blocking(self) -> None:
        payload = copy.deepcopy(pp.SEED_PROFILES[3])  # facebook ads 素材
        payload["music"].update({
            "enabled": True, "music_asset_id": "asset-1", "license_ref": "lic-1",
            "commercial_use_allowed": False,
        })
        self.assertIn("music_not_commercial", self.blocking_codes(payload))

    def test_subtitle_without_font_license_is_blocking(self) -> None:
        payload = copy.deepcopy(self.base)
        payload["subtitles"]["font_license"] = ""
        self.assertIn("subtitle_font_license_missing", self.blocking_codes(payload))

    def test_subtitle_without_srt_is_blocking(self) -> None:
        payload = copy.deepcopy(self.base)
        payload["subtitles"]["sidecar_formats"] = ["vtt"]
        self.assertIn("subtitle_srt_required", self.blocking_codes(payload))

    def test_stale_rules_warning(self) -> None:
        import datetime

        payload = copy.deepcopy(self.base)
        payload["rules"] = {
            "rules_source_url": "https://example.invalid/rules",
            "rules_verified_at": (datetime.date.today() - datetime.timedelta(days=400)).isoformat(),
            "rule_revision": "r1",
        }
        spec = pp.spec_from_payload(payload)
        codes = {(item["code"], item["severity"]) for item in pp.validate_spec(spec)}
        self.assertIn(("rules_stale", "WARNING"), codes)
        self.assertEqual(pp.rules_status(spec)["status"], "STALE")

    def test_voice_locale_mismatch_is_warning(self) -> None:
        payload = copy.deepcopy(self.base)
        payload["voice"].update({"enabled": True, "voice_ref": "voice-1", "locale": "en-US"})
        problems = pp.validate_spec(pp.spec_from_payload(payload))
        self.assertIn(("voice_locale_mismatch", "WARNING"), {(item["code"], item["severity"]) for item in problems})

    def test_invalid_duration_range_rejected_by_model(self) -> None:
        from pydantic import ValidationError

        payload = copy.deepcopy(self.base)
        payload["video"]["duration_min_seconds"] = 90
        payload["video"]["duration_max_seconds"] = 30
        with self.assertRaises(ValidationError):
            pp.spec_from_payload(payload)

    def test_output_compatibility_reports_aspect_and_duration(self) -> None:
        spec = pp.spec_from_payload(pp.SEED_PROFILES[0])  # 9:16 1080x1920
        same = pp.output_compatibility(spec, {"width": 540, "height": 960, "fps": 24, "frame_count": 144})
        self.assertEqual([item for item in same if item["severity"] == "BLOCKING"], [])
        wrong = pp.output_compatibility(spec, {"width": 1080, "height": 1080, "fps": 24, "frame_count": 144})
        self.assertIn("output_aspect_mismatch", {item["code"] for item in wrong})
        short = pp.output_compatibility(spec, {"width": 540, "height": 960, "fps": 24, "frame_count": 10})
        self.assertIn("output_duration_out_of_range", {item["code"] for item in short})

    def test_preset_validation_rules(self) -> None:
        clean = pp.PostproductionPresetSpec.model_validate(pp.SEED_PRESETS[0]["spec"])
        self.assertEqual(pp.summarize(pp.validate_preset_spec(clean))["valid"], True)
        subtitled = pp.PostproductionPresetSpec.model_validate(pp.SEED_PRESETS[1]["spec"])
        self.assertEqual(pp.summarize(pp.validate_preset_spec(subtitled))["valid"], True)
        broken = copy.deepcopy(pp.SEED_PRESETS[1]["spec"])
        broken["voice"].update({"enabled": True, "voice_ref": ""})
        codes = {item["code"] for item in pp.validate_preset_spec(
            pp.PostproductionPresetSpec.model_validate(broken)
        ) if item["severity"] == "BLOCKING"}
        self.assertIn("voice_ref_missing", codes)


class PlatformProfileApiTests(unittest.TestCase):
    def setUp(self) -> None:
        fixtures.JobControlAcceptanceTests.setUp(self)

    tearDown = fixtures.JobControlAcceptanceTests.tearDown

    def test_seeded_profiles_are_listed_with_validation_summary(self) -> None:
        response = self.client.get("/api/v1/platform-profiles")
        self.assertEqual(response.status_code, 200, response.text)
        profiles = {item["profile_key"]: item for item in response.json()}
        for key in EXPECTED_PROFILES:
            self.assertIn(key, profiles)
            self.assertEqual(profiles[key]["rules_status"]["status"], "NOT_VERIFIED")
            self.assertTrue(profiles[key]["valid"], profiles[key])
        self.assertEqual(profiles["facebook-ads-1x1-esmx"]["aspect_ratio"], "1:1")

    def test_profile_versions_are_immutable_and_increment(self) -> None:
        profile_id = "tiktok-mx-9x16-esmx"
        versions = self.client.get(f"/api/v1/platform-profiles/{profile_id}/versions").json()
        self.assertEqual([item["version"] for item in versions], [1])
        v1_hash = versions[0]["payload_sha256"]
        spec = copy.deepcopy(versions[0]["spec"])
        spec["copy_spec"]["max_length"] = 1500
        created = self.client.post(
            f"/api/v1/platform-profiles/{profile_id}/versions",
            json={"spec": spec, "notes": "收紧文案上限"},
        )
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["version"], 2)
        after = self.client.get(f"/api/v1/platform-profiles/{profile_id}/versions").json()
        self.assertEqual([item["version"] for item in after], [1, 2])
        self.assertEqual(after[0]["payload_sha256"], v1_hash)  # 旧版本未被改写
        self.assertEqual(after[1]["spec"]["copy_spec"]["max_length"], 1500)
        # 客户端指定的 identity.version 被服务端覆盖
        spec2 = copy.deepcopy(spec)
        spec2["identity"]["version"] = 99
        spec2["copy_spec"]["max_length"] = 1200
        third = self.client.post(
            f"/api/v1/platform-profiles/{profile_id}/versions",
            json={"spec": spec2},
        )
        self.assertEqual(third.json()["version"], 3)
        self.assertEqual(third.json()["spec"]["identity"]["version"], 3)

    def test_validate_endpoint_reports_blocking_and_output_compatibility(self) -> None:
        ok = self.client.post("/api/v1/platform-profiles/tiktok-mx-9x16-esmx/validate", json={})
        self.assertEqual(ok.status_code, 200, ok.text)
        body = ok.json()
        self.assertTrue(body["valid"], body["blocking"])
        self.assertEqual(body["rules_status"]["status"], "NOT_VERIFIED")
        self.assertEqual(body["account"]["status"], "NOT_REQUESTED")

        account = self.client.post(
            "/api/v1/platform-profiles/tiktok-mx-9x16-esmx/validate",
            json={"account_id": "acc-1"},
        ).json()
        self.assertEqual(account["account"]["status"], "NOT_CONFIGURED")
        self.assertIsNone(account["account"]["capabilities"])

        mismatched = self.client.post(
            "/api/v1/platform-profiles/facebook-ads-1x1-esmx/validate",
            json={"output": {"width": 540, "height": 960, "fps": 24, "frame_count": 144}},
        ).json()
        self.assertFalse(mismatched["valid"])
        self.assertIn("output_aspect_mismatch", {item["code"] for item in mismatched["blocking"]})

    def test_create_and_version_checkout_profile(self) -> None:
        created = self.client.post(
            "/api/v1/platform-profiles",
            json={"name": "内部预览 · 9:16 · es-MX", "platform": "tiktok", "profile_key": "internal-preview"},
        )
        self.assertEqual(created.status_code, 201, created.text)
        body = created.json()
        self.assertEqual(body["profile"]["profile_key"], "internal-preview")
        self.assertEqual(body["version"]["version"], 1)
        duplicate = self.client.post(
            "/api/v1/platform-profiles",
            json={"name": "重复", "platform": "tiktok", "profile_key": "internal-preview"},
        )
        self.assertEqual(duplicate.status_code, 409)
        invalid = self.client.post(
            "/api/v1/platform-profiles",
            json={"name": "坏规格", "platform": "tiktok", "profile_key": "bad-spec", "spec": {"identity": {}}},
        )
        self.assertEqual(invalid.status_code, 422)

    def test_presets_are_versioned_and_listed(self) -> None:
        listed = self.client.get("/api/v1/postproduction-presets")
        self.assertEqual(listed.status_code, 200, listed.text)
        keys = {item["preset_key"] for item in listed.json()}
        self.assertIn("clean-9x16-nosub", keys)
        self.assertIn("es-mx-subtitled", keys)
        detail = self.client.get("/api/v1/postproduction-presets/es-mx-subtitled").json()
        self.assertTrue(detail["spec"]["subtitles"]["enabled"])
        self.assertFalse(detail["spec"]["voice"]["enabled"])  # 未配置 Provider 时显式关闭
        spec = copy.deepcopy(detail["spec"])
        spec["voice"].update({"enabled": True, "voice_ref": "es-MX-neural-demo"})
        versioned = self.client.post(
            "/api/v1/postproduction-presets/es-mx-subtitled/versions",
            json={"spec": spec, "notes": "启用 es-MX 配音"},
        )
        self.assertEqual(versioned.status_code, 201, versioned.text)
        self.assertEqual(versioned.json()["version"], 2)
        self.assertTrue(versioned.json()["spec"]["voice"]["enabled"])


if __name__ == "__main__":
    unittest.main()
