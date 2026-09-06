<?php
declare(strict_types=1);

const VPS_AGENT_VERSION = '1.3.0';

function configure_agent_http(): void
{
    header('Access-Control-Allow-Origin: *');
    header('Access-Control-Allow-Methods: GET, POST, OPTIONS');
    header('Access-Control-Allow-Headers: Content-Type, X-LibreSpeed-Token');
    if (($_SERVER['REQUEST_METHOD'] ?? '') === 'OPTIONS') {
        http_response_code(204);
        exit;
    }
}

configure_agent_http();

function json_response(array $payload, int $status = 200): void
{
    http_response_code($status);
    header('Content-Type: application/json; charset=utf-8');
    header('Cache-Control: no-store, no-cache, must-revalidate, max-age=0');
    echo json_encode($payload, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT);
    exit;
}

function request_header(string $name): string
{
    $key = 'HTTP_' . strtoupper(str_replace('-', '_', $name));
    return isset($_SERVER[$key]) ? trim((string) $_SERVER[$key]) : '';
}

function require_agent_token(): void
{
    $expected = trim((string) getenv('LIBRESPEED_VPS_TOKEN'));
    if ($expected === '') {
        json_response([
            'status' => 'error',
            'error' => 'agent_token_not_configured',
            'message' => '当前节点没有配置 LIBRESPEED_VPS_TOKEN，代理接口不可用。',
        ], 503);
    }

    $actual = request_header('X-LibreSpeed-Token');
    if ($actual === '' || !hash_equals($expected, $actual)) {
        json_response([
            'status' => 'error',
            'error' => 'unauthorized',
            'message' => '缺少 X-LibreSpeed-Token 请求头，或代理访问令牌不正确。',
        ], 401);
    }
}

function read_json_body(): array
{
    $raw = file_get_contents('php://input', false, null, 0, 65537);
    if (is_string($raw) && strlen($raw) > 65536) {
        json_response(['status' => 'error', 'error' => 'body_too_large', 'message' => '控制请求不能超过 64 KiB。'], 413);
    }
    if (!is_string($raw) || trim($raw) === '') {
        return [];
    }
    $object = json_decode($raw);
    if (!is_object($object)) {
        json_response([
            'status' => 'error',
            'error' => 'invalid_json',
            'message' => '请求体必须是 JSON 对象。',
        ], 400);
    }
    $data = json_decode($raw, true);
    foreach (['fixed', 'target'] as $key) {
        if (array_key_exists($key, $data) && !is_string($data[$key])) {
            json_response(['status' => 'error', 'error' => 'invalid_target', 'message' => '节点地址必须是字符串。'], 400);
        }
    }
    return $data;
}

function require_post(): void
{
    if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') {
        header('Allow: POST, OPTIONS');
        json_response(['status' => 'error', 'error' => 'method_not_allowed', 'message' => '测速必须使用 POST 请求。'], 405);
    }
}

function acquire_measurement_lock(): void
{
    $path = sys_get_temp_dir() . '/librespeed-vps-measure.lock';
    $handle = fopen($path, 'c');
    if ($handle === false) {
        json_response([
            'status' => 'error',
            'error' => 'lock_open_failed',
            'message' => '无法创建测速任务锁。',
        ], 500);
    }
    if (!flock($handle, LOCK_EX | LOCK_NB)) {
        fclose($handle);
        json_response([
            'status' => 'error',
            'error' => 'measurement_busy',
            'message' => '当前节点已有测速任务正在运行，请稍后再试。',
        ], 409);
    }
    register_shutdown_function(static function () use ($handle): void {
        flock($handle, LOCK_UN);
        fclose($handle);
    });
}

