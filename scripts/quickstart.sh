#!/usr/bin/env bash
# Guided first run: check tools, install dependencies, reach the pod, train.
#
#   scripts/quickstart.sh                      # asks for anything missing
#   scripts/quickstart.sh --pod 192.168.1.88 --side left --watch-export ~/Downloads/export.zip
#
# Read-only toward the pod: it fetches data over the LAN API and never uploads,
# imports or activates anything. Outputs stay under the ignored data/ folder.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
POD="${POD:-}"
SIDE=""
EXPORT=""
DRY_RUN=0
YES=0

usage() {
  sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'
  cat <<'EOF'

Options:
  --pod ADDRESS         pod IP or hostname (port 3000 is assumed)
  --side left|right     the side whose sleeper wears the Apple Watch
  --watch-export FILE   Apple Health export.zip; omit to use Watch data already on the pod
  --yes                 install uv without asking if it is missing
  --dry-run             check everything, then print the training command instead of running it
EOF
}

die() { echo "quickstart: $*" >&2; exit 1; }
ask() { local prompt="$1" reply; read -r -p "$prompt" reply || true; printf '%s' "$reply"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --pod) POD="${2:-}"; shift 2 ;;
    --side) SIDE="${2:-}"; shift 2 ;;
    --watch-export) EXPORT="${2:-}"; shift 2 ;;
    --yes) YES=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "unknown option: $1" ;;
  esac
done

cd "$ROOT"

# 1. Tools. Node must match .node-version's major; uv manages Python itself.
want_node="$(cut -d. -f1 < .node-version)"
command -v node >/dev/null 2>&1 || die "Node.js $want_node is not installed. On a Mac: brew install node@$want_node (then follow brew's PATH hint)."
have_node="$(node --version | sed 's/^v//' | cut -d. -f1)"
[ "$have_node" = "$want_node" ] || die "Node.js $want_node is required; found $(node --version). On a Mac: brew install node@$want_node."
command -v npm >/dev/null 2>&1 || die "npm is missing; reinstall Node.js $want_node."
if ! command -v uv >/dev/null 2>&1; then
  if [ "$YES" = 1 ] || [ "$(ask 'uv is not installed. Install it now from astral.sh? [y/N] ')" = "y" ]; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    command -v uv >/dev/null 2>&1 || die "uv installed but is not on PATH; open a new terminal and rerun."
  else
    die "uv is required: https://docs.astral.sh/uv/getting-started/installation/"
  fi
fi
echo "✓ Node.js $(node --version), $(uv --version)"

# 2. Dependencies, pinned by the lockfiles.
if [ "$DRY_RUN" = 1 ]; then
  echo "• would run: npm ci && uv sync --locked"
else
  npm ci --no-audit --no-fund
  uv sync --locked
fi

# 3. The pod. Its address is shown in the sleepypod iOS app under Settings.
[ -n "$POD" ] || POD="$(ask 'Pod IP address (sleepypod app → Settings): ')"
[ -n "$POD" ] || die "a pod address is required"
case "$POD" in http://*|https://*) base="$POD" ;; *:*) base="http://$POD" ;; *) base="http://$POD:3000" ;; esac
curl -fsS -m 5 "$base/api/settings" >/dev/null 2>&1 || die "cannot reach the pod at $base. Is this Mac on the same network as the pod?"
echo "✓ Pod reachable at $base"

# 4. Whose sleep, and the Watch labels.
[ -n "$SIDE" ] || SIDE="$(ask 'Which side does the Watch wearer sleep on? [left/right] ')"
case "$SIDE" in left|right) ;; *) die "side must be left or right" ;; esac
if [ -z "$EXPORT" ] && [ "$DRY_RUN" = 0 ]; then
  EXPORT="$(ask 'Path to Apple Health export.zip (Enter to use Watch data already on the pod): ')"
fi
EXPORT="${EXPORT/#\~/$HOME}"
[ -z "$EXPORT" ] || [ -f "$EXPORT" ] || die "no file at $EXPORT"

# 5. Train. A fresh run folder each time; nothing is uploaded or activated.
out="data/runs/$(date -u +%Y%m%dT%H%M%SZ)-quickstart"
cmd=(uv run --locked python scripts/train.py --pod "$POD" --side "$SIDE" --dashboard --out "$out")
[ -z "$EXPORT" ] || cmd+=(--watch-export "$EXPORT")
if [ "$DRY_RUN" = 1 ]; then
  printf '• would run:'; printf ' %q' "${cmd[@]}"; echo
  exit 0
fi
echo "Training into $out (a live dashboard link appears below; Ctrl-C closes it when you are done)…"
"${cmd[@]}"
