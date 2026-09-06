import pathlib
import ipaddress
import re
import subprocess
import unittest


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]


def tracked_files():
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
    )
    return [pathlib.Path(item.decode("utf-8")) for item in result.stdout.split(b"\0") if item]


class RepositoryIntegrityTests(unittest.TestCase):
    def test_required_delivery_files_are_tracked(self):
        tracked = {path.as_posix() for path in tracked_files()}
        required = {
            "README.md",
            "LICENSE",
            "start.py",
            "start.cmd",
            "start.sh",
            "new/prepare_runtime.py",
            "new/interactive_vps_pair.py",
            "new/interactive_vps_pair.ps1",
            "new/deploy_vps_pair.py",
            "new/deploy_node.sh",
            "new/requirements-deploy.txt",
            "new/project/vps.html",
            "new/project/vps-agent/common.php",
            "new/project/vps-agent/health.php",
            "new/project/vps-agent/measure.php",
            "new/project/vps-agent/pair.php",
            "shell_script/deploy_librespeed.sh",
        }
        self.assertEqual(set(), required - tracked)

    def test_private_runtime_files_are_not_tracked(self):
        for path in tracked_files():
            normalized = path.as_posix()
            self.assertFalse(normalized.startswith('new/.vps_token'))
            self.assertNotIn(normalized, ('new/.vps_pair.json', 'new/.ssh_known_hosts'))
            self.assertNotIn("__pycache__", path.parts)
            self.assertFalse(normalized.startswith("new/reports/interactive_report_"))
            self.assertFalse(normalized.startswith("new/reports/pair_report_"))
            if normalized.startswith('new/reports/'):
                self.assertEqual(normalized, 'new/reports/README.md')

    def test_all_tracked_shell_scripts_use_lf(self):
        for path in tracked_files():
            if path.suffix == ".sh":
                data = (REPOSITORY_ROOT / path).read_bytes()
                self.assertNotIn(b"\r\n", data, path.as_posix())

    def test_private_backups_and_environments_are_ignored(self):
        paths = ['.venv/pyvenv.cfg', 'venv/pyvenv.cfg', 'new/.venv/bin/python',
                 '.private/audit.md', 'history.bundle', 'new/.vps_token',
                 'new/.vps_pair.json', 'new/.ssh_known_hosts', 'new/reports/private.md']
        result = subprocess.run(['git', 'check-ignore', '--no-index', '--stdin', '-z'],
                                input=('\0'.join(paths) + '\0').encode(),
                                cwd=REPOSITORY_ROOT, check=True, capture_output=True)
        self.assertEqual(set(paths), {p.decode() for p in result.stdout.split(b'\0') if p})
        for path in tracked_files():
            self.assertFalse(set(path.parts) & {'.venv', 'venv', '.private'})
            self.assertNotEqual(path.suffix, '.bundle')

    def test_readme_relative_links_exist(self):
        readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
        links = re.findall(r"\[[^\]]+\]\(([^)]+)\)", readme)
        for link in links:
            link = link.strip("<>").split("#", 1)[0]
            if not link or "://" in link or link.startswith("mailto:"):
                continue
            self.assertTrue((REPOSITORY_ROOT / link).exists(), link)

    def test_runtime_dockerfile_uses_pinned_base_image(self):
        dockerfile = (REPOSITORY_ROOT / "new/project/Dockerfile").read_text(encoding="utf-8")
        self.assertRegex(dockerfile, r"ghcr\.io/librespeed/speedtest@sha256:[0-9a-f]{64}")
        self.assertNotIn("speedtest:latest", dockerfile)

    def test_owned_examples_use_only_documentation_or_loopback_ipv4(self):
        allowed = [ipaddress.ip_network(network) for network in
                   ('127.0.0.0/8', '192.0.2.0/24', '198.51.100.0/24', '203.0.113.0/24')]
        for path in tracked_files():
            if path.as_posix().startswith('new/project/') or path.suffix not in ('.md', '.py', '.sh', '.ps1'):
                continue
            content = (REPOSITORY_ROOT / path).read_text(encoding='utf-8-sig')
            for candidate in re.findall(r'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])', content):
                address = ipaddress.ip_address(candidate)
                self.assertTrue(any(address in network for network in allowed),
                                f'{path}: 非文档示例地址，请先匿名化，不在错误日志回显原地址')


if __name__ == "__main__":
    unittest.main()