function normalize_base_url(string $url): string
{
    $url = trim($url);
    if ($url === '') {
        json_response([
            'status' => 'error',
            'error' => 'empty_target',
            'message' => '必须提供目标地址。',
        ], 400);
    }
    if (preg_match('/[\x00-\x20\\\\]/', $url)) {
        json_response(['status' => 'error', 'error' => 'invalid_target', 'message' => '地址不能包含空白、控制字符或反斜杠。'], 400);
    }
    if (!str_contains($url, '://')) {
        $url = 'http://' . $url;
    }
    $parts = parse_url($url);
    if (!is_array($parts) || empty($parts['scheme']) || empty($parts['host'])) {
        json_response([
            'status' => 'error',
            'error' => 'invalid_target',
            'message' => '目标地址必须包含主机名或 IP。',
        ], 400);
    }
    if (!in_array(strtolower((string) $parts['scheme']), ['http', 'https'], true)) {
        json_response([
            'status' => 'error',
            'error' => 'unsupported_scheme',
            'message' => '目标地址只支持 HTTP 或 HTTPS。',
        ], 400);
    }
    if (isset($parts['user']) || isset($parts['pass']) || isset($parts['query']) || isset($parts['fragment'])) {
        json_response([
            'status' => 'error',
            'error' => 'unsupported_target_components',
            'message' => '目标地址不能包含账号、密码、查询参数或锚点。',
        ], 400);
    }
    return rtrim($url, '/');
}

function endpoint_url(string $base, string $path): string
{
    return rtrim($base, '/') . '/' . ltrim($path, '/');
}

function bounded_int(array $input, string $key, int $default, int $min, int $max): int
{
    $value = $input[$key] ?? $default;
    if (!is_int($value) || $value < $min || $value > $max) {
        json_response([
            'status' => 'error', 'error' => 'invalid_parameter',
            'message' => $key . ' 必须是 ' . $min . ' 到 ' . $max . ' 之间的整数。',
        ], 400);
    }
    return $value;
}

function run_curl_metric(string $cmd, string $metricPattern): array
{
    $lines = [];
    $exitCode = 0;
    exec($cmd . ' 2>&1', $lines, $exitCode);
    $raw = implode("\n", $lines);
    if (!preg_match($metricPattern, $raw, $matches)) {
        return [
            'ok' => false,
            'exitCode' => $exitCode,
            'raw' => $raw,
        ];
    }
    $httpCode = (int) $matches[3];
    return [
        'ok' => $exitCode === 0 && $httpCode >= 200 && $httpCode < 300,
        'exitCode' => $exitCode,
        'raw' => $raw,
        'seconds' => (float) $matches[1],
        'bytes' => (int) $matches[2],
        'httpCode' => $httpCode,
        'responseBytes' => isset($matches[4]) ? (int) $matches[4] : null,
    ];
}

function mbps(int $bytes, float $seconds): float
{
    if ($seconds <= 0.0) {
        return 0.0;
    }
    return round(($bytes * 8) / $seconds / 1000000, 2);
}

function measure_download(string $targetBase, int $megabytes, int $timeout): array
{
    $ckSize = max(1, min(1024, $megabytes));
    $url = endpoint_url($targetBase, 'backend/garbage.php') . '?cors=true&ckSize=' . $ckSize . '&r=' . rawurlencode((string) microtime(true));
    $format = '__METRICS__:%{time_total}:%{size_download}:%{http_code}';
    $cmd = 'curl --proto =http,https -sS --max-time ' . (int) $timeout . ' -o /dev/null -w ' . escapeshellarg($format) . ' ' . escapeshellarg($url);
    $result = run_curl_metric($cmd, '/__METRICS__:([0-9.]+):([0-9]+):([0-9]+)/');
    $expectedBytes = $ckSize * 1048576;
    if (!($result['ok'] ?? false) || ($result['bytes'] ?? 0) !== $expectedBytes) {
        return [
            'status' => 'error',
            'error' => 'download_failed',
            'detail' => $result,
            'expectedBytes' => $expectedBytes,
            'message' => '下载失败或数据量不符合请求，不能把错误页面当作测速数据。',
            'url' => $url,
        ];
    }
    return [
        'status' => 'ok',
        'url' => $url,
        'bytes' => $result['bytes'],
        'seconds' => round($result['seconds'], 4),
        'mbps' => mbps($result['bytes'], $result['seconds']),
        'httpCode' => $result['httpCode'],
    ];
}

