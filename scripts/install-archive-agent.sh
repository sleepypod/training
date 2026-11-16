#!/usr/bin/env bash
# Nightly off-pod archive (plan A1/B3): a launchd agent that runs core's
# `pnpm biometrics:archive pull` at 12:30 into this checkout's ignored data/archive.
#
#   scripts/install-archive-agent.sh --dry-run     # print the plist, change nothing
#   scripts/install-archive-agent.sh               # write and load the agent
#   scripts/install-archive-agent.sh --uninstall   # unload and remove it
#   scripts/install-archive-agent.sh --print-command  # the pull it runs, for the dashboard
#
# The pull is read-only toward the pod. The pod comes from data/workspace-settings.json
# `pod`; the core checkout from its `coreCheckout` key, else ../sleepypod-core next to
# this checkout. Logs go to data/archive/pull.log.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.sleepypod.training-archive"
DRY_RUN=0
UNINSTALL=0
PRINT=0
POD=""
CORE=""
SETTINGS=""
DEST=""

usage() {
  sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'
  cat <<'EOF'

Options:
  --pod HOST[:PORT]   override workspace-settings `pod`
  --core DIR          override workspace-settings `coreCheckout`
  --settings FILE     workspace settings (default: <primary checkout>/data/workspace-settings.json)
  --dest DIR          archive destination (default: <primary checkout>/data/archive)
  --dry-run           print the plist instead of installing it
  --uninstall         unload and delete the agent
  --print-command     print the pull's working directory, PATH and argv, NUL-separated, and exit
EOF
}

die() { echo "install-archive-agent: $*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --pod) POD="${2:-}"; shift 2 ;;
    --core) CORE="${2:-}"; shift 2 ;;
    --settings) SETTINGS="${2:-}"; shift 2 ;;
    --dest) DEST="${2:-}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    --print-command) PRINT=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "unknown option: $1" ;;
  esac
done

PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ "$UNINSTALL" = 1 ]; then
  if [ "$DRY_RUN" = 1 ]; then
    echo "Would unload $LABEL and delete $PLIST"
    exit 0
  fi
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Removed $LABEL"
  exit 0
fi

# Worktrees share the primary checkout's data/, where the archive lives.
common="$(git -C "$ROOT" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
PRIMARY="${common:+$(dirname "$common")}"
PRIMARY="${PRIMARY:-$ROOT}"
SETTINGS="${SETTINGS:-$PRIMARY/data/workspace-settings.json}"
DEST="${DEST:-$PRIMARY/data/archive}"

command -v node >/dev/null || die "node is not on PATH"
setting() {
  [ -f "$SETTINGS" ] || return 0
  node -e 'const v = JSON.parse(require("fs").readFileSync(process.argv[1], "utf8"))[process.argv[2]]; if (typeof v === "string") process.stdout.write(v)' "$SETTINGS" "$1"
}
POD="${POD:-$(setting pod)}"
CORE="${CORE:-$(setting coreCheckout)}"
CORE="${CORE:-$(dirname "$PRIMARY")/sleepypod-core}"
[ -n "$POD" ] || die "no pod: set \"pod\" in $SETTINGS or pass --pod"
[[ "$POD" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*(:[0-9]{1,5})?$ ]] || die "pod must be HOST or HOST:PORT, got $POD"
[ -d "$CORE" ] || die "core checkout $CORE not found: set \"coreCheckout\" in $SETTINGS or pass --core"
CORE="$(cd "$CORE" && pwd)"

# The checkout must have the archiver and its --curl transport (sleepypod/core#925).
node -e 'const s = JSON.parse(require("fs").readFileSync(process.argv[1], "utf8")).scripts || {}; process.exit(s["biometrics:archive"] ? 0 : 1)' "$CORE/package.json" 2>/dev/null \
  || die "$CORE has no biometrics:archive script; point coreCheckout at a core checkout that has scripts/biometrics-archive"
grep -q -- "--curl" "$CORE/scripts/biometrics-archive/cli.ts" 2>/dev/null \
  || die "$CORE's archiver has no --curl option; update the checkout (sleepypod/core#925)"
[ -d "$CORE/node_modules" ] || echo "warning: $CORE/node_modules is missing; run pnpm install there before the first pull" >&2

# launchd starts with a bare PATH and no nvm: pin core's Node version when nvm has it.
want="$(cat "$CORE/.node-version" 2>/dev/null || cat "$CORE/.nvmrc" 2>/dev/null || true)"
want="${want#v}"
NODE_BIN=""
if [ -n "$want" ] && [ -x "${NVM_DIR:-$HOME/.nvm}/versions/node/v$want/bin/node" ]; then
  NODE_BIN="${NVM_DIR:-$HOME/.nvm}/versions/node/v$want/bin"
else
  NODE_BIN="$(dirname "$(command -v node)")"
fi
if [ -x "$NODE_BIN/pnpm" ]; then
  PNPM_BIN="$NODE_BIN"
else
  pnpm_path="$(command -v pnpm || true)"
  [ -n "$pnpm_path" ] || die "pnpm not found next to $NODE_BIN/node or on PATH"
  PNPM_BIN="$(dirname "$pnpm_path")"
fi
AGENT_PATH="$NODE_BIN"
[ "$PNPM_BIN" = "$NODE_BIN" ] || AGENT_PATH="$AGENT_PATH:$PNPM_BIN"
AGENT_PATH="$AGENT_PATH:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

squote() { printf "'%s'" "${1//\'/\'\\\'\'}"; }
xml() { local s="${1//&/&amp;}"; s="${s//</&lt;}"; s="${s//>/&gt;}"; printf '%s' "$s"; }

PULL=(pnpm -s biometrics:archive pull --pod "$POD" --dest "$DEST" --curl)
if [ "$PRINT" = 1 ]; then
  # The dashboard's one-click and on-open pulls run exactly this, so both share the resolution above.
  printf '%s\0' "$CORE" "$AGENT_PATH" "${PULL[@]}"
  exit 0
fi

LOG="$DEST/pull.log"
# Same pull as PULL above; test_web_jobs.py checks the two agree.
COMMAND="echo \"--- \$(date -u +%Y-%m-%dT%H:%M:%SZ)\"; cd $(squote "$CORE") && pnpm -s biometrics:archive pull --pod $(squote "$POD") --dest $(squote "$DEST") --curl"

plist() {
  cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/sh</string><string>-c</string>
    <string>$(xml "$COMMAND")</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>$(xml "$AGENT_PATH")</string></dict>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>12</integer><key>Minute</key><integer>30</integer></dict>
  <key>StandardOutPath</key><string>$(xml "$LOG")</string>
  <key>StandardErrorPath</key><string>$(xml "$LOG")</string>
</dict>
</plist>
EOF
}

if [ "$DRY_RUN" = 1 ]; then
  plist
  echo "# node $("$NODE_BIN/node" --version) from $NODE_BIN; would write $PLIST" >&2
  exit 0
fi

[ "$(uname)" = Darwin ] || die "launchd agents are macOS only; use the cron line in core's scripts/biometrics-archive/README.md"
mkdir -p "$DEST" "$(dirname "$PLIST")"
plist > "$PLIST"
plutil -lint "$PLIST" >/dev/null
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installed $LABEL: daily 12:30 pull from $POD into $DEST (log $LOG)"
echo "Run it now with: launchctl kickstart gui/$(id -u)/$LABEL"
