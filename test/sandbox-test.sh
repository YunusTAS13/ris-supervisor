#!/usr/bin/env bash
# Sandbox end-to-end test for ris-supervisor.
# Runs rissup + risctl against a scratch directory; touches nothing else.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(mktemp -d)"
DAEMON=""
cleanup() {
    if kill -0 "${DAEMON}" 2>/dev/null; then
        kill -TERM "${DAEMON}" 2>/dev/null || true
        wait "${DAEMON}" 2>/dev/null || true
    fi
    rm -rf "${ROOT}"
}
trap cleanup EXIT

mkdir -p "${ROOT}/config" "${ROOT}/log"

cat >"${ROOT}/config/ticker.service" <<'EOF'
exec = /bin/sh -c 'while true; do echo sandboxtick; sleep 0.2; done'
restart = always
restart_delay = 0.3
run_at_boot = true
EOF

cat >"${ROOT}/config/crashy.service" <<'EOF'
exec = /bin/sh -c 'echo dying; exit 3'
restart = on_failure
restart_delay = 0.2
backoff_limit = 5
run_at_boot = false
EOF

cat >"${ROOT}/config/never.service" <<'EOF'
exec = /bin/sh -c 'sleep 997'
restart = never
run_at_boot = true
stop_timeout = 3
EOF

cat >"${ROOT}/config/silent.service" <<'EOF'
exec = /bin/sh -c 'echo should-not-be-logged; sleep 100'
restart = never
run_at_boot = true
redirect = devnull
EOF

D="python3 src/supervisor.py --config-dir ${ROOT}/config --log-dir ${ROOT}/log --socket ${ROOT}/sock --log ${ROOT}/rissup.log"
$D &
DAEMON=$!

fail() { echo "FAIL: $1"; exit 1; }
ctl() { python3 src/ctl.py --socket "${ROOT}/sock" "$@"; }
field() { # field <service> <pid|state|restarts>
    local idx
    case "$2" in
        pid) idx=2 ;;
        state) idx=3 ;;
        restarts) idx=4 ;;
        *) idx=2 ;;
    esac
    ctl status | awk -v n="$1" -v c="$idx" '$1==n{print $c}'
}
wait_for() { # wait_for <probe-cmd...>
    for _ in $(seq 1 50); do
        if eval "$*" >/dev/null 2>&1; then return 0; fi
        sleep 0.2
    done
    fail "timeout waiting for: $*"
}

# wait for the control socket, then for boot services to come up
wait_for "test -S ${ROOT}/sock"
wait_for "[ \"\$(field ticker state)\" = running ]"

echo "== boot: ticker and never running, crashy not started"
[ "$(field ticker state)" = running ] || fail "ticker not running at boot"
[ "$(field never state)" = running ] || fail "never not running at boot"
[ "$(field crashy state)" = stopped ] || fail "crashy should not run at boot"
wait_for "grep -q sandboxtick ${ROOT}/log/ticker.log"
echo "   ok (service output is being logged)"

echo "== redirect devnull: silent service writes no log file"
[ "$(field silent state)" = running ] || fail "silent not running at boot"
[ ! -e "${ROOT}/log/silent.log" ] || fail "silent.log should not exist (redirect = devnull)"
echo "   ok"

echo "== crash-restart: SIGKILL ticker, supervisor brings it back"
OLD=$(field ticker pid)
kill -9 "$OLD" 2>/dev/null || true
wait_for "[ \"\$(field ticker state)\" = running ] && [ \"\$(field ticker pid)\" != $OLD ]"
[ "$(field ticker restarts)" -ge 1 ] || fail "ticker restarts counter not incremented"
echo "   ok (pid $OLD -> $(field ticker pid))"

echo "== backoff limiter: crashy gives up after backoff_limit=5"
ctl start crashy >/dev/null
sleep 3
[ "$(field crashy state)" = stopped ] || fail "crashy should have given up"
RESTARTS=$(field crashy restarts)
[ "$RESTARTS" = 4 ] || fail "crashy restarts=$RESTARTS want 4"
echo "   ok (stop after 5 failed attempts / 4 restarts)"

echo "== reload: new service appears and boots"
cat >"${ROOT}/config/later.service" <<'EOF'
exec = /bin/sh -c 'echo later; sleep 100'
restart = never
run_at_boot = true
EOF
ctl reload >/dev/null
wait_for "[ \"\$(field later state)\" = running ]"
echo "   ok"

echo "== stop command: graceful stop of never"
ctl stop never >/dev/null
wait_for "[ \"\$(field never state)\" = stopped ]"
echo "   ok"

echo "== restart command: ticker switches to a fresh pid"
OLD=$(field ticker pid)
ctl restart ticker >/dev/null
wait_for "[ \"\$(field ticker state)\" = running ] && [ \"\$(field ticker pid)\" != $OLD ]"
echo "   ok"

echo "== SIGTERM shutdown: daemon exits 0 and socket goes away"
kill -TERM "${DAEMON}"
wait "${DAEMON}"
DAEMON=""
[ ! -e "${ROOT}/sock" ] || fail "control socket was not removed"
sleep 1
pgrep -f "sandboxtick" >/dev/null 2>&1 && fail "ticker survived daemon shutdown"
pgrep -f "sleep 997" >/dev/null 2>&1 && fail "service survived daemon shutdown"

echo "ALL TESTS PASSED"