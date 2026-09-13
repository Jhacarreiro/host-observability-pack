#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
AUDIT="$ROOT/scripts/audit-public"
run_bad_fixture() {
  kind=$1
  tmp=$(mktemp -d /tmp/hop-public-audit.XXXXXX)
  cp "$AUDIT" "$tmp/audit-public"
  chmod +x "$tmp/audit-public"
  cd "$tmp"
  git init -q
  git config user.email test@example.invalid
  git config user.name test
  case "$kind" in
    private-ip) printf 'target=%s\n' "192.$(printf 168).203.77" > fixture.txt ;;
    private-path) printf 'path=%s\n' "/home/$(printf synthetic-operator)/service" > fixture.txt ;;
    credential) printf 'API_KEY=%s\n' "synthetic$(printf 1234567890)" > fixture.txt ;;
  esac
  git add fixture.txt audit-public
  if ./audit-public >/dev/null 2>&1; then
    echo "negative audit fixture was not rejected: $kind" >&2
    rm -rf "$tmp"
    exit 1
  fi
  cd /
  rm -rf "$tmp"
}
run_bad_fixture private-ip
run_bad_fixture private-path
run_bad_fixture credential
echo "public audit negative fixtures: PASS"
