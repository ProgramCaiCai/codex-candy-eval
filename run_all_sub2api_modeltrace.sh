#!/usr/bin/env sh
# 对 sub2api 中所有匹配账号运行 ModelTrace；参数透传给 Python 入口。
set -eu

repo_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
env_file="${SUB2API_ENV_FILE:-$repo_dir/.env}"
python_bin="${PYTHON_BIN:-python3}"

if [ ! -f "$repo_dir/sub2api_modeltrace_eval.py" ]; then
    printf 'sub2api_modeltrace_eval.py not found under: %s\n' "$repo_dir" >&2
    exit 2
fi
if ! command -v "$python_bin" >/dev/null 2>&1 && [ ! -x "$python_bin" ]; then
    printf 'Python executable is not available: %s\n' "$python_bin" >&2
    exit 127
fi
if [ -f "$env_file" ]; then
    set -- --env-file "$env_file" "$@"
elif [ -n "${SUB2API_ENV_FILE-}" ]; then
    printf 'Environment file not found: %s\n' "$env_file" >&2
    exit 2
fi

cd -- "$repo_dir"
exec "$python_bin" "$repo_dir/sub2api_modeltrace_eval.py" "$@"
