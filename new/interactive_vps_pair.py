#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LibreSpeed 双 VPS 测速项目的本地交互式运行脚本。"""

from __future__ import annotations

import getpass
import hashlib
import json
import pathlib
import secrets
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any

import deploy_vps_pair


ROOT = pathlib.Path(__file__).resolve().parent
TOKEN_FILE = ROOT / ".vps_token"
REPORT_DIR = ROOT / "reports"
CONFIG_FILE = ROOT / '.vps_pair.json'


DEFAULT_FIXED_HOST = None
DEFAULT_FIXED_PORT = 8080
DEFAULT_TARGET_HOST = None
DEFAULT_TARGET_PORT = 8080
DEFAULT_SSH_PORT = 22
DEFAULT_SSH_USER = "root"
HTTP_RETRIES = 3
HTTP_RETRY_DELAY_SECONDS = 2
DEPLOY_FIXED_ACTIONS = {"3", "5"}
DEPLOY_TARGET_ACTIONS = {"3", "4"}
DEPLOY_ACTIONS = DEPLOY_FIXED_ACTIONS | DEPLOY_TARGET_ACTIONS
HEALTH_ACTIONS = {"1", "2", "3", "4", "5"}
PAIR_ACTIONS = {"2", "3", "4", "5", "6"}


@dataclass
class NodeConfig:
    role: str
    host: str
    web_port: int
    ssh_port: int
    ssh_user: str
    scheme: str = 'http'

    @property
    def url(self) -> str:
        return deploy_vps_pair.node_url(self.host, self.web_port, self.scheme)


def load_saved_nodes() -> dict[str, Any]:
    if not CONFIG_FILE.exists():
        return {}
    data = json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or data.get('version') != 1:
        raise ValueError('本地节点配置格式不受支持，请先检查 new/.vps_pair.json。')
    nodes = {}
    for role in ('fixed', 'target'):
        node = data.get(role)
        if not isinstance(node, dict):
            raise ValueError('本地节点配置缺少固定端或测试端。')
        host = deploy_vps_pair.validate_host(node.get('host', ''))
        for key in ('web_port', 'ssh_port'):
            if type(node.get(key)) is not int or not 1 <= node[key] <= 65535:
                raise ValueError('本地配置的端口必须在 1 到 65535 之间。')
        if node.get('scheme', 'http') not in ('http', 'https') or not isinstance(node.get('ssh_user'), str):
            raise ValueError('本地节点配置中的协议或 SSH 用户无效。')
        nodes[role] = {**node, 'host': host}
    return nodes


def save_nodes(fixed: NodeConfig, target: NodeConfig) -> None:
    deploy_vps_pair.write_private_text(CONFIG_FILE, json.dumps(
        {'version': 1, 'fixed': asdict(fixed), 'target': asdict(target)}, ensure_ascii=False, indent=2
    ) + '\n')


def traffic_budget(download_mb: int, upload_mb: int) -> str:
    total = 2 * (download_mb + upload_mb)
    return (
        f'一轮双向测速预计传输 {total} MiB 有效数据；每台 VPS 约发送 {total // 2} MiB、'
        f'接收 {total // 2} MiB。实际计费还取决于服务商规则，协议开销另计。'
    )


def configure_console() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name)
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def prompt_text(label: str, default: str | None = None, required: bool = True) -> str:
    suffix = f" [{default}]" if default not in (None, "") else ""
    while True:
        value = input(f"{label}{suffix}: ").strip()
        if not value and default is not None:
            value = str(default)
        if value or not required:
            return value
        print("该项不能为空。")


def prompt_int(label: str, default: int, min_value: int = 1, max_value: int = 65535) -> int:
    while True:
        raw = prompt_text(label, str(default))
        try:
            value = int(raw)
        except ValueError:
            print("请输入整数。")
            continue
        if min_value <= value <= max_value:
            return value
        print(f"请输入 {min_value} 到 {max_value} 之间的整数。")


def prompt_yes_no(label: str, default: bool = True) -> bool:
    default_label = "Y/n" if default else "y/N"
    while True:
        value = input(f"{label} [{default_label}]: ").strip().lower()
        if not value:
            return default
        if value in ("y", "yes", "1", "是"):
            return True
        if value in ("n", "no", "0", "否"):
            return False
        print("请输入 y 或 n。")


def prompt_choice(label: str, choices: dict[str, str], default: str) -> str:
    print(label)
    for key, desc in choices.items():
        marker = " 默认" if key == default else ""
        print(f"  {key}. {desc}{marker}")
    while True:
        value = input(f"请选择 [{default}]: ").strip() or default
        if value in choices:
            return value
        print("无效选择。")


def pause_step(label: str) -> None:
    input(f"\n{label}。按 Enter 继续...")


def prompt_host(label: str, default: str | None) -> str:
    while True:
        try:
            return deploy_vps_pair.validate_host(prompt_text(label, default))
        except deploy_vps_pair.argparse.ArgumentTypeError as exc:
            print(exc)