function make_upload_file(int $megabytes): string
{
    $megabytes = max(1, min(256, $megabytes));
    $file = tempnam(sys_get_temp_dir(), 'lsvps-upload-');
    if (!is_string($file)) {
        json_response(['status' => 'error', 'error' => 'tempfile_failed'], 500);
    }
    register_shutdown_function(static function () use ($file): void { @unlink($file); });
    $handle = fopen($file, 'wb');
    if (!$handle) {
        json_response(['status' => 'error', 'error' => 'tempfile_open_failed'], 500);
    }
    $chunk = random_bytes(1048576);
    for ($i = 0; $i < $megabytes; $i++) {
        if (fwrite($handle, $chunk) !== strlen($chunk)) {
            fclose($handle);
            json_response(['status' => 'error', 'error' => 'upload_disk_full', 'message' => '临时上传数据写入失败，请检查可用磁盘空间。'], 500);
        }
    }
    fclose($handle);
    return $file;
}

function measure_upload(string $targetBase, int $megabytes, int $timeout): array
{
    $url = endpoint_url($targetBase, 'backend/empty.php') . '?cors=true&r=' . rawurlencode((string) microtime(true));
    $file = make_upload_file($megabytes);
    $format = '__METRICS__:%{time_total}:%{size_upload}:%{http_code}:%{size_download}';
    $cmd = 'curl --proto =http,https -sS --max-time ' . (int) $timeout . ' -o /dev/null -X POST -H ' . escapeshellarg('Content-Type: application/octet-stream') . ' --data-binary @' . escapeshellarg($file) . ' -w ' . escapeshellarg($format) . ' ' . escapeshellarg($url);
    $result = run_curl_metric($cmd, '/__METRICS__:([0-9.]+):([0-9]+):([0-9]+):([0-9]+)/');
    @unlink($file);
    if (!($result['ok'] ?? false) || ($result['bytes'] ?? 0) !== $megabytes * 1048576 || $result['responseBytes'] !== 0) {
        return [
            'status' => 'error',
            'error' => 'upload_failed',
            'message' => '上传失败、发出数据量不符或目标未返回预期的空响应。',
            'detail' => $result,
            'url' => $url,
        ];
    }
    return [
        'status' => 'ok',
        'url' => $url,
        'bytes' => $result['bytes'],
        'seconds' => round($result['seconds'], 4),
        'mbps' => mbps($result['bytes'], $result['seconds']),
        'httpCode' => $result['httpCode'],
    ];
}

function measure_latency(string $targetBase, int $count, int $timeout): array
{
    $samples = [];
    $failed = 0;
    $urlBase = endpoint_url($targetBase, 'backend/empty.php');
    for ($i = 0; $i < $count; $i++) {
        $url = $urlBase . '?cors=true&r=' . rawurlencode((string) microtime(true)) . '-' . $i;
        $format = '__METRICS__:%{time_total}:%{size_download}:%{http_code}';
        $cmd = 'curl --proto =http,https -sS --max-time ' . (int) $timeout . ' -o /dev/null -w ' . escapeshellarg($format) . ' ' . escapeshellarg($url);
        $result = run_curl_metric($cmd, '/__METRICS__:([0-9.]+):([0-9]+):([0-9]+)/');
        if (!($result['ok'] ?? false) || ($result['bytes'] ?? -1) !== 0) {
            $failed++;
        } else {
            $samples[] = round(((float) $result['seconds']) * 1000, 3);
        }
        usleep(120000);
    }
    if (count($samples) === 0) {
        return [
            'status' => 'error',
            'error' => 'latency_failed',
            'sent' => $count,
            'failed' => $failed,
            'received' => 0,
            'lossPct' => 100.0,
            'samplesMs' => [],
        ];
    }
    $avg = array_sum($samples) / count($samples);
    $jitterValues = [];
    for ($i = 1; $i < count($samples); $i++) {
        $jitterValues[] = abs($samples[$i] - $samples[$i - 1]);
    }
    $jitter = count($jitterValues) ? array_sum($jitterValues) / count($jitterValues) : 0.0;
    return [
        'status' => 'ok',
        'sent' => $count,
        'received' => count($samples),
        'failed' => $failed,
        'lossPct' => round(($failed / $count) * 100, 2),
        'minMs' => round(min($samples), 3),
        'avgMs' => round($avg, 3),
        'maxMs' => round(max($samples), 3),
        'jitterMs' => round($jitter, 3),
        'samplesMs' => $samples,
    ];
}

function measurement_ok(array $result): bool
{
    foreach (['download', 'upload', 'latency'] as $metric) {
        if (!isset($result[$metric]) || !is_array($result[$metric]) || ($result[$metric]['status'] ?? '') !== 'ok') {
            return false;
        }
    }
    return true;
}

