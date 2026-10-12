"""Small local policy fixtures; no network, generation, or accepted draft writes."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "explainer-video-pipeline" / "scripts"))
from production_policy import DEFAULT_POLICY, audit_file, audit_plan, write_json


class PolicyAudit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.base = Path(self.tmp.name)
        self.preview = self.base / "review-fixture.bin"
        self.preview.write_bytes(b"declared review evidence fixture only; not a video or visual certification")
        self.plan = {"production_policy": copy.deepcopy(DEFAULT_POLICY), "shots": [self.shot()], "mg": []}

    def tearDown(self):
        self.tmp.cleanup()

    def shot(self, duration=7, amount=.25, identity="S001"):
        return {"id": identity, "start_us": 0, "end_us": int(duration * 1000000),
                "camera": {"motion": "zoom_in", "amount": amount, "focus": [.5, .5]}}

    def final_review(self, reason=None):
        review = {"continuous_preview": self.preview.name, "reviewed": True, "focus_protected": True}
        if reason:
            review["reason"] = reason
        self.plan["shots"][0]["motion_review"] = review

    def audit(self, stage="planning"):
        return audit_plan(self.plan, self.base, stage)

    def codes(self, result, severity="errors"):
        return {row["code"] for row in result[severity]}

    def ledger(self, rows):
        self.plan["production_policy"]["flow_submission_ledger"] = "flow-submissions.json"
        write_json(self.base / "flow-submissions.json", {"submissions": rows})

    def submission(self, index, status="used", shot_id=None):
        return {"task_id": f"task-{index}", "shot_id": shot_id or f"S{index:03}",
                "asset_id": f"asset-{index}", "provider": "flow", "media_type": "video",
                "paid": True, "is_retry": False, "status": status}

    def test_legacy_explicitly_not_applicable(self):
        self.plan.pop("production_policy")
        self.plan["shots"][0]["end_us"] = 24000000
        self.assertEqual(self.audit("final")["status"], "not_applicable")

    def test_other_channel_is_not_forced(self):
        self.plan["production_policy"]["channel_id"] = "other_creator"
        self.plan["production_policy"].pop("preset_id", None)
        self.assertEqual(self.audit("final")["status"], "not_applicable")

    def test_empty_channel_cannot_bypass(self):
        self.plan["production_policy"].pop("channel_id")
        self.assertIn("policy_schema", self.codes(self.audit()))

    def test_long_still_fails_without_rewriting(self):
        self.plan["shots"][0]["end_us"] = 14500000
        frozen = copy.deepcopy(self.plan)
        self.assertIn("ordinary_still_too_long", self.codes(self.audit()))
        self.assertEqual(self.plan, frozen)

    def test_eight_to_ten_seconds_is_review_not_arbitrary_rejection(self):
        self.plan["shots"][0]["end_us"] = 9600000
        result = self.audit()
        self.assertEqual(result["errors"], [])
        self.assertIn("ordinary_still_over_target", self.codes(result, "warnings"))

    def test_explicit_duration_exception_is_allowed_but_visible(self):
        self.plan["shots"][0]["end_us"] = 11000000
        self.plan["shots"][0]["duration_exception"] = {"reason": "One deliberate reaction pause after complex information; reviewed with adjacent cuts"}
        self.assertNotIn("ordinary_still_too_long", self.codes(self.audit()))
        self.assertIn("ordinary_still_over_target", self.codes(self.audit(), "warnings"))

    def test_mechanism_word_alone_does_not_exempt(self):
        self.plan["shots"][0].update(end_us=14000000, presentation={"kind": "mechanism", "state_changes": []})
        codes = self.codes(self.audit())
        self.assertIn("mechanism_exemption_invalid", codes)
        self.assertIn("ordinary_still_too_long", codes)

    def test_staggered_actual_reveals_support_mechanism(self):
        shot = self.plan["shots"][0]
        shot.update(end_us=14000000, presentation={"kind": "mechanism", "state_changes": [
            {"at_us": 4000000, "description": "Expose the first anatomical compartment", "element_ids": ["part-a"]},
            {"at_us": 8000000, "description": "Reveal the separate second compartment", "element_ids": ["part-b"]}]})
        self.plan["mg"] = [{"id": "part-a", "type": "png", "start_us": 4000000, "end_us": 14000000},
                           {"id": "part-b", "type": "png", "start_us": 8000000, "end_us": 14000000}]
        self.assertNotIn("ordinary_still_too_long", self.codes(self.audit()))
        self.assertTrue(self.audit()["shot_metrics"][0]["mechanism_duration_exempt"])

    def test_claimed_late_states_do_not_use_unrelated_initial_animation(self):
        shot = self.plan["shots"][0]
        shot.update(end_us=14000000, presentation={"kind": "mechanism", "state_changes": [
            {"at_us": 4000000, "description": "claimed first late event", "element_ids": ["part"]},
            {"at_us": 8000000, "description": "claimed second late event", "element_ids": ["part"]}]})
        self.plan["mg"] = [{"id": "part", "type": "png", "start_us": 0, "end_us": 14000000,
                            "animation": {"keyframes": [{"at_us": 0, "alpha": 0}, {"at_us": 350000, "alpha": 1}, {"at_us": 14000000, "alpha": 1}]}}]
        self.assertIn("ordinary_still_too_long", self.codes(self.audit()))

    def test_native_translation_fraction_is_half_native_span(self):
        self.plan["shots"][0]["camera"] = {"motion": "pan_right", "amount": .12}
        metrics = self.audit()["shot_metrics"][0]
        self.assertAlmostEqual(metrics["x_endpoint_canvas_fraction"], .12)
        self.assertEqual(metrics["y_endpoint_canvas_fraction"], 0)
        self.assertTrue(metrics["passes_numerical_motion_screen"])

    def test_explicit_crop_uses_relative_not_absolute_scale(self):
        self.plan["shots"][0]["camera"] = {"keyframes": [
            {"at_us": 0, "scale": 2.0, "x": 0, "y": 0},
            {"at_us": 7000000, "scale": 2.1, "x": 0, "y": 0}]}
        self.assertAlmostEqual(self.audit()["shot_metrics"][0]["scale_range_relative_delta"], .05)
        self.assertIn("weak_camera_motion", self.codes(self.audit(), "warnings"))

    def test_strong_numbers_do_not_certify_final_motion(self):
        self.assertIn("motion_final_review_missing", self.codes(self.audit("final")))
        self.final_review()
        self.assertEqual(self.audit("final")["errors"], [])

    def test_weak_motion_needs_reviewed_deliberate_exception(self):
        self.plan["shots"][0]["camera"]["amount"] = .03
        self.final_review()
        self.assertIn("weak_camera_motion", self.codes(self.audit("final")))
        self.final_review("Protect the focused hand and allow a brief deliberate still reaction")
        self.assertNotIn("weak_camera_motion", self.codes(self.audit("final")))
        self.assertIn("weak_camera_motion", self.codes(self.audit("final"), "warnings"))

    def test_flow_more_than_five_clips_fails(self):
        self.plan["shots"] = [dict(self.shot(identity=f"S{i:03}"), media_type="video") for i in range(6)]
        self.assertIn("flow_video_limit", self.codes(self.audit()))

    def test_paid_failed_attempts_count_and_future_work_checks_remaining(self):
        self.plan["shots"][0]["media_type"] = "video"
        self.ledger([self.submission(i, "failed") for i in range(5)])
        self.assertIn("flow_remaining_budget", self.codes(self.audit()))
        self.ledger([self.submission(i, "failed") for i in range(6)])
        self.assertIn("flow_paid_budget", self.codes(self.audit("final")))

    def test_minor_retry_is_rejected_major_functional_retry_allowed(self):
        row = self.submission(1)
        row.update(is_retry=True, major_error_reason="The color is not pretty enough")
        self.ledger([row])
        self.assertIn("flow_retry_minor_or_undocumented", self.codes(self.audit()))
        row.update(major_error_code="wrong_action", major_error_reason="It never lifts the required lid, so the explanation is false")
        self.ledger([row])
        self.assertNotIn("flow_retry_minor_or_undocumented", self.codes(self.audit()))

    def test_final_video_maps_actual_asset_and_task(self):
        self.plan["shots"][0].update(media_type="video", generation={"provider": "flow", "task_id": "task-1", "asset_id": "asset-1"})
        self.ledger([self.submission(1)])
        self.assertEqual(self.audit("final")["errors"], [])
        self.plan["shots"][0]["generation"]["asset_id"] = "wrong-asset"
        self.assertIn("flow_asset_mapping", self.codes(self.audit("final")))

    def add_label(self, background=True):
        element = {"id": "label", "type": "text", "text": "Allspice", "text_color": "#20333D",
                   "bbox_px": [100, 200, 400, 260], "start_us": 0, "end_us": 7000000,
                   "readability": {"font_px_at_1080p": 48, "reviewed": True, "final_preview": self.preview.name}}
        if background:
            element["readability"]["background"] = {"color": "#FFF4DD", "opacity": .96, "padding_px": 16, "element_id": "backing"}
        self.plan["mg"] = [element, {"id": "backing", "type": "png", "bbox_px": [84, 184, 416, 276], "start_us": 0, "end_us": 7000000}]
        self.final_review()
        return element

    def test_label_backing_must_be_rendered_and_have_contrast(self):
        label = self.add_label()
        self.assertEqual(self.audit("final")["errors"], [])
        self.plan["mg"].pop()
        self.assertIn("label_backing_not_rendered", self.codes(self.audit("final")))
        label["readability"]["background"]["color"] = "#20333D"
        self.assertIn("label_contrast_unplanned", self.codes(self.audit("final")))

    def test_unbacked_label_requires_actual_contrast_review(self):
        label = self.add_label(background=False)
        self.assertIn("label_contrast_unplanned", self.codes(self.audit("final")))
        label["readability"].update(contrast_reviewed=True, contrast_ratio=7.8)
        self.assertEqual(self.audit("final")["errors"], [])
        label["readability"]["font_px_at_1080p"] = 18
        self.assertIn("label_size_unverified", self.codes(self.audit("final")))

    def test_project_root_evidence_and_cross_project_ledger(self):
        self.plan["project_identity"] = {"project_id": "episode-03", "project_instance_id": "instance-A", "run_id": "new-run", "project_root": str(self.base)}
        self.final_review()
        self.ledger([self.submission(1)])
        result = audit_plan(self.plan, self.base / "planning", "final")
        self.assertIn("flow_ledger_cross_project", self.codes(result))
        ledger = {"project_identity": dict(self.plan["project_identity"], run_id="older-run"), "submissions": [self.submission(1)]}
        write_json(self.base / "flow-submissions.json", ledger)
        result = audit_plan(self.plan, self.base / "planning", "final")
        self.assertNotIn("flow_ledger_cross_project", self.codes(result))
        self.assertNotIn("motion_final_preview_missing", self.codes(result))


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(PolicyAudit)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    receipt = {"tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
               "passed": result.wasSuccessful(), "scope": "Local planning/final evidence fixtures only; no perceptual certification, live quota, network, or accepted draft modification"}
    write_json(Path(__file__).parent / "production_policy_test_result.json", receipt)
    raise SystemExit(0 if result.wasSuccessful() else 1)
