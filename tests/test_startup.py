import contextlib
import importlib
import io
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'new'))
runtime = importlib.import_module('prepare_runtime')
wizard = importlib.import_module('interactive_vps_pair')


class StartupTests(unittest.TestCase):
    def test_declining_install_does_not_create_files_or_run_commands(self):
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(runtime, 'ROOT', pathlib.Path(directory)), \
             mock.patch('builtins.input', return_value=''), \
             mock.patch.object(runtime.subprocess, 'run') as run, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runtime.prepare_and_restart(), 1)
            run.assert_not_called()
            self.assertEqual(list(pathlib.Path(directory).iterdir()), [])

    def test_install_eof_is_not_consent(self):
        with mock.patch('builtins.input', side_effect=EOFError), \
             mock.patch.object(runtime.subprocess, 'run') as run, \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(EOFError):
                runtime.prepare_and_restart()
            run.assert_not_called()

    def test_install_uses_only_project_venv_and_pinned_requirements(self):
        with tempfile.TemporaryDirectory(prefix='startup space ') as directory, \
             mock.patch.object(runtime, 'ROOT', pathlib.Path(directory)), \
             mock.patch('builtins.input', return_value='yes'), \
             mock.patch.object(runtime.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)) as run, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runtime.prepare_and_restart(), 1)
            calls = run.call_args_list
            environment = pathlib.Path(directory) / '.venv'
            executable = runtime.venv_python(environment)
            self.assertIn(str(environment), calls[0].args[0])
            self.assertEqual(calls[1].args[0][0], str(executable))
            self.assertIn(str(pathlib.Path(directory) / 'new/requirements-deploy.txt'), calls[1].args[0])
            self.assertEqual(calls[-1].args[0], [str(executable), '-B', str(pathlib.Path(directory) / 'start.py')])

    def test_failed_install_never_launches_wizard(self):
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(runtime, 'ROOT', pathlib.Path(directory)), \
             mock.patch('builtins.input', return_value='y'), \
             mock.patch.object(runtime.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'venv')) as run, \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, '环境准备失败'):
                runtime.prepare_and_restart()
            self.assertEqual(run.call_count, 1)

    def test_invalid_existing_venv_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = pathlib.Path(directory) / '.venv'
            environment.mkdir()
            with mock.patch.object(runtime, 'ROOT', pathlib.Path(directory)), \
                 mock.patch('builtins.input', return_value='y'), \
                 mock.patch.object(runtime.subprocess, 'run') as run, \
                 contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError, '已有'):
                    runtime.prepare_and_restart()
                run.assert_not_called()

    def test_daily_action_never_loads_or_installs_ssh_dependency(self):
        with mock.patch.object(wizard, 'load_saved_nodes', return_value={}), \
             mock.patch.object(wizard, 'pause_step'), \
             mock.patch.object(wizard, 'prompt_choice', return_value='2'), \
             mock.patch.object(wizard, 'prompt_host', side_effect=EOFError), \
             mock.patch.object(wizard.deploy_vps_pair, 'load_paramiko') as load, \
             mock.patch.object(runtime, 'prepare_and_restart') as prepare, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(wizard.main(), 1)
            load.assert_not_called()
            prepare.assert_not_called()

    def test_zip_style_copy_starts_without_git_configs_or_dependencies(self):
        with tempfile.TemporaryDirectory(prefix='librespeed clean space ') as directory:
            root = pathlib.Path(directory)
            (root / 'new').mkdir()
            for relative in ('start.py', 'new/interactive_vps_pair.py', 'new/deploy_vps_pair.py',
                             'new/prepare_runtime.py'):
                shutil.copyfile(ROOT / relative, root / relative)
            result = subprocess.run([sys.executable, '-B', '-S', str(root / 'start.py')],
                                    input=b'', capture_output=True, cwd=directory, timeout=20,
                                    env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
            self.assertEqual(result.returncode, 1, result.stderr.decode('utf-8', errors='replace'))
            self.assertIn('输入已结束或用户取消', result.stdout.decode('utf-8'))
            self.assertFalse((root / '.venv').exists())
            self.assertFalse((root / 'new/.vps_token').exists())
            self.assertFalse((root / 'new/.vps_pair.json').exists())

    @unittest.skipUnless(os.name == 'nt', '仅 Windows 验证 CMD 与 PowerShell 包装入口')
    def test_windows_launcher_cancels_in_clean_directory_with_spaces(self):
        with tempfile.TemporaryDirectory(prefix='librespeed windows space ') as directory:
            root = pathlib.Path(directory)
            (root / 'new').mkdir()
            for relative in ('start.cmd', 'start.py', 'new/interactive_vps_pair.ps1',
                             'new/interactive_vps_pair.py', 'new/deploy_vps_pair.py',
                             'new/prepare_runtime.py'):
                shutil.copyfile(ROOT / relative, root / relative)
            self.assertTrue((root / 'new/interactive_vps_pair.ps1').read_bytes().startswith(b'\xef\xbb\xbf'))
            result = subprocess.run(['cmd.exe', '/d', '/c', str(root / 'start.cmd')],
                                    input=b'', capture_output=True, cwd=ROOT.parent, timeout=30)
            self.assertEqual(result.returncode, 1, result.stderr.decode('utf-8', errors='replace'))
            self.assertIn('输入已结束或用户取消', result.stdout.decode('utf-8', errors='replace'))
            self.assertFalse((root / '.venv').exists())

    @unittest.skipIf(os.name == 'nt', 'Shell 启动入口由 Linux CI 验证')
    def test_shell_launcher_cancels_outside_project_directory(self):
        with tempfile.TemporaryDirectory(prefix='librespeed shell space ') as directory:
            root = pathlib.Path(directory)
            (root / 'new').mkdir()
            for relative in ('start.sh', 'start.py', 'new/interactive_vps_pair.py',
                             'new/deploy_vps_pair.py', 'new/prepare_runtime.py'):
                shutil.copyfile(ROOT / relative, root / relative)
            result = subprocess.run(['bash', str(root / 'start.sh')], input=b'', capture_output=True,
                                    cwd=ROOT.parent, timeout=20,
                                    env={**os.environ, 'PYTHON_BIN': sys.executable})
            self.assertEqual(result.returncode, 1, result.stderr.decode('utf-8', errors='replace'))
            self.assertIn('输入已结束或用户取消', result.stdout.decode('utf-8'))


if __name__ == '__main__':
    unittest.main()