def load_or_prompt_token(allow_generate: bool) -> str:
    if TOKEN_FILE.exists():
        saved = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if saved:
            if prompt_yes_no(f"检测到已有代理访问令牌文件：{TOKEN_FILE}，是否复用", True):
                return deploy_vps_pair.validate_token(saved)

    while True:
        if allow_generate:
            token = getpass.getpass("请输入代理访问令牌；直接回车生成，确认执行且连通后才保存：").strip()
        else:
            token = getpass.getpass("请输入已经部署在另一端的代理访问令牌；本次不能留空：").strip()
        if token:
            try:
                deploy_vps_pair.validate_token(token)
                break
            except ValueError as exc:
                print(exc)
                continue
        if allow_generate:
            token = secrets.token_urlsafe(32)
            print("已生成新的代理访问令牌。")
            break
        print("单端部署或纯 HTTP 操作不能自动生成新令牌，否则会和现有服务器不一致。")
    return token


def token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def execution_state_text(value: bool | None) -> str:
    if value is None:
        return "未执行"
    return "通过" if value else "未通过"


def http_request_once(url: str, token: str | None = None, method: str = "GET", payload: dict[str, Any] | None = None, timeout: int = 20) -> dict[str, Any]:
    headers = {"User-Agent": "LibreSpeed-VPS-Interactive/1.0"}
    data = None
    if token:
        headers["X-LibreSpeed-Token"] = token
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    started = time.perf_counter()
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with deploy_vps_pair.open_http(request, timeout=timeout) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise ValueError('接口响应超过 2 MiB，已停止读取。')
            elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
            text = deploy_vps_pair.redact(raw.decode("utf-8", errors="replace"), token)
            parsed = None
            try:
                parsed = deploy_vps_pair.redact(json.loads(text), token) if text else None
            except json.JSONDecodeError:
                parsed = None
            return {
                "ok": 200 <= response.status < 300,
                "statusCode": response.status,
                "elapsedMs": elapsed_ms,
                "url": url,
                "bodyBytes": len(raw),
                "json": parsed,
                "textPreview": text[:500],
            }
    except urllib.error.HTTPError as exc:
        with exc:
            raw = exc.read(2 * 1024 * 1024)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        text = deploy_vps_pair.redact(raw.decode("utf-8", errors="replace"), token)
        parsed = None
        try:
            parsed = deploy_vps_pair.redact(json.loads(text), token) if text else None
        except json.JSONDecodeError:
            parsed = None
        return {
            "ok": False,
            "statusCode": exc.code,
            "elapsedMs": elapsed_ms,
            "url": url,
            "bodyBytes": len(raw),
            "json": parsed,
            "textPreview": text[:500],
            "error": deploy_vps_pair.redact(str(exc), token),
        }
    except Exception as exc:
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        return {
            "ok": False,
            "statusCode": None,
            "elapsedMs": elapsed_ms,
            "url": url,
            "error": deploy_vps_pair.redact(repr(exc), token),
        }


def http_request(url: str, token: str | None = None, method: str = "GET", payload: dict[str, Any] | None = None, timeout: int = 20) -> dict[str, Any]:
    last_result: dict[str, Any] | None = None
    attempts = HTTP_RETRIES if method == 'GET' else 1
    for attempt in range(1, attempts + 1):
        result = http_request_once(url, token=token, method=method, payload=payload, timeout=timeout)
        result["attempt"] = attempt
        last_result = result
        if result.get("ok") or result.get('statusCode') not in (None, 502, 503, 504):
            return result
        if attempt < attempts:
            time.sleep(HTTP_RETRY_DELAY_SECONDS)
    return last_result or {"ok": False, "url": url, "error": "请求未执行"}


def run_health(node: NodeConfig, token: str) -> dict[str, Any]:
    checks = {
        "rootPage": http_request(f"{node.url}/", timeout=15),
        "vpsPage": http_request(f"{node.url}/vps.html", timeout=15),
        "agentHealth": http_request(f"{node.url}/vps-agent/health.php", token=token, timeout=15),
    }
    agent = checks['agentHealth']
    payload = agent.get('json')
    if not (isinstance(payload, dict) and payload.get('status') == 'ok'
            and isinstance(payload.get('agentVersion'), str)
            and isinstance(payload.get('node'), dict)
            and isinstance(payload.get('originalLibreSpeed'), dict)):
        agent['ok'] = False
        agent['error'] = agent.get('error') or '接口未返回有效的 LibreSpeed 代理健康数据。'
    ok = all(item.get("ok") for item in checks.values())
    clock = analyze_agent_clock(checks["agentHealth"])
    return {
        "node": asdict(node),
        "publicUrl": node.url,
        "ok": ok,
        "clock": clock,
        "checks": checks,
    }


