#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/refresh-host.sh --dest ABSOLUTE_DIR SHA

Install SHA into ABSOLUTE_DIR from this git checkout. The script exports that
commit with git archive, writes DEPLOYED_SHA, then runs npm ci, npm run build,
and python3 -m pytest -q. SHA is 7 to 64 hex characters and is resolved to one
full commit. A second run of the same commit prints already installed and does
not rebuild.

--dest is required and absolute. It must not be this checkout, a directory
inside it, or a directory that contains it. An existing dest must be empty
or already this install. The script does not restart the host and does not
call Kalshi.

Options:
  --dest DIR   Absolute install directory. Required.
  --dry-run    Resolve SHA and print the plan. Write nothing.
  -h, --help   Show this text.

Examples:
  scripts/refresh-host.sh --help
  scripts/refresh-host.sh --dry-run --dest /home/box/mcp/kalshi-mcp-main 1ce6d72
  scripts/refresh-host.sh --dest /home/box/mcp/kalshi-mcp-main 1ce6d72
EOF
}

die_usage() {
  echo "error: $1" >&2
  echo "  scripts/refresh-host.sh --dry-run --dest /home/box/mcp/kalshi-mcp-main 1ce6d72" >&2
  exit 2
}

parse_args() {
  DEST=""
  SHA=""
  DRY_RUN=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -h|--help)
        usage
        exit 0
        ;;
      --dry-run)
        DRY_RUN=1
        shift
        ;;
      --dest)
        [[ $# -ge 2 ]] || die_usage "--dest needs an absolute directory"
        DEST=$2
        shift 2
        ;;
      --)
        shift
        break
        ;;
      -*)
        die_usage "unknown option: $1"
        ;;
      *)
        [[ -z $SHA ]] || die_usage "unexpected argument: $1"
        SHA=$1
        shift
        ;;
    esac
  done
  [[ -z ${1:-} ]] || die_usage "unexpected argument: $1"
  [[ -n $SHA ]] || die_usage "missing SHA"
  [[ -n $DEST ]] || die_usage "missing --dest"
  [[ $SHA =~ ^[0-9a-fA-F]{7,64}$ ]] || die_usage "SHA must be 7 to 64 hex characters"
  [[ $DEST == /* ]] || die_usage "--dest must be an absolute path"
}

resolve_commit() {
  local script_dir
  script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
  if ! SOURCE_REAL=$(git -C "$script_dir" rev-parse --show-toplevel); then
    echo "error: $script_dir is not inside a git checkout" >&2
    exit 1
  fi
  SOURCE_REAL=$(realpath "$SOURCE_REAL")
  if ! FULL=$(git -C "$SOURCE_REAL" rev-parse --verify --quiet "${SHA}^{commit}"); then
    echo "error: $SHA is not one commit in $SOURCE_REAL" >&2
    echo "  git -C $SOURCE_REAL fetch origin main" >&2
    exit 1
  fi
  DEST_REAL=$(realpath -m "$DEST")
  local dest_slash source_slash
  dest_slash="${DEST_REAL%/}/"
  source_slash="${SOURCE_REAL%/}/"
  if [[ $dest_slash == "$source_slash" || $dest_slash == "$source_slash"* || $source_slash == "$dest_slash"* ]]; then
    echo "error: --dest must be outside this git checkout ($SOURCE_REAL)" >&2
    echo "  scripts/refresh-host.sh --dry-run --dest /home/box/mcp/kalshi-mcp-main $SHA" >&2
    exit 2
  fi
}

dest_is_install() {
  [[ -f $DEST_REAL/package.json ]] || return 1
  [[ -f $DEST_REAL/server.py || -f $DEST_REAL/kalshi_readonly/__init__.py ]] || return 1
  python3 -c 'import json,sys; name=json.load(open(sys.argv[1], encoding="utf-8")).get("name"); raise SystemExit(0 if name=="kalshi-readonly" else 1)' "$DEST_REAL/package.json"
}

refuse_foreign_dest() {
  [[ -e $DEST_REAL ]] || return 0
  if [[ ! -d $DEST_REAL ]]; then
    echo "error: --dest is not a directory" >&2
    exit 2
  fi
  if [[ -z $(ls -A "$DEST_REAL") ]]; then
    return 0
  fi
  if dest_is_install; then
    return 0
  fi
  echo "error: --dest is not a kalshi-mcp install" >&2
  echo "  scripts/refresh-host.sh --dry-run --dest /home/box/mcp/kalshi-mcp-main $SHA" >&2
  exit 2
}

current_stamp() {
  local raw trimmed size
  CURRENT="unknown"
  if [[ ! -f $DEST_REAL/DEPLOYED_SHA ]]; then
    return 0
  fi
  size=$(wc -c < "$DEST_REAL/DEPLOYED_SHA")
  size=${size//[[:space:]]/}
  if (( size > 128 )); then
    return 0
  fi
  raw=$(<"$DEST_REAL/DEPLOYED_SHA")
  trimmed="${raw#"${raw%%[![:space:]]*}"}"
  trimmed="${trimmed%"${trimmed##*[![:space:]]}"}"
  if [[ $trimmed == "$FULL" ]]; then
    CURRENT=$FULL
  fi
}

print_plan() {
  echo "source: $SOURCE_REAL"
  echo "dest: $DEST_REAL"
  echo "sha: $FULL"
  echo "current: $CURRENT"
  echo "steps: git archive, DEPLOYED_SHA, npm ci --include=dev, npm run build, python3 -m pytest -q"
}

preflight() {
  local cmd
  for cmd in git tar npm python3 flock; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
      echo "error: $cmd is not installed" >&2
      exit 1
    fi
  done
  if ! python3 -m pytest --version >/dev/null 2>&1; then
    echo "error: python3 -m pytest is not installed" >&2
    echo "  python3 -m pip install -r requirements-dev.txt" >&2
    exit 1
  fi
}

publish() {
  local staging previous
  mkdir -p "$(dirname "$DEST_REAL")"
  exec 9>"${DEST_REAL}.refresh.lock"
  if ! flock -n 9; then
    echo "error: another refresh holds ${DEST_REAL}.refresh.lock" >&2
    echo "  scripts/refresh-host.sh --dry-run --dest $DEST $SHA" >&2
    exit 1
  fi
  staging="${DEST_REAL}.staging"
  previous="${DEST_REAL}.previous"
  if [[ ! -e $DEST_REAL && -e $previous ]]; then
    mv "$previous" "$DEST_REAL"
  fi
  rm -rf "$staging"
  mkdir -p "$staging"
  git -C "$SOURCE_REAL" archive --format=tar "$FULL" | tar -x -C "$staging"
  printf '%s\n' "$FULL" > "$staging/DEPLOYED_SHA.tmp"
  mv "$staging/DEPLOYED_SHA.tmp" "$staging/DEPLOYED_SHA"
  (
    cd "$staging"
    npm ci --include=dev
    npm run build
    python3 -m pytest -q
  )
  local discarded
  discarded="${DEST_REAL}.previous.old"
  rm -rf "$discarded"
  if [[ -e $previous ]]; then
    mv "$previous" "$discarded"
  fi
  if [[ -e $DEST_REAL ]]; then
    mv "$DEST_REAL" "$previous"
  fi
  if ! mv "$staging" "$DEST_REAL"; then
    if [[ ! -e $DEST_REAL && -e $previous ]]; then
      mv "$previous" "$DEST_REAL"
    fi
    if [[ ! -e $previous && -e $discarded ]]; then
      mv "$discarded" "$previous"
    fi
    exit 1
  fi
  rm -rf "$discarded"
  echo "deployed: $FULL"
  echo "dest: $DEST_REAL"
  echo "restart: required"
}

main() {
  parse_args "$@"
  resolve_commit
  refuse_foreign_dest
  current_stamp
  print_plan
  if [[ $CURRENT == "$FULL" ]]; then
    echo "already installed: $FULL"
    exit 0
  fi
  if [[ $DRY_RUN -eq 1 ]]; then
    exit 0
  fi
  preflight
  publish
}

main "$@"
