#!/bin/bash
# End-to-end check of the companion app in the iOS Simulator, all on this Mac:
#   a local account server (wrangler dev; sign-in emails are printed to its log),
#   the desktop app replaying the San Diego -> Phoenix recording, signed in to that server,
#   and the app's UI test: sign in by emailed code, watch the flight arrive, sign out.
#
#   ios/run-e2e.sh            # the phone on the same "Wi-Fi" (straight to the desktop)
#   ios/run-e2e.sh --relay    # the desktop's local-network server off: everything through the relay
#
# Needs Xcode (DEVELOPER_DIR below), Node (server/), and the repo's .venv.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export DEVELOPER_DIR="${DEVELOPER_DIR:-/Applications/Xcode-beta.app/Contents/Developer}"
WORK="${TMPDIR:-/tmp}/localtc-e2e"
MODE="${1:-}"
EMAIL="e2e-$(date +%s)@example.com"
API="http://localhost:8787"
DESKTOP="http://127.0.0.1:8767"
mkdir -p "$WORK/data/localtc"
: > "$WORK/wrangler.log"

cleanup() {
  [ -n "${APP_PID:-}" ] && kill "$APP_PID" 2>/dev/null || true
  [ -n "${WRANGLER_PID:-}" ] && kill "$WRANGLER_PID" 2>/dev/null || true
  lsof -ti tcp:8787 | xargs kill 2>/dev/null || true
}
trap cleanup EXIT

code_for() {  # the newest sign-in code emailed to $1
  for _ in $(seq 1 40); do
    c=$(grep -o "\[email to $1\] [0-9]\{6\}" "$WORK/wrangler.log" | tail -1 | grep -o "[0-9]\{6\}$" || true)
    [ -n "$c" ] && { echo "$c"; return; }
    sleep 0.5
  done
  echo "no code for $1" >&2; exit 1
}

echo "== account server"
# Runs sign in from the same address again and again: the local rate-limit counters start empty.
(cd "$ROOT/server" && npx wrangler d1 migrations apply localtc --local --env dev --config wrangler.toml >/dev/null \
  && npx wrangler d1 execute localtc --local --env dev --config wrangler.toml --command "DELETE FROM attempts" >/dev/null \
  && exec npx wrangler dev --env dev --config wrangler.toml --port 8787) > "$WORK/wrangler.log" 2>&1 &
WRANGLER_PID=$!
for _ in $(seq 1 60); do curl -s "$API/v1/health" >/dev/null && break; sleep 1; done

echo "== desktop app (replaying KSAN -> KPHX)"
LAN=true; EXPECT="Same Wi-Fi"
if [ "$MODE" = "--relay" ]; then LAN=false; EXPECT="Via server"; fi
cat > "$WORK/data/localtc/settings.toml" <<TOML
[account]
api_url = "$API"
companion_lan = $LAN
[ui]
source = "replay"
[replay]
path = "$ROOT/tests/fixtures/real_ksan_kphx"
speed = 20.0
[tts]
enabled = false
[voice]
enabled = false
[llm]
enabled = false
TOML
# No system keychain for this throwaway sign-in: the token lives in memory until the app exits.
(cd "$ROOT" && XDG_CACHE_HOME="$WORK/data" PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring PYTHONPATH=src \
  exec .venv/bin/python -m localtc.cli app --port 8767 --no-open) > "$WORK/desktop.log" 2>&1 &
APP_PID=$!
for _ in $(seq 1 60); do curl -s "$DESKTOP/api/state" >/dev/null && break; sleep 1; done

echo "== desktop signs in as $EMAIL"
curl -s -X POST "$DESKTOP/api/account/start" -H 'Content-Type: application/json' -d "{\"email\":\"$EMAIL\"}" >/dev/null
CODE=$(code_for "$EMAIL")
curl -s -X POST "$DESKTOP/api/account/finish" -H 'Content-Type: application/json' -d "{\"email\":\"$EMAIL\",\"code\":\"$CODE\"}" | head -c 200; echo
curl -s -X POST "$DESKTOP/api/flight/start" -H 'Content-Type: application/json' -d '{}' >/dev/null

echo "== UI test in the simulator (expecting: $EXPECT)"
cd "$ROOT/ios"
TEST_RUNNER_LOCALTC_API="$API" TEST_RUNNER_LOCALTC_MAIL_LOG="$WORK/wrangler.log" TEST_RUNNER_LOCALTC_EMAIL="$EMAIL" \
TEST_RUNNER_LOCALTC_EXPECT="$EXPECT" \
xcodebuild test -project "LocalTC Companion.xcodeproj" -scheme "LocalTC Companion" \
  -destination "platform=iOS Simulator,name=${SIMULATOR:-iPhone 17}" \
  -derivedDataPath "$WORK/dd" -resultBundlePath "$WORK/result-$(date +%s).xcresult" 2>&1 \
  | grep -E "Test Case|error:|passed|failed|\*\* TEST" || true
echo "Result bundles and logs: $WORK"
