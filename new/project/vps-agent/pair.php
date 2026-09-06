<?php
declare(strict_types=1);

require_once __DIR__ . '/common.php';

require_agent_token();
require_post();
acquire_measurement_lock();
$input = read_json_body();
$token = request_header('X-LibreSpeed-Token');
$fixedBase = normalize_base_url((string) ($input['fixed'] ?? public_url()));
$targetBase = normalize_base_url((string) ($input['target'] ?? getenv('LIBRESPEED_DEFAULT_TARGET') ?: ''));
if ($fixedBase !== normalize_base_url(public_url()) || $fixedBase === $targetBase) {
    json_response(['status' => 'error', 'error' => 'invalid_pair', 'message' => '固定端必须是接收此请求的当前节点，测试端必须是另一个节点。'], 400);
}
$timeout = bounded_int($input, 'timeoutSeconds', 45, 5, 300);
$pingCount = bounded_int($input, 'pingCount', 10, 3, 60);
$directionTimeout = ($timeout * 2) + ($pingCount * min($timeout, 3)) + 30;

$forward = measure_target($targetBase, $input);
$reversePayload = $input;
$reversePayload['target'] = $fixedBase;
$reverse = post_json(
    endpoint_url($targetBase, 'vps-agent/measure.php'),
    $reversePayload,
    $token,
    $directionTimeout
);
$pairOk = measurement_ok($forward) && measurement_ok($reverse);

json_response([
    'status' => $pairOk ? 'ok' : 'error',
    'message' => $pairOk ? '双向测速全部完成。' : '至少一个方向的下载、上传或延迟测试失败。',
    'agentVersion' => VPS_AGENT_VERSION,
    'timestamp' => gmdate('c'),
    'mode' => 'bidirectional-vps-pair',
    'fixed' => [
        'baseUrl' => $fixedBase,
        'node' => $forward['node'] ?? null,
    ],
    'target' => [
        'baseUrl' => $targetBase,
        'node' => $reverse['node'] ?? null,
    ],
    'forward' => [
        'name' => 'fixed_to_target',
        'from' => $fixedBase,
        'to' => $targetBase,
        'result' => $forward,
    ],
    'reverse' => [
        'name' => 'target_to_fixed',
        'from' => $targetBase,
        'to' => $fixedBase,
        'result' => $reverse,
    ],
    'notes' => [
        'download' => '下载速度通过源 VPS 请求目标 VPS 的 backend/garbage.php 计算。',
        'upload' => '上传速度通过源 VPS 向目标 VPS 的 backend/empty.php 提交临时数据计算。',
        'latency' => '延迟和抖动通过多次 HTTP 空响应请求计算，思路与原版 LibreSpeed 保持一致。',
    ],
]);
