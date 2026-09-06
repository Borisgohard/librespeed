#!/usr/bin/env python3
import argparse
import base64
import getpass
import hashlib
import ipaddress
import io
import json
import os
import pathlib
import re
import secrets
import shlex
import sys
import tarfile
import tempfile
import time
import urllib.request
import uuid
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parent
PROJECT_DIR = ROOT / "project"
TOKEN_FILE = ROOT / ".vps_token"
REPORT_DIR = ROOT / "reports"
KNOWN_HOSTS_FILE = ROOT / ".ssh_known_hosts"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def open_http(request, timeout):
    return urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout)


def redact(value, secret):
    if isinstance(value, str):
        return value.replace(secret, "[已隐去令牌]") if secret else value
    if isinstance(value, dict):
        return {redact(key, secret): redact(item, secret) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item, secret) for item in value]
    return value


def validate_host(value):
    value = value.strip()
    if value.startswith('[') and value.endswith(']'):
        value = value[1:-1]
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        pass
    if len(value) > 253 or not value or any(
        not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label)
        for label in value.rstrip('.').split('.')
    ):
        raise argparse.ArgumentTypeError("请只输入 IP 或域名，不要附带 http://、端口、路径或账号。")
    return value.lower().rstrip('.')


def node_url(host, port, scheme='http'):
    host = validate_host(host)
    return f"{scheme}://{'[' + host + ']' if ':' in host else host}:{port}"


def configure_console():
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name)
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def integer_between(label, minimum, maximum):
    def parse(value):
        try:
            number = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"{label}必须是整数。") from exc
        if not minimum <= number <= maximum:
            raise argparse.ArgumentTypeError(f"{label}必须在 {minimum} 到 {maximum} 之间。")
        return number

    return parse


def parse_args():
    parser = argparse.ArgumentParser(description="把 LibreSpeed 双 VPS 测速项目部署到两台服务器，并生成双向测速报告。")
    parser.add_argument("--fixed-host", type=validate_host, required=True)
    parser.add_argument("--fixed-port", type=integer_between("固定端 Web 端口", 1, 65535), default=8080)
    parser.add_argument("--target-host", type=validate_host, required=True)
    parser.add_argument("--target-port", type=integer_between("测试端 Web 端口", 1, 65535), default=8080)
    parser.add_argument("--ssh-port", type=integer_between("SSH 端口", 1, 65535), default=22)
    parser.add_argument("--user", default="root")
    parser.add_argument("--password", default=os.environ.get("VPS_PASSWORD"))
    parser.add_argument("--fixed-ssh-port", type=integer_between("固定端 SSH 端口", 1, 65535), help="固定端 SSH 端口；默认沿用 --ssh-port。")
    parser.add_argument("--target-ssh-port", type=integer_between("测试端 SSH 端口", 1, 65535), help="测试端 SSH 端口；默认沿用 --ssh-port。")
    parser.add_argument("--fixed-user", help="固定端 SSH 用户；默认沿用 --user。")
    parser.add_argument("--target-user", help="测试端 SSH 用户；默认沿用 --user。")
    parser.add_argument("--fixed-password", default=os.environ.get("FIXED_VPS_PASSWORD"), help=argparse.SUPPRESS)
    parser.add_argument("--target-password", default=os.environ.get("TARGET_VPS_PASSWORD"), help=argparse.SUPPRESS)
    parser.add_argument("--token", default=os.environ.get("LIBRESPEED_VPS_TOKEN"))
    parser.add_argument("--skip-fixed", action="store_true", help="跳过固定端部署。")
    parser.add_argument("--skip-target", action="store_true", help="跳过测试端部署。")
    parser.add_argument("--no-test", action="store_true", help="只部署，不执行双向测速。")
    parser.add_argument("--download-mb", type=integer_between("下载测试大小", 1, 1024), default=64)
    parser.add_argument("--upload-mb", type=integer_between("上传测试大小", 1, 256), default=32)
    parser.add_argument("--ping-count", type=integer_between("延迟采样次数", 3, 60), default=10)
    parser.add_argument("--timeout-seconds", type=integer_between("单项超时时间", 5, 300), default=60)
    return parser.parse_args()


