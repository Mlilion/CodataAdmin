#!/bin/bash
set -euo pipefail

# ==================== 配置 ====================
PROJECT_NAME="CodataAdmin"
WORK_DIR="$(cd "$(dirname "$0")" && pwd)"
DOCKER_DIR="${WORK_DIR}/docker"
ENV_FILE="${DOCKER_DIR}/.env"
ENV_EXAMPLE_FILE="${DOCKER_DIR}/.env.example"
COMPOSE_FILE="${DOCKER_DIR}/docker-compose-example.yaml"
BACKEND_ENV_DIR="${WORK_DIR}/backend/env"
BACKEND_ENV_FILE="${BACKEND_ENV_DIR}/.env.${DEPLOY_ENV:-prod}"
BACKEND_ENV_EXAMPLE="${BACKEND_ENV_FILE}.example"
FRONTEND_DIST_FILE="${WORK_DIR}/docker/nginx/web/dist/index.html"
DEFAULT_SECRET_KEY='vgb0tnl9d58+6n-6h-ea&u^1#s0ccp!794=krylxcjq75vzps$'

COLOR_GREEN='\033[0;32m'; COLOR_BLUE='\033[0;34m'; COLOR_YELLOW='\033[0;33m'; COLOR_RED='\033[0;31m'; COLOR_RESET='\033[0m'

log() {
    local color; local level=${2:-INFO}
    case $level in
        INFO) color="${COLOR_BLUE}" ;; WARN) color="${COLOR_YELLOW}" ;;
        ERROR) color="${COLOR_RED}" ;; SUCCESS) color="${COLOR_GREEN}" ;;
        *) color="${COLOR_RESET}" ;;
    esac
    echo -e "${color}[$(date '+%Y-%m-%d %H:%M:%S')] [${level}] ${1}${COLOR_RESET}"
}

compose() {
    if docker compose version &>/dev/null; then
        docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" "$@"
    else
        docker-compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" "$@"
    fi
}

# ==================== 核心函数 ====================

load_env() {
    if [ -f "${ENV_FILE}" ]; then
        set -a; source "${ENV_FILE}"; set +a
    else
        if [ -f "${ENV_EXAMPLE_FILE}" ]; then
            cp "${ENV_EXAMPLE_FILE}" "${ENV_FILE}"
            log "已从 ${ENV_EXAMPLE_FILE} 生成 ${ENV_FILE}" "WARN"
        fi
        log "请先编辑 ${ENV_FILE}，替换所有生产密码和端口配置后再部署" "ERROR"
        exit 1
    fi
    BACKEND_ENV_FILE="${BACKEND_ENV_DIR}/.env.${DEPLOY_ENV:-prod}"
    BACKEND_ENV_EXAMPLE="${BACKEND_ENV_FILE}.example"
    log "✅ 环境变量已加载" "SUCCESS"
}

