#!/usr/bin/env sh
# 对名称筛选后唯一的 sub2api 账号运行 ModelTrace。
set -eu

usage() {
    printf '%s\n' '用法: ./run_one_sub2api_modeltrace.sh ACCOUNT [其他参数...]'
}

if [ "$#" -eq 0 ]; then
    usage >&2
    exit 2
fi
case "$1" in
    -h|--help) usage; exit 0 ;;
    -*) printf '%s\n' '首个参数必须是账号名称。' >&2; exit 2 ;;
esac
account=$1
shift

repo_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
env_file="${SUB2API_ENV_FILE:-$repo_dir/.env}"
python_bin="${PYTHON_BIN:-python3}"

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
exec "$python_bin" "$repo_dir/sub2api_modeltrace_eval.py" \
    --workers 1 "$@" --account "$account" --single-account