def q(value):
    return shlex.quote(str(value))


def get_node_credentials(args, role):
    role_name = "固定端" if role == "fixed" else "测试端"
    ssh_port = getattr(args, f"{role}_ssh_port") or args.ssh_port
    user = getattr(args, f"{role}_user") or args.user
    password = getattr(args, f"{role}_password") or args.password
    if not password:
        password = getpass.getpass(f"请输入{role_name} SSH 密码：")
    return ssh_port, user, password


def load_paramiko():
    try:
        import paramiko
    except ImportError as exc:
        raise RuntimeError(
            "自动部署需要 paramiko。请先执行：python -m pip install -r new/requirements-deploy.txt"
        ) from exc
    return paramiko


def get_token(args, allow_generate):
    if args.token:
        save_token(args.token)
        return args.token
    if TOKEN_FILE.exists():
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if token:
            protect_token_file()
            return validate_token(token)
    if not allow_generate:
        raise RuntimeError(
            "没有找到可复用的代理令牌。只有同时部署两端时才能自动生成新令牌；"
            "单端部署或纯测速必须提供与另一端一致的 --token 或 LIBRESPEED_VPS_TOKEN。"
        )
    token = secrets.token_urlsafe(32)
    save_token(token)
    return token


def protect_token_file():
    try:
        TOKEN_FILE.chmod(0o600)
    except OSError:
        pass


def save_token(token):
    token = validate_token(token)
    if TOKEN_FILE.exists():
        previous = TOKEN_FILE.read_text(encoding='utf-8').strip()
        if previous and previous != token:
            write_private_text(TOKEN_FILE.with_name('.vps_token.previous'), previous + '\n')
    write_private_text(TOKEN_FILE, token + '\n')


def validate_token(token):
    if not isinstance(token, str) or not 1 <= len(token) <= 256 or not re.fullmatch(r'[!-~]+', token):
        raise ValueError('令牌必须是 1 到 256 个可打印 ASCII 字符，不能包含空格或换行。')
    return token


def write_private_text(path, content):
    path = pathlib.Path(path)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(content)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        pathlib.Path(temporary).unlink(missing_ok=True)


def make_archive():
    if not PROJECT_DIR.exists():
        raise FileNotFoundError(f"没有找到待部署项目目录：{PROJECT_DIR}")
    tmp = tempfile.NamedTemporaryFile(prefix="librespeed-vps-", suffix=".tar.gz", delete=False)
    tmp.close()
    added_dirs = set()

    def add_dir(tar, rel):
        name = rel.as_posix().rstrip("/")
        if not name or name in added_dirs:
            return
        parent = rel.parent
        if parent != rel and parent.as_posix() not in (".", ""):
            add_dir(tar, parent)
        path = PROJECT_DIR / rel
        info = tarfile.TarInfo(name)
        info.type = tarfile.DIRTYPE
        info.mtime = 0
        info.mode = path.stat().st_mode if path.exists() else 0o755
        tar.addfile(info)
        added_dirs.add(name)

    def add_file(tar, path, rel):
        parent = rel.parent
        if parent.as_posix() not in (".", ""):
            add_dir(tar, parent)
        data = path.read_bytes()
        if path.suffix == ".sh":
            data = data.replace(b"\r\n", b"\n")
        info = tarfile.TarInfo(rel.as_posix())
        info.size = len(data)
        info.mtime = 0
        info.mode = path.stat().st_mode
        tar.addfile(info, io.BytesIO(data))

    def add_path(tar, path, rel):
        if path.is_dir():
            add_dir(tar, rel)
            for child in sorted(path.iterdir()):
                add_path(tar, child, rel / child.name)
        elif path.is_file():
            add_file(tar, path, rel)

    include_paths = [
        pathlib.Path("Dockerfile"),
        pathlib.Path("index-modern.html"),
        pathlib.Path("index-classic.html"),
        pathlib.Path("vps.html"),
        pathlib.Path("docker") / "entrypoint.sh",
        pathlib.Path('vps-agent/common.php'),
        pathlib.Path('vps-agent/health.php'),
        pathlib.Path('vps-agent/measure.php'),
        pathlib.Path('vps-agent/pair.php'),
    ]
    try:
        with tarfile.open(tmp.name, "w:gz") as tar:
            for rel in include_paths:
                source = PROJECT_DIR / rel
                if not source.exists():
                    raise FileNotFoundError(f"部署包缺少必需文件或目录：{source}")
                add_path(tar, source, rel)
    except BaseException:
        pathlib.Path(tmp.name).unlink(missing_ok=True)
        raise
    return pathlib.Path(tmp.name)