def analyze_agent_clock(agent_health: dict[str, Any]) -> dict[str, Any]:
    payload = agent_health.get("json")
    reported_at = payload.get("time") if isinstance(payload, dict) else None
    if not isinstance(reported_at, str):
        return {"checked": False, "ok": None, "message": "代理没有返回服务器时间。"}
    try:
        remote_time = datetime.fromisoformat(reported_at.replace("Z", "+00:00"))
        if remote_time.tzinfo is None:
            remote_time = remote_time.replace(tzinfo=timezone.utc)
        offset_seconds = round((remote_time - datetime.now(timezone.utc)).total_seconds(), 1)
    except ValueError:
        return {"checked": False, "ok": None, "reportedAt": reported_at, "message": "服务器时间格式无法解析。"}
    clock_ok = abs(offset_seconds) <= 30
    return {
        "checked": True,
        "ok": clock_ok,
        "reportedAt": reported_at,
        "offsetSeconds": offset_seconds,
        "message": "服务器时间与本地时间差在 30 秒以内。" if clock_ok else "服务器时间与本地时间差超过 30 秒，报告中的服务器时间线可能不准确。",
    }


def run_pair_report(fixed: NodeConfig, target: NodeConfig, token: str, download_mb: int, upload_mb: int, ping_count: int, timeout_seconds: int) -> dict[str, Any]:
    payload = {
        "fixed": fixed.url,
        "target": target.url,
        "downloadMegabytes": download_mb,
        "uploadMegabytes": upload_mb,
        "pingCount": ping_count,
        "timeoutSeconds": timeout_seconds,
    }
    return http_request(
        f"{fixed.url}/vps-agent/pair.php",
        token=token,
        method="POST",
        payload=payload,
        timeout=deploy_vps_pair.pair_request_timeout(timeout_seconds, ping_count),
    )


def summarize_direction(item: dict[str, Any]) -> dict[str, Any]:
    result = deploy_vps_pair.measurement_result(item)
    dl = result.get('download') if isinstance(result.get('download'), dict) else {}
    ul = result.get('upload') if isinstance(result.get('upload'), dict) else {}
    latency = result.get('latency') if isinstance(result.get('latency'), dict) else {}
    dl_detail = dl.get("detail") if isinstance(dl.get("detail"), dict) else {}
    ul_detail = ul.get("detail") if isinstance(ul.get("detail"), dict) else {}
    return {
        "status": result.get("status"),
        "error": result.get('message') or result.get('error'),
        "downloadStatus": dl.get("status"),
        "downloadError": dl.get('message') or dl.get("error"),
        "downloadHttpCode": dl.get("httpCode") or dl_detail.get("httpCode"),
        "downloadMbps": dl.get("mbps"),
        "downloadSeconds": dl.get('seconds', dl_detail.get('seconds')),
        "downloadBytes": dl.get('bytes', dl_detail.get('bytes')),
        "uploadStatus": ul.get("status"),
        "uploadError": ul.get('message') or ul.get("error"),
        "uploadHttpCode": ul.get("httpCode") or ul_detail.get("httpCode"),
        "uploadMbps": ul.get("mbps"),
        "uploadSeconds": ul.get('seconds', ul_detail.get('seconds')),
        "uploadBytes": ul.get('bytes', ul_detail.get('bytes')),
        "latencyStatus": latency.get("status"),
        "latencyError": latency.get("error"),
        "pingAvgMs": latency.get("avgMs"),
        "jitterMs": latency.get("jitterMs"),
        "lossPct": latency.get("lossPct"),
        "latencySamplesMs": latency.get("samplesMs"),
        "pingMinMs": latency.get('minMs'),
        "pingMaxMs": latency.get('maxMs'),
        "sent": latency.get('sent'),
        "received": latency.get('received'),
        "failed": latency.get('failed'),
    }


def report_summary(pair_response: dict[str, Any]) -> dict[str, Any]:
    pair_json = pair_response.get("json")
    if not isinstance(pair_json, dict):
        return {"ok": False, "error": pair_response.get("error") or pair_response.get("textPreview")}
    is_ok = bool(pair_response.get("ok") and deploy_vps_pair.pair_report_ok(pair_json))
    return {
        "ok": is_ok,
        "error": None if is_ok else pair_json.get("message") or "至少一个方向的下载、上传或延迟测试失败",
        "mode": pair_json.get("mode"),
        "timestamp": pair_json.get("timestamp"),
        "forward": summarize_direction(pair_json.get("forward", {})),
        "reverse": summarize_direction(pair_json.get("reverse", {})),
    }


def print_health_result(label: str, health: dict[str, Any]) -> None:
    print(f"\n[{label}] {health['publicUrl']}")
    for name, item in health["checks"].items():
        check_name = {
            "rootPage": "原版首页",
            "vpsPage": "双 VPS 页面",
            "agentHealth": "代理健康接口",
        }.get(name, name)
        state = "正常" if item.get("ok") else "失败"
        print(f"  {check_name}：{state}，状态码={item.get('statusCode')}，耗时={item.get('elapsedMs')}毫秒，尝试次数={item.get('attempt')}")
        if name == "agentHealth" and isinstance(item.get("json"), dict):
            node = item['json'].get('node') if isinstance(item['json'].get('node'), dict) else {}
            print(f"    代理版本={item['json'].get('agentVersion')}，节点名称={node.get('name')}，公开地址={node.get('publicUrl')}")
    clock = health.get("clock", {})
    if clock.get("checked"):
        state = "正常" if clock.get("ok") else "警告"
        print(f"  服务器时钟：{state}，相对本地偏差={clock.get('offsetSeconds')} 秒。{clock.get('message')}")


