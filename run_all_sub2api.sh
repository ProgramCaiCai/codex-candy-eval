#!/usr/bin/env sh
# 从仓库根目录加载凭据，并把 CLI 参数交给 sub2api_eval.py。
set -eu

repo_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
env_file="${SUB2API_ENV_FILE:-$repo_dir/.env}"

if [ ! -f "$repo_dir/sub2api_eval.py" ]; then
    printf 'sub2api_eval.py not found under: %s\n' "$repo_dir" >&2
    exit 2
fi

python_bin="${PYTHON_BIN-}"
if [ -z "$python_bin" ]; then
    if command -v python3 >/dev/null 2>&1; then
        python_bin="$(command -v python3)"
    elif command -v python >/dev/null 2>&1; then
        python_bin="$(command -v python)"
    else
        printf 'Python 3 is required but was not found in PATH.\n' >&2
        exit 127
    fi
elif ! command -v "$python_bin" >/dev/null 2>&1 && [ ! -x "$python_bin" ]; then
    printf 'Configured Python executable is not available: %s\n' "$python_bin" >&2
    exit 127
fi

if [ -f "$env_file" ]; then
    set -- --env-file "$env_file" "$@"
elif [ -n "${SUB2API_ENV_FILE-}" ]; then
    printf 'Environment file not found: %s\n' "$env_file" >&2
    exit 2
fi

# 默认并发为 10；后面的显式 --workers/-j 参数会覆盖该值。
set -- --workers 20 "$@"
# 无论从哪个目录启动，都把相对路径固定到仓库根目录。
cd -- "$repo_dir"
exec "$python_bin" "$repo_dir/sub2api_eval.py" "$@"