class Remote:
    def __init__(self, host, port, user, password):
        paramiko = load_paramiko()
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.client = paramiko.SSHClient()
        self.client.load_system_host_keys()
        if KNOWN_HOSTS_FILE.exists():
            self.client.load_host_keys(str(KNOWN_HOSTS_FILE))

        class ConfirmHostKey(paramiko.MissingHostKeyPolicy):
            def missing_host_key(self, client, hostname, key):
                fingerprint = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')
                print(f'首次连接 {hostname}，服务器指纹：SHA256:{fingerprint}')
                answer = input('核对云控制台提供的 SSH 指纹后，输入 yes 信任此服务器：').strip().lower()
                if answer != 'yes':
                    raise RuntimeError('未确认服务器身份，已停止 SSH 连接。')
                client.get_host_keys().add(hostname, key.get_name(), key)
                client.save_host_keys(str(KNOWN_HOSTS_FILE))
                KNOWN_HOSTS_FILE.chmod(0o600)

        self.client.set_missing_host_key_policy(ConfirmHostKey())

    def __enter__(self):
        try:
            self.client.connect(
                self.host, port=self.port, username=self.user, password=self.password,
                timeout=25, banner_timeout=30, auth_timeout=25,
                look_for_keys=False, allow_agent=False,
            )
        except BaseException:
            self.client.close()
            raise
        return self

    def __exit__(self, exc_type, exc, tb):
        self.client.close()

    def put(self, local, remote):
        sftp = self.client.open_sftp()
        try:
            sftp.put(str(local), remote)
        finally:
            sftp.close()

    def run(self, command, timeout=1800):
        chan = self.client.get_transport().open_session()
        chan.get_pty()
        chan.exec_command(command)
        output = io.StringIO()
        start = time.monotonic()
        while True:
            while chan.recv_ready():
                data = chan.recv(8192).decode(errors="replace")
                output.write(data)
                print(data, end="")
            while chan.recv_stderr_ready():
                data = chan.recv_stderr(8192).decode(errors="replace")
                output.write(data)
                print(data, end="")
            if chan.exit_status_ready():
                while chan.recv_ready():
                    data = chan.recv(8192).decode(errors="replace")
                    output.write(data)
                    print(data, end="")
                rc = chan.recv_exit_status()
                if rc != 0:
                    raise RuntimeError(f"{self.host} 远端命令执行失败，退出码为 {rc}")
                return output.getvalue()
            if time.monotonic() - start > timeout:
                chan.close()
                raise TimeoutError(f"{self.host} 远端命令执行超时")
            time.sleep(0.3)


def deploy_command(port, public_url, peer_url, token, node_name, archive_name='src.tar.gz'):
    script = (ROOT / 'deploy_node.sh').read_text(encoding='utf-8')
    arguments = [port, public_url, peer_url, validate_token(token), node_name, archive_name]
    return 'bash -c ' + q(script) + ' -- ' + ' '.join(q(value) for value in arguments)


def deploy_node(name, host, ssh_port, user, password, web_port, public_url, peer_url, token, archive):
    print(f"\n===== 部署节点 {name} {host}:{web_port} =====")
    with Remote(host, ssh_port, user, password) as remote:
        archive_name = 'src-' + uuid.uuid4().hex + '.tar.gz'
        remote.run("umask 077; mkdir -p /opt/librespeed-vps")
        try:
            remote.put(archive, '/opt/librespeed-vps/' + archive_name)
            remote.run(deploy_command(web_port, public_url, peer_url, token, name, archive_name), timeout=2400)
        finally:
            if remote.client.get_transport() and remote.client.get_transport().is_active():
                try:
                    remote.run('rm -f -- ' + q('/opt/librespeed-vps/' + archive_name), timeout=30)
                except Exception:
                    print(f'警告：未能再次确认 {host} 的上传包清理；请检查 /opt/librespeed-vps/{archive_name}。部署脚本自身也会清理该文件。')