def print_pair_summary(summary: dict[str, Any]) -> None:
    print("\n[双向测速摘要]")
    if not summary.get("ok"):
        print(f"  测试失败：{summary.get('error')}")
        for key, label in (("forward", "固定端 -> 测试端"), ("reverse", "测试端 -> 固定端")):
            direction = summary.get(key, {})
            if direction:
                print(
                    f"  {label}：下载={direction.get('downloadStatus')}，"
                    f"上传={direction.get('uploadStatus')}，延迟={direction.get('latencyStatus')}"
                )
        return
    print("  固定端 -> 测试端")
    print(f"    下载速度：{summary['forward']['downloadMbps']} 兆比特每秒，上传速度：{summary['forward']['uploadMbps']} 兆比特每秒")
    print(f"    平均延迟：{summary['forward']['pingAvgMs']} 毫秒，延迟抖动：{summary['forward']['jitterMs']} 毫秒，HTTP 请求失败率：{summary['forward']['lossPct']}%")
    print("  测试端 -> 固定端")
    print(f"    下载速度：{summary['reverse']['downloadMbps']} 兆比特每秒，上传速度：{summary['reverse']['uploadMbps']} 兆比特每秒")
    print(f"    平均延迟：{summary['reverse']['pingAvgMs']} 毫秒，延迟抖动：{summary['reverse']['jitterMs']} 毫秒，HTTP 请求失败率：{summary['reverse']['lossPct']}%")


