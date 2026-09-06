#!/usr/bin/env bash
set -Eeuo pipefail

# LibreSpeed 官方 standalone 模式一键部署脚本。
# 用法：
#   PORT=8080 TITLE="LibreSpeed" USE_NEW_DESIGN=true bash deploy_librespeed.sh

PORT="${PORT:-8080}"
WEBPORT="${WEBPORT:-8080}"
TITLE="${TITLE:-LibreSpeed}"
TAGLINE="${TAGLINE:-LibreSpeed 官方测速服务}"
USE_NEW_DESIGN="${USE_NEW_DESIGN:-true}"
TELEMETRY="${TELEMETRY:-false}"
CONTAINER_NAME="${CONTAINER_NAME:-librespeed}"
IMAGE="${IMAGE:-ghcr.io/librespeed/speedtest@sha256:c2d5da9b11ffaf6fab535e4b84405878e67a714d650d292319369278834eb1c8}"
INSTALL_DIR="${INSTALL_DIR:-/opt/librespeed}"
LOG_FILE="${LOG_FILE:-$INSTALL_DIR/deploy.log}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "错误：请使用 root 权限运行本脚本。" >&2
  exit 1
fi

mkdir -p "$INSTALL_DIR"
touch "$LOG_FILE"
exec > >(tee -a "$LOG_FILE") 2>&1

log() {
  printf '\n[%s] %s\n' "$(date '+%F %T %z')" "$*"
}

retry() {
  local max="${1:-3}"
  local delay="${2:-5}"
  shift 2
  local i=1
  until "$@"; do
    if (( i >= max )); then
      return 1
    fi
    log "命令执行失败，${delay} 秒后重试（第 $i/$max 次）：$*"
    sleep "$delay"
    i=$((i + 1))
  done
}

setup_proxy_hint() {
  cat <<'HINT'
网络提示：
  如果脚本和代理运行在同一台机器，可以临时使用 127.0.0.1:42343：
    export http_proxy=http://127.0.0.1:42343
    export https_proxy=http://127.0.0.1:42343
  如果代理只运行在你的本地电脑，VPS 上的 127.0.0.1 指向 VPS 自己，不能直接使用；
  必须先建立受保护的代理隧道，或改用 VPS 能访问的代理地址。
HINT
}

install_docker_debian() {
  log "通过 apt 安装 Docker。"
  export DEBIAN_FRONTEND=noninteractive
  retry 3 5 apt-get update
  retry 3 5 apt-get install -y ca-certificates curl gnupg docker.io
  apt-get install -y docker-compose-plugin docker-compose 2>/dev/null || true
  systemctl enable --now docker
}

install_docker_rhel() {
  log "通过 yum/dnf 安装 Docker。"
  local pkg_mgr="yum"
  if command -v dnf >/dev/null 2>&1; then
    pkg_mgr="dnf"
  fi
  retry 3 5 "$pkg_mgr" install -y ca-certificates curl docker
  "$pkg_mgr" install -y docker-compose-plugin docker-compose 2>/dev/null || true
  systemctl enable --now docker
}

ensure_docker() {
  if command -v docker >/dev/null 2>&1; then
    log "检测到 Docker 已安装：$(docker --version)"
  else
    setup_proxy_hint
    if command -v apt-get >/dev/null 2>&1; then
      install_docker_debian
    elif command -v yum >/dev/null 2>&1 || command -v dnf >/dev/null 2>&1; then
      install_docker_rhel
    else
      log "没有检测到受支持的包管理器，改用 get.docker.com 安装方式。"
      retry 3 5 sh -c 'curl -fsSL https://get.docker.com | sh'
      systemctl enable --now docker
    fi
  fi

  if ! docker compose version >/dev/null 2>&1 && ! command -v docker-compose >/dev/null 2>&1; then
    log "没有检测到 Docker Compose，尝试通过系统包管理器安装。"
    if command -v apt-get >/dev/null 2>&1; then
      retry 3 5 apt-get update
      apt-get install -y docker-compose-plugin docker-compose 2>/dev/null || true
    elif command -v yum >/dev/null 2>&1 || command -v dnf >/dev/null 2>&1; then
      local pkg_mgr="yum"
      if command -v dnf >/dev/null 2>&1; then
        pkg_mgr="dnf"
      fi
      "$pkg_mgr" install -y docker-compose-plugin docker-compose 2>/dev/null || true
    fi
  fi

  if docker compose version >/dev/null 2>&1; then
    docker compose version
  elif command -v docker-compose >/dev/null 2>&1; then
    docker-compose version
  else
    log "Docker Compose 不可用，本次部署将使用 docker run 兜底启动。"
  fi
}