def call_pair_report(fixed_url, target_url, token, args):
    payload = {
        "fixed": fixed_url,
        "target": target_url,
        "downloadMegabytes": args.download_mb,
        "uploadMegabytes": args.upload_mb,
        "pingCount": args.ping_count,
        "timeoutSeconds": args.timeout_seconds,
    }
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        fixed_url.rstrip("/") + "/vps-agent/pair.php",
        data=data,
        headers={
            "Content-Type": "application/json",
            "X-LibreSpeed-Token": token,
        },
        method="POST",
    )
    request_timeout = pair_request_timeout(args.timeout_seconds, args.ping_count)
    with open_http(request, timeout=request_timeout) as response:
        raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError('接口响应超过 2 MiB，已停止读取。')
        return redact(json.loads(raw.decode('utf-8')), token)


def pair_request_timeout(timeout_seconds, ping_count):
    direction_timeout = (timeout_seconds * 2) + (ping_count * min(timeout_seconds, 3)) + 30
    return (direction_timeout * 2) + 30


def metric_line(label, item):
    result = measurement_result(item)
    dl = result.get("download") if isinstance(result.get("download"), dict) else {}
    ul = result.get("upload") if isinstance(result.get("upload"), dict) else {}
    lt = result.get("latency") if isinstance(result.get("latency"), dict) else {}
    return (
        f"| {label} | {dl.get('mbps', '失败')} 兆比特每秒 | "
        f"{ul.get('mbps', '失败')} 兆比特每秒 | {lt.get('avgMs', '失败')} 毫秒 | "
        f"{lt.get('jitterMs', '失败')} 毫秒 | {lt.get('lossPct', '失败')}% |"
    )


def measurement_result(item):
    result = item.get("result", item) if isinstance(item, dict) else {}
    return result if isinstance(result, dict) else {}


def measurement_ok(item):
    result = measurement_result(item)
    return all(
        isinstance(result.get(metric), dict) and result[metric].get("status") == "ok"
        for metric in ("download", "upload", "latency")
    )


def pair_report_ok(report):
    return (
        isinstance(report, dict)
        and report.get("status") == "ok"
        and measurement_ok(report.get("forward", {}))
        and measurement_ok(report.get("reverse", {}))
    )