def save_interactive_report(report: dict[str, Any]) -> tuple[pathlib.Path, pathlib.Path]:
    REPORT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    json_path = REPORT_DIR / f"interactive_report_{stamp}.json"
    md_path = REPORT_DIR / f"interactive_report_{stamp}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = report.get("pairSummary") or {}
    outcome = report.get("outcome")
    pair_requested = report.get("action") in PAIR_ACTIONS or report.get("pairSummary") is not None
    fixed = report["config"]["fixed"]
    target = report["config"]["target"]
    deployment = report.get("deployment") or {}
    if deployment.get("requested"):
        deployed_roles = []
        for role in deployment.get("deployed", []):
            deployed_roles.append("固定端" if role == "fixed" else "测试端")
        deployed_text = "、".join(deployed_roles) if deployed_roles else "未记录到成功部署的节点"
        deployment_text = f"本次包含远端自动部署，完成的节点为：{deployed_text}。SSH 密码只在本次进程内使用，没有写入报告。"
    else:
        deployment_text = "本次没有执行 SSH 登录和远端部署，所有健康检查和测速都通过本地 HTTP 请求完成。"
    md = [
        "# LibreSpeed 双 VPS 本地交互式执行报告",
        "",
        f"本报告由本地交互式脚本在 {report['generatedAt']} 生成。本次选择的动作是“{report['actionLabel']}”。脚本在本地收集输入，并严格按照所选动作执行后续步骤。{deployment_text}",
        "",
        f"固定端是 `{fixed['host']}:{fixed['web_port']}`，测试端是 `{target['host']}:{target['web_port']}`。本次使用的令牌指纹是 `{report['tokenFingerprint']}`；验证连通后可保存在 `{TOKEN_FILE}`，报告不记录令牌明文。",
        "",
    ]
    if pair_requested:
        md.append(f"本次测速参数为：下载测试大小 {report['config']['downloadMegabytes']} MiB，上传测试大小 {report['config']['uploadMegabytes']} MiB，延迟采样 {report['config']['pingCount']} 次，单项超时时间 {report['config']['timeoutSeconds']} 秒。1 MiB 是 1048576 字节。")
        md.append(traffic_budget(report['config']['downloadMegabytes'], report['config']['uploadMegabytes']))
    else:
        md.append("本次所选动作不包含双向测速，因此没有使用下载大小、上传大小、延迟采样次数和测速超时参数。")
    md.extend([
        "",
        "## 执行结论",
        "",
    ])
    if isinstance(outcome, dict):
        md.append(
            f"本次所选动作的总体结果为“{'全部通过' if outcome.get('ok') else '未全部通过'}”。"
            f"健康检查结果为“{execution_state_text(outcome.get('healthOk'))}”，"
            f"双向测速结果为“{execution_state_text(outcome.get('pairOk'))}”。"
        )
        if report.get('error'):
            md.append(f"流程随后中断：{report['error']}。以上结论仅对应已经完成的检查。")
    elif report.get("error"):
        md.append(f"脚本执行过程中发生异常，异常摘要为：`{report.get('error')}`。即使执行失败，本报告和 JSON 原始记录仍已保存。")
    else:
        md.append("报告生成时流程尚未形成完整结论，请结合下面的健康检查、双向测速和 JSON 原始数据判断。")
    md.extend([
        "",
        "## 健康检查",
        "",
    ])
    if report.get("health"):
        for role, health in report.get("health", {}).items():
            role_name = "固定端" if role == "fixed" else "测试端"
            md.append(f"### {role_name}")
            md.append("")
            md.append(f"{role_name}公开访问地址为 `{health.get('publicUrl')}`。总体检查结果为“{'通过' if health.get('ok') else '未通过'}”。")
            for name, item in health.get("checks", {}).items():
                check_name = {
                    "rootPage": "原版 LibreSpeed 首页",
                    "vpsPage": "双 VPS 测速页面",
                    "agentHealth": "受令牌保护的代理健康接口",
                }.get(name, name)
                md.append(f"- {check_name} 返回状态码 {item.get('statusCode')}，本地请求耗时 {item.get('elapsedMs')} 毫秒，尝试次数 {item.get('attempt')}，结果为“{'正常' if item.get('ok') else '失败'}”。")
                if item.get('error'):
                    md.append(f"  失败原因：{item['error']}。")
                if name == 'agentHealth' and isinstance(item.get('json'), dict):
                    md.append(f"  节点代理版本：{item['json'].get('agentVersion', '未返回')}。")
            clock = health.get("clock", {})
            if clock.get("checked"):
                md.append(f"- 服务器报告时间相对本地时间偏差 {clock.get('offsetSeconds')} 秒，判断为“{'正常' if clock.get('ok') else '警告'}”。{clock.get('message')}")
            md.append("")
    else:
        md.append("没有记录到已完成的健康检查；可能是所选动作不包含该步骤，或流程在此之前已中断。")
        md.append("")

    md.extend([
        "## 双向测速摘要",
        "",
        "| 测试方向 | 下载速度 | 上传速度 | 平均延迟 | 延迟抖动 | HTTP 请求失败率 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ])
    if not pair_requested:
        md.append("| 未执行 | 未执行 | 未执行 | 未执行 | 未执行 | 未执行 |")
        md.append("")
        md.append("本次所选动作没有执行双向测速。这不是测速失败，而是用户选择的动作本来就不包含该步骤。")
    elif not report.get('pairResponse') and not summary.get('forward'):
        md.append('| 未启动 | 未执行 | 未执行 | 未执行 | 未执行 | 未执行 |')
        md.extend(['', summary.get('error') or report.get('error') or '流程尚未进入测速阶段，没有发出测速请求。'])
    elif summary.get("ok"):
        fwd = summary["forward"]
        rev = summary["reverse"]
        md.append(f"| 固定端 -> 测试端 | {fwd.get('downloadMbps')} 兆比特每秒 | {fwd.get('uploadMbps')} 兆比特每秒 | {fwd.get('pingAvgMs')} 毫秒 | {fwd.get('jitterMs')} 毫秒 | {fwd.get('lossPct')}% |")
        md.append(f"| 测试端 -> 固定端 | {rev.get('downloadMbps')} 兆比特每秒 | {rev.get('uploadMbps')} 兆比特每秒 | {rev.get('pingAvgMs')} 毫秒 | {rev.get('jitterMs')} 毫秒 | {rev.get('lossPct')}% |")
        md.append("")
        md.append(f"固定端到测试端方向中，固定端从测试端下载随机数据的速度为 {fwd.get('downloadMbps')} 兆比特每秒，固定端向测试端上传数据的速度为 {fwd.get('uploadMbps')} 兆比特每秒。该方向的平均 HTTP 往返延迟为 {fwd.get('pingAvgMs')} 毫秒，延迟抖动为 {fwd.get('jitterMs')} 毫秒，HTTP 请求失败率为 {fwd.get('lossPct')}%。")
        md.append("")
        md.append(f"测试端到固定端方向中，测试端从固定端下载随机数据的速度为 {rev.get('downloadMbps')} 兆比特每秒，测试端向固定端上传数据的速度为 {rev.get('uploadMbps')} 兆比特每秒。该方向的平均 HTTP 往返延迟为 {rev.get('pingAvgMs')} 毫秒，延迟抖动为 {rev.get('jitterMs')} 毫秒，HTTP 请求失败率为 {rev.get('lossPct')}%。")
    else:
        md.append("| 未全部完成 | 见下方逐项说明 | 见下方逐项说明 | 见下方逐项说明 | 见下方逐项说明 | 见下方逐项说明 |")
        md.append("")
        md.append(f"双向测速没有成功完成。失败信息为：{summary.get('error') or report.get('error') or '未返回明确错误'}。")
        for key, label in (("forward", "固定端到测试端"), ("reverse", "测试端到固定端")):
            direction = summary.get(key, {})
            if not direction:
                continue
            md.append("")
            md.append(
                f"{label}的子项状态为：下载{'完成' if direction.get('downloadStatus') == 'ok' else '未完成'}，"
                f"上传{'完成' if direction.get('uploadStatus') == 'ok' else '未完成'}，"
                f"延迟{'完成' if direction.get('latencyStatus') == 'ok' else '未完成'}。成功子项仍保留在详细记录中。"
            )

    for key, label in (('forward', '固定端发起'), ('reverse', '测试端发起')):
        direction = summary.get(key, {})
        if not direction:
            continue
        direction = {key: ('未返回' if value is None else value) for key, value in direction.items()}
        md.extend(['', f'### {label}的详细记录', ''])
        if direction.get('error') not in (None, '未返回'):
            md.append(f"该方向返回的整体原因是：{direction['error']}。")
        for prefix, name in (('download', '下载'), ('upload', '上传')):
            status_text = '成功' if direction.get(prefix + 'Status') == 'ok' else '未完成'
            md.append(
                f"{name}{status_text}：记录到 {direction.get(prefix + 'Bytes', '未返回')} 字节，"
                f"耗时 {direction.get(prefix + 'Seconds', '未返回')} 秒，"
                f"速度 {direction.get(prefix + 'Mbps', '未返回')} Mbps，"
                f"HTTP 状态 {direction.get(prefix + 'HttpCode', '未返回')}。"
            )
            if direction.get(prefix + 'Error') not in (None, '未返回'):
                md.append(f"{name}未完成的原因：{direction[prefix + 'Error']}。")
            duration = direction.get(prefix + 'Seconds')
            if isinstance(duration, (int, float)) and duration < 1:
                md.append(f'{name}用时不足 1 秒，容易受连接建立和瞬时突发影响；比较线路时应增大数据量并重复测量。')
        md.append(
            f"延迟采样计划发送 {direction.get('sent')} 次，成功 {direction.get('received')} 次，"
            f"失败 {direction.get('failed')} 次；最小 {direction.get('pingMinMs')} 毫秒，"
            f"最大 {direction.get('pingMaxMs')} 毫秒，平均 {direction.get('pingAvgMs')} 毫秒，"
            f"抖动 {direction.get('jitterMs')} 毫秒，请求失败率 {direction.get('lossPct')}%。"
        )
        md.append(f"每次成功采样的 HTTP 往返耗时（毫秒）：{direction.get('latencySamplesMs') or '没有成功样本'}。")
    md.extend(['', '下载是发起端接收数据，上传是发起端发送数据。这里是单连接 HTTP 文件传输速度，包含请求开销；请求失败率来自 HTTP 采样，不能当作 ICMP 丢包率。'])
    if report.get('phase'):
        md.append(f"流程最后到达的阶段：{report['phase']}。")

    md.extend([
        "",
        "## 详细数据",
        "",
        f"完整机器可读数据保存在 `{json_path}`。其中包含两端健康检查的原始 HTTP 状态、每一步耗时、双向测速接口返回的完整 JSON 和脚本整理出的摘要字段。",
        "",
        "## 本地复跑方式",
        "",
        "```powershell",
        f'python "{pathlib.Path(__file__).resolve()}"',
        "```",
    ])
    md_path.write_text("\n".join(md) + "\n", encoding="utf-8")
    return json_path, md_path


def deploy_if_needed(
    action: str,
    fixed: NodeConfig,
    target: NodeConfig,
    token: str,
    ssh_passwords: dict[str, str],
    deployed: list[str],
) -> None:
    if action not in DEPLOY_ACTIONS:
        return

    archive = deploy_vps_pair.make_archive()
    try:
        if action in DEPLOY_FIXED_ACTIONS:
            pause_step("准备自动部署固定端，不需要手工登录 VPS")
            deploy_vps_pair.deploy_node("fixed", fixed.host, fixed.ssh_port, fixed.ssh_user, ssh_passwords["fixed"], fixed.web_port, fixed.url, target.url, token, archive)
            deployed.append("fixed")
        if action in DEPLOY_TARGET_ACTIONS:
            pause_step("准备自动部署测试端，不需要手工登录 VPS")
            deploy_vps_pair.deploy_node("target", target.host, target.ssh_port, target.ssh_user, ssh_passwords["target"], target.web_port, target.url, fixed.url, token, archive)
            deployed.append("target")
    finally:
        try:
            archive.unlink()
        except OSError:
            pass


def run_wizard() -> int:
    configure_console()
    print("LibreSpeed 双 VPS 测速本地交互式向导")
    print("说明：脚本会在本地发起 HTTP/SSH 自动操作；你不需要手工登录两台机器。")

    actions = {
        "1": "只做健康检查，不测速，不部署",
        "2": "健康检查 + 双向测速，不部署",
        "3": "自动部署固定端和测试端 + 健康检查 + 双向测速",
        "4": "只自动部署测试端 + 健康检查 + 双向测速",
        "5": "只自动部署固定端 + 健康检查 + 双向测速",
        "6": "只做双向测速，不做健康检查，不部署",
    }

    saved = load_saved_nodes()
    pause_step("步骤 1：选择执行动作")
    action = prompt_choice("请选择本次要执行的动作", actions, '2' if TOKEN_FILE.exists() else '3')
    if action in DEPLOY_ACTIONS:
        try:
            deploy_vps_pair.load_paramiko()
        except RuntimeError:
            from prepare_runtime import prepare_and_restart
            return prepare_and_restart()
    fixed_saved = saved.get('fixed', {})
    target_saved = saved.get('target', {})

    pause_step("步骤 2：输入固定端信息")
    fixed_host = prompt_host("固定端 IP/域名", fixed_saved.get('host', DEFAULT_FIXED_HOST))
    fixed_port = prompt_int("固定端 Web 端口", fixed_saved.get('web_port', DEFAULT_FIXED_PORT))
    fixed_scheme = prompt_choice('固定端 Web 协议', {'http': 'HTTP', 'https': 'HTTPS（需已有证书和反向代理）'}, fixed_saved.get('scheme', 'http'))

    pause_step("步骤 3：输入测试端信息")
    target_host = prompt_host("测试端 IP/域名", target_saved.get('host', DEFAULT_TARGET_HOST))
    while target_host == fixed_host:
        print('两端必须是不同的 VPS，请重新输入测试端。')
        target_host = prompt_host('测试端 IP/域名', None)
    target_port = prompt_int("测试端 Web 端口", target_saved.get('web_port', DEFAULT_TARGET_PORT))
    target_scheme = prompt_choice('测试端 Web 协议', {'http': 'HTTP', 'https': 'HTTPS（需已有证书和反向代理）'}, target_saved.get('scheme', 'http'))

    fixed_ssh_port = fixed_saved.get('ssh_port', DEFAULT_SSH_PORT)
    fixed_ssh_user = fixed_saved.get('ssh_user', DEFAULT_SSH_USER)
    target_ssh_port = target_saved.get('ssh_port', DEFAULT_SSH_PORT)
    target_ssh_user = target_saved.get('ssh_user', DEFAULT_SSH_USER)
    if action in DEPLOY_ACTIONS:
        pause_step("步骤 4：输入需要部署节点的 SSH 信息")
        if action in DEPLOY_FIXED_ACTIONS:
            fixed_ssh_port = prompt_int("固定端 SSH 端口", fixed_ssh_port)
            fixed_ssh_user = prompt_text("固定端 SSH 用户名（需 root 权限）", fixed_ssh_user)
        if action in DEPLOY_TARGET_ACTIONS:
            target_ssh_port = prompt_int("测试端 SSH 端口", target_ssh_port)
            target_ssh_user = prompt_text("测试端 SSH 用户名（需 root 权限）", target_ssh_user)
    else:
        print("\n本次不部署服务器，因此无需输入 SSH 端口、用户名或密码。")

    fixed = NodeConfig("fixed", fixed_host, fixed_port, fixed_ssh_port, fixed_ssh_user, fixed_scheme)
    target = NodeConfig("target", target_host, target_port, target_ssh_port, target_ssh_user, target_scheme)
    if ((action in DEPLOY_FIXED_ACTIONS and fixed.scheme == 'https')
            or (action in DEPLOY_TARGET_ACTIONS and target.scheme == 'https')):
        raise ValueError('自动部署使用 HTTP 端口。请先部署，再配置 HTTPS 反向代理，日常检查/测速时可以选 HTTPS。')

    pause_step("步骤 5：确认或输入代理访问令牌")
    token = load_or_prompt_token(allow_generate=action == "3")

    download_mb = 64
    upload_mb = 32
    ping_count = 10
    timeout_seconds = 60
    if action in PAIR_ACTIONS:
        pause_step("步骤 6：输入测速参数")
        download_mb = prompt_int("下载测试大小 MiB（1 MiB = 1048576 字节）", download_mb, 1, 1024)
        upload_mb = prompt_int("上传测试大小 MiB", upload_mb, 1, 256)
        ping_count = prompt_int("延迟采样次数", ping_count, 3, 60)
        timeout_seconds = prompt_int("单项超时时间 秒", timeout_seconds, 5, 300)
    else:
        print("\n本次只做健康检查，因此无需输入测速流量和采样参数。")

    ssh_passwords: dict[str, str] = {}
    if action in DEPLOY_ACTIONS:
        pause_step("步骤 7：输入 SSH 密码，脚本只在当前内存中使用")
        if action in DEPLOY_FIXED_ACTIONS:
            ssh_passwords["fixed"] = getpass.getpass("固定端 SSH 密码: ")
        if action in DEPLOY_TARGET_ACTIONS:
            if action == "3" and prompt_yes_no("固定端和测试端的 SSH 密码是否相同", True):
                ssh_passwords["target"] = ssh_passwords["fixed"]
            else:
                ssh_passwords["target"] = getpass.getpass("测试端 SSH 密码: ")

    action_label = actions[action]
    print("\n[执行前确认]")
    print(f"动作：{action_label}")
    print(f"固定端：{fixed.url}")
    print(f"测试端：{target.url}")
    if action in DEPLOY_FIXED_ACTIONS:
        print(f"固定端 SSH：{fixed.ssh_user}@{fixed.host}:{fixed.ssh_port}")
    if action in DEPLOY_TARGET_ACTIONS:
        print(f"测试端 SSH：{target.ssh_user}@{target.host}:{target.ssh_port}")
    if action in PAIR_ACTIONS:
        print(f"测速参数：下载测试 {download_mb} MiB，上传测试 {upload_mb} MiB，延迟采样 {ping_count} 次，单项超时 {timeout_seconds} 秒")
        print(traffic_budget(download_mb, upload_mb))
    else:
        print("双向测速：本次不执行")
    print(f"令牌文件：{TOKEN_FILE}")
    print(f"令牌指纹：{token_fingerprint(token)}")
    remember = prompt_yes_no('是否把两端地址、端口和用户名保存在本地，供下次复用（不包含密码/令牌）', True)
    if not prompt_yes_no("确认开始执行", False):
        print("已取消。")
        return 1

    report: dict[str, Any] = {
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "action": action,
        "actionLabel": action_label,
        "tokenFile": str(TOKEN_FILE),
        "tokenFingerprint": token_fingerprint(token),
        "config": {
            "fixed": asdict(fixed),
            "target": asdict(target),
            "downloadMegabytes": download_mb,
            "uploadMegabytes": upload_mb,
            "pingCount": ping_count,
            "timeoutSeconds": timeout_seconds,
        },
        "deployment": {
            "requested": action in DEPLOY_ACTIONS,
            "deployed": [],
            "remoteLog": "/opt/librespeed-vps/deploy.log",
        },
        "health": {},
        "pairResponse": None,
        "pairSummary": None,
    }

    try:
        if remember:
            save_nodes(fixed, target)
        report['phase'] = '自动部署'
        deploy_if_needed(action, fixed, target, token, ssh_passwords, report["deployment"]["deployed"])
        if report['deployment']['deployed']:
            deploy_vps_pair.save_token(token)

        if action in HEALTH_ACTIONS:
            pause_step("步骤 8：开始健康检查")
            report['phase'] = '健康检查'
            fixed_health = run_health(fixed, token)
            report['health']['fixed'] = fixed_health
            target_health = run_health(target, token)
            report['health']['target'] = target_health
            print_health_result("固定端", fixed_health)
            print_health_result("测试端", target_health)

        health_ok = all(item.get('ok') for item in report['health'].values()) if action in HEALTH_ACTIONS else None
        if any(item['checks']['agentHealth'].get('ok') for item in report['health'].values()):
            deploy_vps_pair.save_token(token)
        if action in PAIR_ACTIONS and health_ok is not False:
            pause_step("步骤 9：开始双向测速")
            report['phase'] = '双向测速'
            pair_response = run_pair_report(fixed, target, token, download_mb, upload_mb, ping_count, timeout_seconds)
            pair_summary = report_summary(pair_response)
            report["pairResponse"] = pair_response
            report["pairSummary"] = pair_summary
            print_pair_summary(pair_summary)
            if pair_summary['ok']:
                deploy_vps_pair.save_token(token)
        elif action in PAIR_ACTIONS:
            report['pairSummary'] = {'ok': False, 'error': '健康检查未通过，本次未启动测速；请先处理上方错误。'}
            print('\n健康检查未通过，已跳过会消耗流量的双向测速。')

        health_ok = all(item.get("ok") for item in report["health"].values()) if action in HEALTH_ACTIONS else None
        pair_ok = bool((report.get("pairSummary") or {}).get("ok")) if report.get('pairResponse') is not None else None
        report["outcome"] = {
            "ok": health_ok is not False and pair_ok is not False,
            "healthOk": health_ok,
            "pairOk": pair_ok,
        }

        pause_step("步骤 10：保存本地报告")
        json_path, md_path = save_interactive_report(report)
        print("\n[报告已保存]")
        print(f"JSON: {json_path}")
        print(f"Markdown: {md_path}")
        if report["outcome"]["ok"]:
            return 0
        print("\n检查或测速没有全部通过，详细失败信息已经写入报告。")
        return 3
    except (Exception, KeyboardInterrupt) as exc:
        if report['deployment']['deployed']:
            deploy_vps_pair.save_token(token)
        report["error"] = deploy_vps_pair.redact(repr(exc), token)
        for password in ssh_passwords.values():
            report['error'] = deploy_vps_pair.redact(report['error'], password)
        json_path, md_path = save_interactive_report(report)
        print(f"\n执行失败：{report['error']}")
        print(f"失败报告 JSON: {json_path}")
        print(f"失败报告 Markdown: {md_path}")
        return 1 if isinstance(exc, (EOFError, KeyboardInterrupt)) else 2


def main() -> int:
    try:
        return run_wizard()
    except (EOFError, KeyboardInterrupt):
        print('\n输入已结束或用户取消，流程已停止。')
        return 1
    except Exception as exc:
        print(f'\n无法开始：{exc}', file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