open_firewall_port() {
  if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -qi active; then
    log "通过 ufw 放行 TCP $PORT。"
    ufw allow "$PORT/tcp" || true
  fi

  if command -v firewall-cmd >/dev/null 2>&1 && systemctl is-active --quiet firewalld; then
    log "通过 firewalld 放行 TCP $PORT。"
    firewall-cmd --permanent --add-port="$PORT/tcp" || true
    firewall-cmd --reload || true
  fi
}

write_compose() {
  log "写入 Docker Compose 文件：$INSTALL_DIR/docker-compose.yml。"
  cat > "$INSTALL_DIR/docker-compose.yml" <<EOF_COMPOSE
services:
  speedtest:
    image: ${IMAGE}
    container_name: ${CONTAINER_NAME}
    restart: unless-stopped
    environment:
      MODE: standalone
      WEBPORT: "${WEBPORT}"
      TITLE: "${TITLE}"
      TAGLINE: "${TAGLINE}"
      TELEMETRY: "${TELEMETRY}"
      USE_NEW_DESIGN: "${USE_NEW_DESIGN}"
    ports:
      - "${PORT}:${WEBPORT}"
EOF_COMPOSE
}

start_service() {
  log "拉取镜像：$IMAGE"
  retry 3 10 docker pull "$IMAGE"
  log "启动 LibreSpeed 容器。"
  if docker compose version >/dev/null 2>&1; then
    docker compose -f "$INSTALL_DIR/docker-compose.yml" up -d --remove-orphans
  elif command -v docker-compose >/dev/null 2>&1; then
    docker-compose -f "$INSTALL_DIR/docker-compose.yml" up -d --remove-orphans
  else
    docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
    docker run -d \
      --name "$CONTAINER_NAME" \
      --restart unless-stopped \
      -e MODE=standalone \
      -e WEBPORT="$WEBPORT" \
      -e TITLE="$TITLE" \
      -e TAGLINE="$TAGLINE" \
      -e TELEMETRY="$TELEMETRY" \
      -e USE_NEW_DESIGN="$USE_NEW_DESIGN" \
      -p "${PORT}:${WEBPORT}" \
      "$IMAGE"
  fi
}

health_check() {
  log "开始健康检查：http://127.0.0.1:${PORT}/"
  local ok=0
  for _ in $(seq 1 30); do
    if curl -fsS --max-time 3 "http://127.0.0.1:${PORT}/" >/dev/null; then
      ok=1
      break
    fi
    sleep 2
  done

  if [[ "$ok" != "1" ]]; then
    log "健康检查失败。下面是最近的容器日志："
    docker logs --tail 120 "$CONTAINER_NAME" || true
    exit 1
  fi
}

main() {
  log "开始部署 LibreSpeed。"
  log "端口=$PORT 容器=$CONTAINER_NAME 镜像=$IMAGE 安装目录=$INSTALL_DIR"
  ensure_docker
  open_firewall_port
  write_compose
  start_service
  health_check
  log "LibreSpeed 部署完成。"
  cat <<EOF_DONE

使用提示：
  访问地址：http://$(curl -fsS --max-time 3 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}'):${PORT}/
  本机检查：curl -I http://127.0.0.1:${PORT}/
  服务日志：docker logs -f ${CONTAINER_NAME}
  部署日志：${LOG_FILE}
EOF_DONE
}

main "$@"