check_deps() {
    for dir in \
        "${DOCKER_DIR}/mysql/data" \
        "${DOCKER_DIR}/redis/data" \
        "${DOCKER_DIR}/postgres/data" \
        "${DOCKER_DIR}/nginx/web"; do
        [ -d "$dir" ] || mkdir -p "$dir"
    done
    local missing=()
    for cmd in docker; do
        command -v "$cmd" &>/dev/null || missing+=("$cmd")
    done
    if ! docker compose version &>/dev/null && ! docker-compose --version &>/dev/null; then
        missing+=("docker compose")
    fi
    if [ ${#missing[@]} -gt 0 ]; then
        log "缺少依赖: ${missing[*]}" "ERROR"; exit 1
    fi
    log "✅ 依赖检查通过" "SUCCESS"
}

log_local_revision() {
    cd "${WORK_DIR}"
    if command -v git &>/dev/null && git rev-parse --is-inside-work-tree &>/dev/null; then
        local branch commit dirty
        branch=$(git branch --show-current)
        commit=$(git rev-parse --short HEAD)
        if [ -n "${branch}" ]; then
            log "使用本地代码: ${branch}@${commit}"
        else
            log "使用本地代码: ${commit}（detached HEAD）" "WARN"
        fi
        dirty=$(git status --porcelain 2>/dev/null || true)
        if [ -n "${dirty}" ]; then
            log "当前工作区存在未提交修改，部署将使用当前文件内容" "WARN"
        fi
    else
        log "当前目录不是 Git 工作区，部署将使用当前本地文件" "WARN"
    fi
}

env_file_value() {
    local file=$1
    local key=$2
    local line
    line=$(grep -E "^[[:space:]]*${key}[[:space:]]*=" "${file}" | tail -n 1 || true)
    if [ -z "${line}" ]; then
        return 0
    fi
    local value="${line#*=}"
    value="${value%%#*}"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    value="${value%\"}"
    value="${value#\"}"
    value="${value%\'}"
    value="${value#\'}"
    printf '%s' "${value}"
}

check_prod_env_file() {
    if [ ! -f "${BACKEND_ENV_FILE}" ]; then
        if [ -f "${BACKEND_ENV_EXAMPLE}" ]; then
            cp "${BACKEND_ENV_EXAMPLE}" "${BACKEND_ENV_FILE}"
            log "已从 ${BACKEND_ENV_EXAMPLE} 生成 ${BACKEND_ENV_FILE}" "WARN"
        fi
        log "请先编辑 ${BACKEND_ENV_FILE}，配置后端生产环境变量后再部署" "ERROR"
        exit 1
    fi

    if [ "${DEPLOY_ENV:-prod}" != "prod" ]; then
        return
    fi

    local secret_key
    secret_key=$(env_file_value "${BACKEND_ENV_FILE}" "SECRET_KEY")
    if [ -z "${secret_key}" ]; then
        log "生产环境必须在 ${BACKEND_ENV_FILE} 中显式配置 SECRET_KEY" "ERROR"
        exit 1
    fi
    if [[ "${secret_key}" == *"CHANGE_ME"* || "${secret_key}" == *"your_"* || "${secret_key}" == "${DEFAULT_SECRET_KEY}" ]]; then
        log "生产环境 SECRET_KEY 仍是默认值或占位符，请替换为强随机串" "ERROR"
        exit 1
    fi

}

check_docker_env_placeholders() {
    if [ "${DEPLOY_ENV:-prod}" != "prod" ]; then
        return
    fi

    local bad_lines
    bad_lines=$(grep -nE '^[[:space:]]*[A-Z0-9_]+=' "${ENV_FILE}" \
        | grep -E 'CHANGE_ME|your_|fastapiadmin_root|fastapiadmin123|fastapiadmin_redis|semantic_pg_password' \
        || true)
    if [ -n "${bad_lines}" ]; then
        log "生产环境 docker/.env 仍包含示例密码或占位符:" "ERROR"
        echo "${bad_lines}" | sed 's/^/    /'
        exit 1
    fi
}

validate_compose_config() {
    cd "${DOCKER_DIR}"
    compose config >/dev/null || { log "Docker Compose 配置校验失败" "ERROR"; exit 1; }
    log "✅ Compose 配置校验通过" "SUCCESS"
}

check_frontend_dist() {
    if [ ! -f "${FRONTEND_DIST_FILE}" ]; then
        log "Web 构建产物缺失: ${FRONTEND_DIST_FILE}，请先执行 $0 build-web" "ERROR"
        exit 1
    fi
}

build_image() {
    cd "${DOCKER_DIR}"
    export DOCKER_BUILDKIT=1
    compose build || { log "镜像构建失败" "ERROR"; exit 1; }
    log "✅ 镜像构建完成" "SUCCESS"
}

build_frontend() {
    cd "${DOCKER_DIR}"
    compose run --rm frontend-build || { log "Web 管理台构建失败" "ERROR"; exit 1; }
    check_frontend_dist
    log "✅ Web 管理台构建完成" "SUCCESS"
}

start_service() {
    cd "${DOCKER_DIR}"
    compose up -d --force-recreate || { log "容器启动失败" "ERROR"; exit 1; }
    log "⏳ 等待服务就绪..."
    for i in $(seq 1 30); do
        if compose ps mysql --format '{{.Status}}' 2>/dev/null | grep -q "healthy"; then
            log "✅ MySQL 已就绪" "SUCCESS"; break
        fi
        sleep 2
    done
    compose ps
    log "✅ 服务启动完成" "SUCCESS"
}

stop_service() {
    cd "${DOCKER_DIR}" 2>/dev/null || true
    compose down 2>/dev/null || true
    log "✅ 服务已停止" "SUCCESS"
}

show_logs() {
    cd "${DOCKER_DIR}"
    compose ps --format "table {{.Service}}\t{{.Name}}\t{{.Status}}\t{{.Ports}}"
    echo "--- 最近 50 行日志 ---"
    compose logs --tail=50 2>/dev/null
}

verify() {
    local ok=true
    local services=(mysql redis backend nginx)
    if compose config --services 2>/dev/null | grep -qx "semantic-pg"; then
        services=(mysql redis semantic-pg backend nginx)
    fi
    for svc in "${services[@]}"; do
        local st=$(compose ps "$svc" --format '{{.Status}}' 2>/dev/null || echo "not found")
        if echo "$st" | grep -qE "Up|healthy"; then
            log "✅ $svc: $st" "SUCCESS"
        else
            log "❌ $svc: $st" "ERROR"; ok=false
        fi
    done
    $ok && log "✅ 所有服务正常" "SUCCESS" || log "⚠️ 部分服务异常" "WARN"
}

cleanup() {
    docker system prune -a -f >/dev/null 2>&1 || true
    log "✅ 所有资源已清理" "SUCCESS"
}

preflight() {
    check_prod_env_file
    check_docker_env_placeholders
    validate_compose_config
    log "✅ 部署预检通过" "SUCCESS"
}

# ==================== 主流程 ====================

full_deploy() {
    log "========== 🚀 开始完整部署 ==========" "INFO"
    log "时间: $(date '+%Y-%m-%d %H:%M:%S')"
    load_env
    check_deps
    log_local_revision
    check_prod_env_file
    check_docker_env_placeholders
    validate_compose_config
    build_frontend
    build_image
    stop_service
    start_service
    verify
    show_logs
    cleanup
    log "========== 🎉 部署完成 ==========" "SUCCESS"
}

# ==================== 入口 ====================

trap 'stop_service; exit 130' INT TERM

case ${1:-} in
    start)   load_env; check_deps; preflight; check_frontend_dist; start_service ;;
    stop)    load_env; stop_service ;;
    restart) load_env; compose restart; compose ps ;;
    logs)    load_env; show_logs ;;
    verify)  load_env; verify ;;
    preflight) load_env; check_deps; preflight ;;
    build-web) load_env; check_deps; build_frontend ;;
    clean)   cleanup ;;
    help|-h|--help)
        echo "用法: $0 [命令]"
        echo "  无参数    完整部署（使用本地代码→预检→构建 Web→构建镜像→启动→清理）"
        echo "  start     启动服务"
        echo "  stop      停止服务"
        echo "  restart   重启服务"
        echo "  logs      查看日志"
        echo "  verify    验证服务"
        echo "  preflight 预检生产环境配置"
        echo "  build-web 构建 Web 管理台到 docker/nginx/web/dist"
        echo "  clean     清理构建缓存"
        ;;
    *) full_deploy "$@" ;;
esac
