import json
import os
import pathlib
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'new'))
import deploy_vps_pair as deploy

BASH = str(pathlib.Path('D:/Git/bin/bash.exe')) if os.name == 'nt' else shutil.which('bash')


def bash_path(path):
    value = pathlib.Path(path).as_posix()
    return '/' + value[0].lower() + value[2:] if os.name == 'nt' else value


@unittest.skipUnless(BASH and pathlib.Path(BASH).exists(), '需要可用的 Git Bash 或 Linux Bash')
class DeploymentTransactionTests(unittest.TestCase):
    def exercise(self, failure):
        with tempfile.TemporaryDirectory() as directory:
            base = pathlib.Path(directory)
            installation = base / 'installation'
            installation.mkdir()
            binaries = base / 'bin'
            binaries.mkdir()
            state_file = base / 'state.json'
            previous = {'image': 'old-image', 'running': True, 'port': '9000:8080',
                        'environment': {'LIBRESPEED_VPS_TOKEN': 'old-token'}, 'mounts': ['old-volume']}
            state_file.write_text(json.dumps({'failure': failure, 'containers': {'librespeed-vps': previous}, 'calls': []}))
            helpers = {'id': 'echo 0', 'systemctl': 'exit 0', 'sleep': 'exit 0', 'flock': 'exit 0',
                       'docker': shlex.quote(bash_path(sys.executable)) + ' ' + shlex.quote(bash_path(ROOT / 'tests/support/docker_simulator.py')) + ' "$@"'}
            for name, body in helpers.items():
                path = binaries / name
                path.write_text('#!/usr/bin/env bash\n' + body + '\n', encoding='utf-8', newline='\n')
                path.chmod(0o755)
            archive = deploy.make_archive()
            shutil.move(str(archive), str(installation / 'src.tar.gz'))
            script = (ROOT / 'new/deploy_node.sh').read_text(encoding='utf-8')
            script = script.replace('INSTALL_DIR=/opt/librespeed-vps', 'INSTALL_DIR=' + shlex.quote(bash_path(installation)))
            script = 'export PATH=' + shlex.quote(bash_path(binaries)) + ':$PATH\n' + script
            environment = {**os.environ, 'DEPLOY_TEST_STATE': str(state_file)}
            result = subprocess.run([BASH, '-s', '--', '8080', 'http://a.example:8080',
                                     'http://b.example:8080', 'new-token', 'fixed', 'src.tar.gz'],
                                    input=script, text=True, encoding='utf-8', errors='replace', env=environment,
                                    capture_output=True, timeout=90)
            state = json.loads(state_file.read_text())
            self.assertFalse(list(installation.glob('build.*')))
            self.assertFalse((installation / 'src.tar.gz').exists(), result.stdout + result.stderr)
            return result, state, previous

    def test_success_switches_to_candidate(self):
        result, state, _ = self.exercise('none')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(state['containers']['librespeed-vps']['image'], 'new-image')
        self.assertNotIn('librespeed-vps-previous', state['containers'])

    def test_build_failure_keeps_original_running(self):
        result, state, previous = self.exercise('build')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(state['containers'], {'librespeed-vps': previous})
        self.assertFalse(any(call[0] in ('stop', 'run') for call in state['calls']))

    def test_start_failure_restores_original_configuration(self):
        result, state, previous = self.exercise('run')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(state['containers'], {'librespeed-vps': previous})

    def test_unhealthy_candidate_restores_original_configuration(self):
        result, state, previous = self.exercise('health')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(state['containers'], {'librespeed-vps': previous})
