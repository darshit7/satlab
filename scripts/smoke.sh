#!/usr/bin/env bash
# Smoke-test a running satlab deployment through the web container (nginx → API).
# Used by CI on freshly built images and after every production deploy; also handy locally.
#
#   scripts/smoke.sh <web base URL> [API base URL] [allowed Origin]
#   scripts/smoke.sh http://localhost:8080 http://localhost:8000 http://localhost:8080
#   scripts/smoke.sh https://satlab.darshitp.dev
#
# The API URL (direct, bypassing nginx) is optional: production's API has internal ingress only.
# The allowed Origin (default: the web base URL) must be in the API's SATLAB_CORS_ORIGINS.
set -euo pipefail

web=${1:?usage: smoke.sh <web base URL> [API base URL] [allowed Origin]}
api=${2:-}
origin=${3:-$web}
failures=0

pass() { echo "ok    $*"; }
fail() { echo "FAIL  $*" >&2; failures=$((failures + 1)); }
code() { curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$@" || true; }
expect() { # expect <status> <description> <curl args…>
  local want=$1 what=$2 got
  shift 2
  got=$(code "$@")
  if [[ $got == "$want" ]]; then pass "$want $what"; else fail "$what: got $got, want $want"; fi
}
json() { python3 -c "import json, sys; print(json.load(sys.stdin)$1)"; }
ws_handshake() { # HTTP status of a WebSocket upgrade (key: the RFC 6455 sample, 16 bytes); then cut it short
  curl -s -o /dev/null -w '%{http_code}' --max-time 3 --http1.1 \
    -H 'Connection: Upgrade' -H 'Upgrade: websocket' -H 'Sec-WebSocket-Version: 13' \
    -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' "$@" || true
}

# --- static site and headers ---
expect 302 "/ redirects to the app" "$web/"
expect 200 "health probe" "$web/healthz"
expect 200 "app shell" "$web/satlab/"
expect 200 "SPA fallback for client routes" "$web/satlab/some/client/route"
expect 404 "nothing outside /satlab/" "$web/other"
headers=$(curl -s -D - -o /dev/null --max-time 10 "$web/satlab/" | tr -d '\r')
grep -qi "^content-security-policy: .*'sha256-" <<<"$headers" && pass "CSP with the theme-script hash" \
  || fail "CSP header with a script hash missing"
grep -qi '^x-content-type-options: nosniff' <<<"$headers" && pass "nosniff" || fail "nosniff missing"
grep -qi '^server: nginx/' <<<"$headers" && fail "nginx version leaked" || pass "no server version"
asset=$(curl -s --max-time 10 "$web/satlab/" | grep -o '/satlab/assets/[^"]*\.js' | head -1)
if [[ -n $asset ]]; then
  curl -s -D - -o /dev/null --max-time 10 "$web$asset" | grep -qi 'cache-control: .*immutable' \
    && pass "hashed assets are immutable" || fail "asset cache headers"
  # Module workers (MapLibre) need a JavaScript MIME type: with nosniff, anything else breaks the map.
  worker=$(curl -s --max-time 10 "$web$asset" | grep -o 'assets/[A-Za-z0-9_.-]*worker[A-Za-z0-9_.-]*\.mjs' | head -1)
  if [[ -n $worker ]]; then
    type=$(curl -s -o /dev/null -w '%{content_type}' --max-time 10 "$web/satlab/$worker")
    [[ $type == text/javascript* || $type == application/javascript* ]] \
      && pass "worker served as JavaScript ($type)" || fail "worker $worker served as '$type'"
  else
    fail "no .mjs worker referenced by $asset"
  fi
else
  fail "no JS asset referenced by index.html"
fi

# --- API through the proxy ---
expect 200 "API health via proxy" "$web/satlab/api/health"
station_id=$(curl -s --max-time 10 "$web/satlab/api/stations" | json '[0]["id"]' 2>/dev/null || true)
if [[ -n $station_id ]]; then pass "stations via proxy"; else fail "stations via proxy"; fi
expect 404 "no API docs via proxy" "$web/satlab/api/docs"

session=$(curl -s --max-time 10 -X POST -H 'Content-Type: application/json' \
  -d "{\"station_id\": ${station_id:-1}}" "$web/satlab/api/sessions" || true)
id=$(json '["id"]' <<<"$session" 2>/dev/null || true)
token=$(json '["token"]' <<<"$session" 2>/dev/null || true)
if [[ -n $id && -n $token ]]; then
  pass "session created with a token"
  expect 404 "session hidden without its token" "$web/satlab/api/sessions/$id"
  expect 200 "session readable with its token" -H "X-Session-Token: $token" "$web/satlab/api/sessions/$id"
  got=$(ws_handshake -H "Origin: $origin" "$web/satlab/ws/sessions/$id")
  [[ $got == 101 ]] && pass "101 WebSocket upgrade via proxy" || fail "WebSocket upgrade via proxy: got $got"
  got=$(ws_handshake -H 'Origin: https://evil.example' "$web/satlab/ws/sessions/$id")
  [[ $got == 403 ]] && pass "403 WebSocket from a foreign Origin" || fail "foreign Origin: got $got, want 403"
  expect 204 "session ended" -X POST -H "X-Session-Token: $token" "$web/satlab/api/sessions/$id/end"
else
  fail "session create: $session"
fi

# --- API directly (only where it is reachable) ---
if [[ -n $api ]]; then
  expect 200 "API health direct" "$api/api/health"
  expect 404 "docs disabled" "$api/docs"
  expect 404 "OpenAPI schema disabled" "$api/openapi.json"
fi

if ((failures)); then
  echo "$failures check(s) failed" >&2
  exit 1
fi
echo "all smoke checks passed"
