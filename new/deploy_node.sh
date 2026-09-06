#!/usr/bin/env bash
# 由本地 Python 控制器传入参数，负责单节点的候选构建和原容器恢复。
set -Eeuo pipefail
umask 077
PORT="$1"
PUBLIC_URL="$2"
PEER_URL="$3"
TOKEN="$4"
NODE_NAME="$5"
ARCHIVE_NAME="$6"
INSTALL_DIR=/opt/librespeed-vps
CONTAINER=librespeed-vps
PREVIOUS=librespeed-vps-previous
NEW_IMAGE=librespeed-vps:next
[[ "$PORT" =~ ^[0-9]+$ && "$PORT" -ge 1 && "$PORT" -le 65535 ]]
[[ "$ARCHIVE_NAME" =~ ^src(-[0-9a-f]+)?\.tar\.gz$ ]]
[[ "$(id -u)" == 0 ]] || { echo '自动部署必须使用 root 账号。' >&2; exit 1; }
mkdir -p "$INSTALL_DIR"
exec 9>"$INSTALL_DIR/deploy.lock"
flock -n 9 || { echo '当前节点正在部署，请等待完成后再执行。' >&2; exit 1; }
exec > >(tee -a "$INSTALL_DIR/deploy.log") 2>&1
log() { printf '\n[%s] %s\n' "$(date -u '+%FT%TZ')" "$*"; }

work_dir=''
switched=0
committed=0

wait_healthy() {
  local i
  for i in $(seq 1 45); do
    if docker exec "$CONTAINER" php -r '
      $port = getenv("WEBPORT") ?: "80";
      $options = ["http" => ["timeout" => 3, "follow_location" => 0,
        "header" => "X-LibreSpeed-Token: " . getenv("LIBRESPEED_VPS_TOKEN") . "\r\n"]];
      $raw = @file_get_contents("http://127.0.0.1:" . $port . "/vps-agent/health.php", false, stream_context_create($options));
      $data = json_decode($raw ?: "", true);
      exit(is_array($data) && ($data["status"] ?? "") === "ok" && isset($data["agentVersion"]) ? 0 : 1);
    ' >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  return 1
}

finish() {
  local result=$?
  trap - EXIT HUP INT TERM
  if [[ "$committed" == 0 && "$switched" == 1 ]]; then
    log '候选上线失败，恢复原容器及原来的端口、令牌、环境变量和挂载配置'
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
    if docker container inspect "$PREVIOUS" >/dev/null 2>&1; then
      if docker rename "$PREVIOUS" "$CONTAINER" && docker start "$CONTAINER" && wait_healthy; then
        log '原容器已恢复；本次部署仍记为失败'
      else
        log '原容器恢复未完成，请查看 deploy.log 和容器状态'
      fi
    fi
    result=1
  fi
  if [[ -n "$work_dir" && "$work_dir" == "$INSTALL_DIR"/build.* ]]; then
    rm -rf -- "$work_dir"
  fi
  rm -f -- "$INSTALL_DIR/$ARCHIVE_NAME"
  exit "$result"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

log "开始部署 $NODE_NAME，Web 端口 $PORT"
if ! command -v docker >/dev/null 2>&1; then
  command -v apt-get >/dev/null 2>&1 || { log '自动安装目前支持 Debian/Ubuntu'; exit 1; }
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y ca-certificates curl docker.io
fi
systemctl enable --now docker
docker info >/dev/null
if docker container inspect "$PREVIOUS" >/dev/null 2>&1; then
  log '检测到上次未完成恢复的原容器，请先处理 librespeed-vps-previous，避免覆盖恢复现场'
  exit 1
fi
work_dir=$(mktemp -d "$INSTALL_DIR/build.XXXXXX")
tar -xzf "$INSTALL_DIR/$ARCHIVE_NAME" -C "$work_dir"
log '开始构建候选镜像，当前服务继续运行'
docker build -t "$NEW_IMAGE" "$work_dir"
if docker container inspect "$CONTAINER" >/dev/null 2>&1; then
  current_image=$(docker inspect -f '{{.Image}}' "$CONTAINER")
  docker image tag "$current_image" librespeed-vps:rollback
  docker rename "$CONTAINER" "$PREVIOUS"
  switched=1
  docker stop "$PREVIOUS"
else
  switched=1
fi
docker run -d --name "$CONTAINER" --restart unless-stopped \
  -p "$PORT:8080" -e MODE=standalone -e WEBPORT=8080 -e USE_NEW_DESIGN=true \
  -e "TITLE=LibreSpeed 双 VPS 测速 $NODE_NAME" \
  -e 'TAGLINE=保留浏览器测速，并增加服务器到服务器双向测速' \
  -e "LIBRESPEED_VPS_TOKEN=$TOKEN" -e "LIBRESPEED_NODE_NAME=$NODE_NAME" \
  -e "LIBRESPEED_PUBLIC_URL=$PUBLIC_URL" -e "LIBRESPEED_DEFAULT_TARGET=$PEER_URL" "$NEW_IMAGE"
if ! wait_healthy; then
  log '候选容器健康检查失败'
  docker logs --tail 80 "$CONTAINER" || true
  exit 1
fi
docker image tag "$NEW_IMAGE" librespeed-vps:local
committed=1
docker rm "$PREVIOUS" >/dev/null 2>&1 || true
docker image rm "$NEW_IMAGE" >/dev/null 2>&1 || true
log "部署完成：$PUBLIC_URL；本地向导将继续检查公网访问和两端互通"
