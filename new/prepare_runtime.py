"""仅在用户选择部署且缺少依赖时，征得同意后准备本地环境。"""

import os
import pathlib
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]


def venv_python(environment):
    return environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def prepare_and_restart():
    print('自动部署需要 Paramiko；健康检查和日常测速不需要它。')
    print('可以在项目 .venv 中安装固定版本依赖，需要联网，不修改系统 Python，不连接 VPS。')
    if input('是否准备本地部署环境？输入 y 或 yes 同意，直接回车取消 [y/N]：').strip().lower() not in ('y', 'yes'):
        print('已取消环境准备，没有安装依赖或连接服务器。')
        return 1
    environment = ROOT / '.venv'
    executable = venv_python(environment)
    if environment.exists() and (not executable.is_file() or not (environment / 'pyvenv.cfg').is_file()):
        raise RuntimeError('已有 .venv 不完整，请先移走备份再重试；不会自动覆盖其中的文件。')
    try:
        if not environment.exists():
            subprocess.run([sys.executable, '-m', 'venv', str(environment)], check=True, timeout=120)
        subprocess.run([str(executable), '-m', 'pip', 'install', '--disable-pip-version-check', '--no-input',
                        '--retries', '2', '--timeout', '30', '-r',
                        str(ROOT / 'new/requirements-deploy.txt')], check=True, timeout=300)
        subprocess.run([str(executable), '-c', 'import paramiko'], check=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError('环境准备失败，没有连接 VPS。请检查网络与磁盘；Linux 提示缺少 venv 时安装 python3-venv。'
                           '如果留下不完整 .venv，请先移走备份；完整环境可以重新启动后重试安装。') from exc
    print('依赖已验证。现在重新打开向导，请再次选择本次动作；安装环境不会代替部署确认。', flush=True)
    return subprocess.run([str(executable), '-B', str(ROOT / 'start.py')], check=False).returncode