def save_reports(report, fixed_url, target_url):
    REPORT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    json_path = REPORT_DIR / f"pair_report_{stamp}.json"
    md_path = REPORT_DIR / f"pair_report_{stamp}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md = [
        "# LibreSpeed 双 VPS 测速执行反馈报告",
        "",
        f"本报告由本地命令行脚本在 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 生成。固定端先主动测量测试端，再让测试端反向测量固定端；数据在两台 VPS 之间传输，不经过本地电脑。本入口没有单独检查网页健康，不能据此宣称网页功能已经验收。",
        "",
        f"固定端地址是 `{fixed_url}`，测试端地址是 `{target_url}`。本轮结果：{'六个测速子项全部完成' if pair_report_ok(report) else '未全部完成，请检查下面的失败记录'}。",
        "",
        "## 指标摘要",
        "",
        "| 测试方向 | 下载速度 | 上传速度 | 平均延迟 | 延迟抖动 | HTTP 请求失败率 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
        metric_line("固定端 -> 测试端", report.get("forward", {})),
        metric_line("测试端 -> 固定端", report.get("reverse", {})),
        "",
        "## 结果含义",
        "",
        "固定端到测试端的下载速度，表示固定端从测试端拉取随机数据时测得的吞吐能力；固定端到测试端的上传速度，表示固定端向测试端提交数据时测得的吞吐能力。反向数据同理。平均延迟、延迟抖动和请求失败率来自多次 HTTP 空响应请求，和原版 LibreSpeed 的浏览器测速思路保持一致。这里的失败率不是 ICMP 丢包率。",
        "",
        "## 详细数据位置",
        "",
        f"- `{json_path}`",
        "",
        "## 访问入口",
        "",
        f"- 固定端原版 LibreSpeed：{fixed_url}/",
        f"- 固定端双 VPS 页面：{fixed_url}/vps.html",
        f"- 测试端原版 LibreSpeed：{target_url}/",
        f"- 测试端双 VPS 页面：{target_url}/vps.html",
        f"- 本地令牌文件：{TOKEN_FILE}",
    ]
    for key, label in (('forward', '固定端发起'), ('reverse', '测试端发起')):
        result = measurement_result(report.get(key))
        md.extend(['', '## ' + label + '的详细记录', ''])
        if result.get('message') or result.get('error'):
            md.append('该方向反馈：' + str(result.get('message') or result.get('error')))
        for metric, name in (('download', '下载'), ('upload', '上传'), ('latency', '延迟')):
            data = result.get(metric)
            if not isinstance(data, dict):
                md.append(name + '没有返回可用结果。')
                continue
            md.append(name + ('完成。' if data.get('status') == 'ok' else '未完成。'))
            if metric == 'latency':
                md.append(f"共发出 {data.get('sent', '未返回')} 次请求，成功 {data.get('received', '未返回')} 次，失败 {data.get('failed', '未返回')} 次；最小延迟 {data.get('minMs', '未返回')} 毫秒，最大 {data.get('maxMs', '未返回')} 毫秒。成功样本依次为 {data.get('samplesMs', [])}，单位毫秒。")
            else:
                detail = data.get('detail') if isinstance(data.get('detail'), dict) else data
                md.append(f"实际记录 {detail.get('bytes', '未返回')} 字节，用时 {detail.get('seconds', '未返回')} 秒，HTTP 状态 {detail.get('httpCode', '未返回')}。")
            if data.get('message') or data.get('error'):
                md.append('原因：' + str(data.get('message') or data.get('error')))
    md_path.write_text("\n".join(md) + "\n", encoding="utf-8")
    return json_path, md_path


def main():
    configure_console()
    args = parse_args()
    deploy_fixed = not args.skip_fixed
    deploy_target = not args.skip_target
    if not deploy_fixed and not deploy_target and args.no_test:
        raise SystemExit("错误：固定端、测试端和测速都被跳过，没有可执行动作。")

    if args.fixed_host == args.target_host:
        raise SystemExit('错误：双 VPS 测速必须指定两台不同的服务器。')
    token = get_token(args, allow_generate=deploy_fixed and deploy_target)
    fixed_credentials = get_node_credentials(args, "fixed") if deploy_fixed else None
    target_credentials = get_node_credentials(args, "target") if deploy_target else None
    fixed_url = node_url(args.fixed_host, args.fixed_port)
    target_url = node_url(args.target_host, args.target_port)
    archive = make_archive() if deploy_fixed or deploy_target else None
    try:
        if deploy_fixed and fixed_credentials:
            ssh_port, user, password = fixed_credentials
            deploy_node("fixed", args.fixed_host, ssh_port, user, password, args.fixed_port, fixed_url, target_url, token, archive)
        if deploy_target and target_credentials:
            ssh_port, user, password = target_credentials
            deploy_node("target", args.target_host, ssh_port, user, password, args.target_port, target_url, fixed_url, token, archive)
        if not args.no_test:
            print("\n===== 开始执行双向测速并生成报告 =====")
            report = call_pair_report(fixed_url, target_url, token, args)
            json_path, md_path = save_reports(report, fixed_url, target_url)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            print(f"\n详细 JSON 报告：{json_path}")
            print(f"中文 Markdown 报告：{md_path}")
            print(f"本地令牌文件：{TOKEN_FILE}")
            if not pair_report_ok(report):
                print("错误：报告已保存，但至少一个方向的测速未完整成功。", file=sys.stderr)
                return 3
        return 0
    finally:
        if archive is not None:
            try:
                archive.unlink()
            except OSError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
