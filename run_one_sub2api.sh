#!/usr/bin/env sh
# 从 sub2api 选择一个账号，并把其余参数交给糖果题评测器。
set -eu

usage() {
    cat <<'EOF'
用法:
  ./run_one_sub2api.sh ACCOUNT [sub2api_eval.py 参数...]
  ./run_one_sub2api.sh --account ACCOUNT [sub2api_eval.py 参数...]

ACCOUNT 按账号名称不区分大小写匹配。默认只使用一个 worker；可在后续参数中用
--workers/-j 覆盖。管理员凭据默认从仓库根目录 .env 读取，也可设置
SUB2API_ENV_FILE 指定其他文件。
EOF
}

if [ "$#" -eq 0 ]; then
    usage >&2
    exit 2
fi

case "$1" in
    -h|--help)
        usage
        exit 0
        ;;
    --account)
        if [ "$#" -lt 2 ] || [ -z "$2" ]; then
            printf '%s\n' 'Missing account name after --account.' >&2
            usage >&2
            exit 2
        fi
        account=$2
        shift 2
        ;;
    --account=*)
        account=${1#*=}
        if [ -z "$account" ]; then
            printf '%s\n' 'Account name must not be empty.' >&2
            usage >&2
            exit 2
        fi
        shift
        ;;
    -*)
        printf '%s\n' 'An account name is required as the first argument.' >&2
        usage >&2
        exit 2
        ;;
    *)
        account=$1
        shift
        ;;
esac

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
        printf '%s\n' 'Python 3 is required but was not found in PATH.' >&2
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

# 默认单 worker；显式的 --workers/-j 参数在后面时会覆盖默认值。
# 将 --account 放在最后，防止透传参数意外改测另一个账号。
set -- --workers 1 "$@" --account "$account"

# 无论从哪个目录启动，都把相对路径固定到仓库根目录。
cd -- "$repo_dir"
exec "$python_bin" "$repo_dir/sub2api_eval.py" "$@"
