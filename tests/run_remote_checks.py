"""在已有 Docker 的节点上隔离验证 PHP；结束后删除本次临时资源。"""
import argparse
import getpass
import os
import pathlib
import sys
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'new'))
import deploy_vps_pair as deploy


def main():
    deploy.configure_console()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True, type=deploy.validate_host)
    parser.add_argument('--ssh-port', type=int, default=22)
    args = parser.parse_args()
    password = os.environ.get('VPS_PASSWORD') or getpass.getpass('测试节点 SSH 密码：')
    identifier = uuid.uuid4().hex
    container = 'librespeed-review-' + identifier
    directory = '/opt/librespeed-vps/review-' + identifier
    with deploy.Remote(args.host, args.ssh_port, 'root', password) as remote:
        try:
            remote.run('umask 077; mkdir -p ' + deploy.q(directory + '/new/project/vps-agent') + ' ' + deploy.q(directory + '/tests/support'))
            files = ['tests/test_agent_runtime.py', 'tests/support/php_container.sh', 'new/project/vps-agent/common.php']
            for name in files:
                remote.put(ROOT / name, directory + '/' + name)
            remote.run(
                'docker run -d --name ' + deploy.q(container) + ' --network host '
                '--mount ' + deploy.q('type=bind,src=' + directory + ',dst=/work,readonly')
                + ' --workdir /work --entrypoint sleep librespeed-vps:local infinity'
            )
            remote.run('chmod 700 ' + deploy.q(directory + '/tests/support/php_container.sh'))
            remote.run(
                'cd ' + deploy.q(directory) + ' && LIBRESPEED_TEST_CONTAINER=' + deploy.q(container)
                + ' LIBRESPEED_TEST_PHP=' + deploy.q(directory + '/tests/support/php_container.sh')
                + ' python3 -B -m unittest discover -s tests -p test_agent_runtime.py -v', timeout=180
            )
        finally:
            remote.run('docker rm -f ' + deploy.q(container) + ' >/dev/null 2>&1 || true')
            remote.run('rm -rf -- ' + deploy.q(directory))


if __name__ == '__main__':
    main()
