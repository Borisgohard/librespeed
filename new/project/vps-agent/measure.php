<?php
declare(strict_types=1);

require_once __DIR__ . '/common.php';

require_agent_token();
require_post();
acquire_measurement_lock();
$input = read_json_body();
$target = normalize_base_url((string) ($input['target'] ?? (getenv('LIBRESPEED_DEFAULT_TARGET') ?: '')));

json_response(measure_target($target, $input));
