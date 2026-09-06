import importlib
import datetime as datetime_module
import json
import pathlib
import sys
import tarfile
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT_ROOT = REPOSITORY_ROOT / "new"
sys.path.insert(0, str(SCRIPT_ROOT))

deploy_vps_pair = importlib.import_module("deploy_vps_pair")
interactive_vps_pair = importlib.import_module("interactive_vps_pair")


def successful_metric():
    return {
        "download": {"status": "ok", "mbps": 100.0, "seconds": 1.0},
        "upload": {"status": "ok", "mbps": 80.0, "seconds": 1.0},
        "latency": {"status": "ok", "avgMs": 3.0, "jitterMs": 0.2, "lossPct": 0.0},
    }


class DeploymentArchiveTests(unittest.TestCase):
    def test_archive_is_minimal_posix_and_has_lf_shell_script(self):
        archive_path = deploy_vps_pair.make_archive()
        try:
            with tarfile.open(archive_path, "r:gz") as archive:
                names = archive.getnames()
                self.assertEqual(len(names), len(set(names)))
                self.assertTrue(all("\\" not in name for name in names))
                required = {
                    "Dockerfile",
                    "index-modern.html",
                    "index-classic.html",
                    "vps.html",
                    "docker/entrypoint.sh",
                    "vps-agent/common.php",
                    "vps-agent/health.php",
                    "vps-agent/measure.php",
                    "vps-agent/pair.php",
                }
                self.assertTrue(required.issubset(set(names)))
                entrypoint = archive.extractfile("docker/entrypoint.sh").read()
                self.assertNotIn(b"\r\n", entrypoint)
        finally:
            archive_path.unlink(missing_ok=True)

