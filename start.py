#!/usr/bin/env python3
"""统一入口：校验 Python，优先使用本项目虚拟环境，再启动原有向导。"""

import pathlib
import subprocess
import sys


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    if sys.version_info < (3, 10):
        print('需要 Python 3.10 或更高版本，请升级后重新启动。', file=sys.stderr)
        return 2
    root = pathlib.Path(__file__).resolve().parent
    sys.path.insert(0, str(root / 'new'))
    from prepare_runtime import venv_python
    environment = root / '.venv'
    if environment.exists() and pathlib.Path(sys.prefix).resolve() != environment.resolve():
        executable = venv_python(environment)
        if not executable.is_file() or not (environment / 'pyvenv.cfg').is_file():
            print('项目中已有不完整的 .venv；请先移走备份，再重试。不会自动覆盖。', file=sys.stderr)
            return 2
        return subprocess.run([str(executable), '-B', str(root / 'start.py')], check=False).returncode
    from interactive_vps_pair import main as run_wizard
    return run_wizard()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print('\n用户取消，流程已停止。')
        raise SystemExit(1)
    except OSError as exc:
        print(f'无法启动 Python：{exc}', file=sys.stderr)
        raise SystemExit(2)