function node_name(): string
{
    $name = trim((string) getenv('LIBRESPEED_NODE_NAME'));
    return $name !== '' ? $name : php_uname('n');
}

function public_url(): string
{
    $url = trim((string) getenv('LIBRESPEED_PUBLIC_URL'));
    if ($url !== '') {
        return rtrim($url, '/');
    }
    $host = $_SERVER['HTTP_HOST'] ?? '127.0.0.1';
    $scheme = (!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off') ? 'https' : 'http';
    return $scheme . '://' . $host;
}

function measure_target(string $targetBase, array $input): array
{
    $downloadMb = bounded_int($input, 'downloadMegabytes', 64, 1, 1024);
    $uploadMb = bounded_int($input, 'uploadMegabytes', 32, 1, 256);
    $pingCount = bounded_int($input, 'pingCount', 10, 3, 60);
    $timeout = bounded_int($input, 'timeoutSeconds', 45, 5, 300);

    $download = measure_download($targetBase, $downloadMb, $timeout);
    $upload = measure_upload($targetBase, $uploadMb, $timeout);
    $latency = measure_latency($targetBase, $pingCount, min($timeout, 3));
    $status = (
        ($download['status'] ?? '') === 'ok'
        && ($upload['status'] ?? '') === 'ok'
        && ($latency['status'] ?? '') === 'ok'
    ) ? 'ok' : 'error';

    return [
        'status' => $status,
        'agentVersion' => VPS_AGENT_VERSION,
        'timestamp' => gmdate('c'),
        'node' => [
            'name' => node_name(),
            'publicUrl' => public_url(),
        ],
        'target' => [
            'baseUrl' => $targetBase,
            'downloadUrl' => endpoint_url($targetBase, 'backend/garbage.php'),
            'uploadUrl' => endpoint_url($targetBase, 'backend/empty.php'),
            'pingUrl' => endpoint_url($targetBase, 'backend/empty.php'),
        ],
        'parameters' => [
            'downloadMegabytes' => $downloadMb,
            'uploadMegabytes' => $uploadMb,
            'pingCount' => $pingCount,
            'timeoutSeconds' => $timeout,
        ],
        'download' => $download,
        'upload' => $upload,
        'latency' => $latency,
    ];
}

function redact_response(mixed $value, string $token): mixed
{
    if (is_string($value)) {
        return str_replace($token, '[已隐去令牌]', $value);
    }
    if (is_array($value)) {
        $clean = [];
        foreach ($value as $key => $item) {
            $clean[is_string($key) ? redact_response($key, $token) : $key] = redact_response($item, $token);
        }
        return $clean;
    }
    return $value;
}

function post_json(string $url, array $payload, string $token, int $timeout): array
{
    $body = json_encode($payload, JSON_UNESCAPED_SLASHES);
    $context = stream_context_create([
        'http' => [
            'method' => 'POST',
            'header' => "Content-Type: application/json\r\nX-LibreSpeed-Token: " . $token . "\r\n",
            'content' => $body,
            'timeout' => $timeout,
            'ignore_errors' => true,
            'follow_location' => 0,
        ],
    ]);
    $raw = @file_get_contents($url, false, $context, 0, 2097153);
    if (!is_string($raw)) {
        return [
            'status' => 'error',
            'error' => 'remote_request_failed',
            'url' => $url,
        ];
    }
    $statusLine = $http_response_header[0] ?? '';
    if (!preg_match('#^HTTP/\S+\s+(\d{3})#', $statusLine, $matches)
        || (int) $matches[1] < 200 || (int) $matches[1] >= 300 || strlen($raw) > 2097152) {
        return ['status' => 'error', 'error' => 'remote_http_failed', 'message' => '反向接口未返回成功 HTTP 状态或响应过大；不会跟随跳转传递令牌。'];
    }
    $decoded = json_decode($raw, true);
    if (!is_array($decoded) || !isset($decoded['status'])) {
        return [
            'status' => 'error',
            'error' => 'remote_invalid_json',
            'url' => $url,
            'raw' => redact_response($raw, $token),
        ];
    }
    return redact_response($decoded, $token);
}