class ResultValidationTests(unittest.TestCase):
    def test_command_line_ranges_reject_invalid_values(self):
        parser = deploy_vps_pair.integer_between("测试值", 1, 10)
        self.assertEqual(parser("5"), 5)
        with self.assertRaises(deploy_vps_pair.argparse.ArgumentTypeError):
            parser("0")

    def test_single_end_deploy_cannot_silently_generate_new_token(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            old_token_file = deploy_vps_pair.TOKEN_FILE
            deploy_vps_pair.TOKEN_FILE = pathlib.Path(temporary_directory) / ".vps_token"
            try:
                with self.assertRaises(RuntimeError):
                    deploy_vps_pair.get_token(SimpleNamespace(token=None), allow_generate=False)
                generated = deploy_vps_pair.get_token(SimpleNamespace(token=None), allow_generate=True)
                self.assertTrue(generated)
                self.assertEqual(deploy_vps_pair.TOKEN_FILE.read_text(encoding="utf-8").strip(), generated)
            finally:
                deploy_vps_pair.TOKEN_FILE = old_token_file

    def test_pair_timeout_covers_both_directions(self):
        self.assertEqual(deploy_vps_pair.pair_request_timeout(45, 5), 300)

    def test_large_server_clock_offset_is_reported(self):
        old_now = interactive_vps_pair.datetime.now(interactive_vps_pair.timezone.utc)
        reported = (old_now.replace(microsecond=0) - datetime_module.timedelta(minutes=2)).isoformat()
        result = interactive_vps_pair.analyze_agent_clock({"json": {"time": reported}})
        self.assertTrue(result["checked"])
        self.assertFalse(result["ok"])

    def test_complete_pair_is_successful(self):
        report = {
            "status": "ok",
            "forward": {"result": successful_metric()},
            "reverse": {"result": successful_metric()},
        }
        self.assertTrue(deploy_vps_pair.pair_report_ok(report))

    def test_hidden_metric_failure_is_not_reported_as_success(self):
        failed = successful_metric()
        failed["download"] = {"status": "error", "error": "download_failed"}
        report = {
            "status": "ok",
            "forward": {"result": failed},
            "reverse": {"result": successful_metric()},
        }
        self.assertFalse(deploy_vps_pair.pair_report_ok(report))

        response = {"ok": True, "json": report}
        summary = interactive_vps_pair.report_summary(response)
        self.assertFalse(summary["ok"])
        self.assertIn("失败", summary["error"])

    def test_markdown_report_never_contains_token_plaintext(self):
        secret = "unit-test-secret-that-must-not-leak"
        with tempfile.TemporaryDirectory() as temporary_directory:
            old_report_dir = interactive_vps_pair.REPORT_DIR
            interactive_vps_pair.REPORT_DIR = pathlib.Path(temporary_directory)
            try:
                report = {
                    "generatedAt": "2026-08-31 12:00:00",
                    "actionLabel": "单元测试",
                    "tokenFingerprint": interactive_vps_pair.token_fingerprint(secret),
                    "config": {
                        "fixed": {"host": "fixed.example", "web_port": 8080},
                        "target": {"host": "target.example", "web_port": 8081},
                        "downloadMegabytes": 1,
                        "uploadMegabytes": 1,
                        "pingCount": 3,
                        "timeoutSeconds": 10,
                    },
                    "deployment": {"requested": False, "deployed": []},
                    "health": {},
                    "pairSummary": {"ok": False, "error": "预期的测试失败"},
                }
                json_path, markdown_path = interactive_vps_pair.save_interactive_report(report)
                self.assertTrue(json.loads(json_path.read_text(encoding="utf-8")))
                self.assertNotIn(secret, markdown_path.read_text(encoding="utf-8"))
            finally:
                interactive_vps_pair.REPORT_DIR = old_report_dir

    def test_skipped_step_is_reported_as_not_executed(self):
        self.assertEqual(interactive_vps_pair.execution_state_text(None), "未执行")
        self.assertEqual(interactive_vps_pair.execution_state_text(True), "通过")
        self.assertEqual(interactive_vps_pair.execution_state_text(False), "未通过")

    def test_health_only_report_does_not_render_pair_as_failure(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            old_report_dir = interactive_vps_pair.REPORT_DIR
            interactive_vps_pair.REPORT_DIR = pathlib.Path(temporary_directory)
            try:
                report = {
                    "generatedAt": "2026-08-31T12:00:00+08:00",
                    "action": "1",
                    "actionLabel": "只做健康检查，不测速，不部署",
                    "tokenFingerprint": "0123456789abcdef",
                    "config": {
                        "fixed": {"host": "fixed.example", "web_port": 8080},
                        "target": {"host": "target.example", "web_port": 8081},
                        "downloadMegabytes": 64,
                        "uploadMegabytes": 32,
                        "pingCount": 10,
                        "timeoutSeconds": 60,
                    },
                    "deployment": {"requested": False, "deployed": []},
                    "health": {},
                    "pairSummary": None,
                    "outcome": {"ok": True, "healthOk": True, "pairOk": None},
                }
                _, markdown_path = interactive_vps_pair.save_interactive_report(report)
                markdown = markdown_path.read_text(encoding="utf-8")
                self.assertIn("双向测速结果为“未执行”", markdown)
                self.assertIn("| 未执行 | 未执行 |", markdown)
                self.assertIn("这不是测速失败", markdown)
                self.assertNotIn("双向测速没有成功完成", markdown)
            finally:
                interactive_vps_pair.REPORT_DIR = old_report_dir

    def test_partial_deployment_is_retained_when_next_node_fails(self):
        fixed = interactive_vps_pair.NodeConfig("fixed", "fixed.example", 8080, 22, "root")
        target = interactive_vps_pair.NodeConfig("target", "target.example", 8081, 22, "root")
        deployed = []
        with tempfile.NamedTemporaryFile(delete=False) as temporary_archive:
            archive_path = pathlib.Path(temporary_archive.name)
        with (
            mock.patch.object(interactive_vps_pair, "pause_step"),
            mock.patch.object(deploy_vps_pair, "make_archive", return_value=archive_path),
            mock.patch.object(deploy_vps_pair, "deploy_node", side_effect=[None, RuntimeError("target failed")]),
        ):
            with self.assertRaisesRegex(RuntimeError, "target failed"):
                interactive_vps_pair.deploy_if_needed(
                    "3",
                    fixed,
                    target,
                    "unit-test-token",
                    {"fixed": "fixed-password", "target": "target-password"},
                    deployed,
                )
        self.assertEqual(deployed, ["fixed"])
        self.assertFalse(archive_path.exists())


if __name__ == "__main__":
    unittest.main()
