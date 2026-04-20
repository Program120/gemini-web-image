#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  stop-headless-browser.sh --pid <pid> --temp-profile-root <path>
EOF
}

pid=""
temp_profile_root=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --pid)
      pid="${2:?missing value for --pid}"
      shift 2
      ;;
    --temp-profile-root)
      temp_profile_root="${2:?missing value for --temp-profile-root}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ -z "$pid" || -z "$temp_profile_root" ]]; then
  usage >&2
  exit 1
fi

kill "$pid" 2>/dev/null || true
for _ in $(seq 1 10); do
  if ! kill -0 "$pid" 2>/dev/null; then
    break
  fi
  sleep 1
done
kill -9 "$pid" 2>/dev/null || true
rm -rf "$temp_profile_root"
