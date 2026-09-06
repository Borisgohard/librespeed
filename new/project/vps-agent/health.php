<?php
declare(strict_types=1);

require_once __DIR__ . '/common.php';

require_agent_token();

json_response([
    'status' => 'ok',
    'agentVersion' => VPS_AGENT_VERSION,
    'node' => [
        'name' => node_name(),
        'publicUrl' => public_url(),
    ],
    'originalLibreSpeed' => [
        'download' => endpoint_url(public_url(), 'backend/garbage.php'),
        'upload' => endpoint_url(public_url(), 'backend/empty.php'),
        'ping' => endpoint_url(public_url(), 'backend/empty.php'),
        'getIp' => endpoint_url(public_url(), 'backend/getIP.php'),
    ],
    'time' => gmdate('c'),
]);
