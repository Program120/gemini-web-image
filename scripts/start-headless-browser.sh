#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  start-headless-browser.sh [--browser brave|chrome|chromium] [--desktop-user user] [--profile-directory Default] [--port N] [--session NAME] [--download-dir PATH]
EOF
}

browser="brave"
desktop_user="user"
profile_directory="Default"
port=""
session_name="gemini-web-image"
download_dir=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --browser)
      browser="${2:?missing value for --browser}"
      shift 2
      ;;
    --desktop-user)
      desktop_user="${2:?missing value for --desktop-user}"
      shift 2
      ;;
    --profile-directory)
      profile_directory="${2:?missing value for --profile-directory}"
      shift 2
      ;;
    --port)
      port="${2:?missing value for --port}"
      shift 2
      ;;
    --session)
      session_name="${2:?missing value for --session}"
      shift 2
      ;;
    --download-dir)
      download_dir="${2:?missing value for --download-dir}"
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

if ! id "$desktop_user" >/dev/null 2>&1; then
  echo "Desktop user not found: $desktop_user" >&2
  exit 1
fi

desktop_home="$(getent passwd "$desktop_user" | cut -d: -f6)"
desktop_uid="$(id -u "$desktop_user")"
xdg_runtime_dir="/run/user/$desktop_uid"
dbus_session_bus="unix:path=$xdg_runtime_dir/bus"

case "$browser" in
  brave)
    executable="/opt/brave.com/brave/brave"
    source_profile_root="$desktop_home/.config/BraveSoftware/Brave-Browser"
    ;;
  chrome)
    executable="/opt/google/chrome/chrome"
    source_profile_root="$desktop_home/.config/google-chrome"
    ;;
  chromium)
    if [[ -x /usr/bin/chromium-browser ]]; then
      executable="/usr/bin/chromium-browser"
    elif [[ -x /usr/bin/chromium ]]; then
      executable="/usr/bin/chromium"
    else
      echo "Chromium executable not found." >&2
      exit 1
    fi
    source_profile_root="$desktop_home/.config/chromium"
    ;;
  *)
    echo "Unsupported browser: $browser" >&2
    exit 1
    ;;
esac

if [[ ! -x "$executable" ]]; then
  echo "Browser executable not found: $executable" >&2
  exit 1
fi

if [[ ! -d "$source_profile_root/$profile_directory" ]]; then
  echo "Profile directory not found: $source_profile_root/$profile_directory" >&2
  exit 1
fi

if [[ ! -S "$xdg_runtime_dir/bus" ]]; then
  echo "User D-Bus session bus not found: $xdg_runtime_dir/bus" >&2
  exit 1
fi

if [[ -z "$port" ]]; then
  port="$(
    python3 - <<'PY'
import socket
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
  )"
fi

if [[ -z "$download_dir" ]]; then
  download_dir="$PWD"
fi
mkdir -p "$download_dir"

temp_profile_root="$(mktemp -d "/tmp/gemini-${browser}-profile.XXXXXX")"
log_path="/tmp/gemini-${browser}-${port}.log"

rsync -a \
  --exclude='Singleton*' \
  --exclude='lockfile' \
  --exclude='Crash Reports' \
  --exclude='Safe Browsing' \
  --exclude='ShaderCache' \
  --exclude='GrShaderCache' \
  --exclude='GraphiteDawnCache' \
  --exclude='component_crx_cache' \
  --exclude='extensions_crx_cache' \
  --exclude='Default/Cache' \
  --exclude='Default/Code Cache' \
  --exclude='Default/GPUCache' \
  "$source_profile_root/" \
  "$temp_profile_root/"

mkdir -p "$download_dir"

launch_args=(
  env
  "HOME=$desktop_home"
  "XDG_RUNTIME_DIR=$xdg_runtime_dir"
  "DBUS_SESSION_BUS_ADDRESS=$dbus_session_bus"
  "$executable"
  --headless=new
  --remote-debugging-port="$port"
  --remote-allow-origins=*
  --no-sandbox
  --disable-dev-shm-usage
  --user-data-dir="$temp_profile_root"
  --profile-directory="$profile_directory"
  about:blank
)

if [[ "$(id -un)" == "$desktop_user" ]]; then
  nohup "${launch_args[@]}" >"$log_path" 2>&1 </dev/null &
  pid="$!"
else
  sudo -u "$desktop_user" -b "${launch_args[@]}" >"$log_path" 2>&1 </dev/null
  pid=""
  for _ in $(seq 1 10); do
    pid="$(
      ps -u "$desktop_user" -o pid= -o args= \
        | awk -v port="$port" -v profile="$temp_profile_root" '
            index($0, "--remote-debugging-port=" port) && index($0, "--user-data-dir=" profile) {
              print $1
              exit
            }
          '
    )"
    if [[ -n "$pid" ]]; then
      break
    fi
    sleep 1
  done
fi

if [[ -z "${pid:-}" ]]; then
  echo "Could not determine browser pid for port ${port}." >&2
  exit 1
fi

for _ in $(seq 1 30); do
  if curl -sf "http://127.0.0.1:${port}/json/version" >/dev/null; then
    break
  fi
  sleep 1
done

if ! curl -sf "http://127.0.0.1:${port}/json/version" >/dev/null; then
  echo "Browser did not expose CDP on port ${port}. See ${log_path}" >&2
  exit 1
fi

python3 - <<PY
import json
print(json.dumps({
    "browser": ${browser@Q},
    "desktop_user": ${desktop_user@Q},
    "desktop_home": ${desktop_home@Q},
    "executable": ${executable@Q},
    "source_profile_root": ${source_profile_root@Q},
    "profile_directory": ${profile_directory@Q},
    "temp_profile_root": ${temp_profile_root@Q},
    "port": int(${port@Q}),
    "pid": int(${pid@Q}),
    "session": ${session_name@Q},
    "download_dir": ${download_dir@Q},
    "log_path": ${log_path@Q},
    "cdp_version_url": f"http://127.0.0.1:{${port@Q}}/json/version"
}, ensure_ascii=False, indent=2))
PY
