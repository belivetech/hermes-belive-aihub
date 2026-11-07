#!/bin/zsh
# Counts the chat requests Hermes sends when a stream breaks, against repro/fake_stream.py.
# Offline: a disposable HERMES_HOME per case, a fake key, and 127.0.0.1 only.
# Usage: HERMES_BIN=/path/to/hermes-env/bin/hermes repro/stream_retries.sh
# Exit 0 when every count matches EXPECTED below (Hermes v0.23.0); a change means Hermes changed.
set -u
DIR=${0:A:h}; WORK=$(mktemp -d); PORT=${PORT:-18480}; fail=0
: ${HERMES_BIN:?set HERMES_BIN to the hermes executable of a Hermes v0.23.0 environment}
# mode:api_max_retries:auto_recovery_cycles:expected main requests
CASES=(ok:0:0:1 error_frame:0:0:2 eof:0:0:4 tool_eof:0:0:4)
for c in $CASES; do
  parts=(${(s.:.)c}); mode=$parts[1]; r=$parts[2]; arc=$parts[3]; want=$parts[4]; PORT=$((PORT+1))
  H=$WORK/$mode; mkdir -p $H; LOG=$H/requests.jsonl; : > $LOG
  printf 'model:\n  provider: custom\n  base_url: http://127.0.0.1:%s/v1\n  default: m\n  api_key: sk-fake-local\n  context_length: 131072\nagent:\n  api_max_retries: %s\n  auto_recovery_cycles: %s\ncompression:\n  enabled: false\n' $PORT $r $arc > $H/config.yaml
  python3 $DIR/fake_stream.py $mode $LOG $PORT & fp=$!; sleep 1
  env -i HOME=$H PATH=/usr/bin:/bin HERMES_HOME=$H /usr/bin/perl -e 'alarm shift; exec @ARGV' 120 \
    $HERMES_BIN chat -q "Say hi" --oneshot -Q > $H/out.txt 2>&1; rc=$?
  env -i HOME=$H PATH=/usr/bin:/bin HERMES_HOME=$H $HERMES_BIN gateway stop --yes > /dev/null 2>&1
  kill $fp 2>/dev/null; wait $fp 2>/dev/null
  # Main-turn requests carry the tool list; the auxiliary title call does not.
  got=$(grep -c '"tools": [1-9]' $LOG)
  [ "$got" = "$want" ] && v=ok || { v=CHANGED; fail=1; }
  echo "$mode api_max_retries=$r auto_recovery_cycles=$arc requests=$got expected=$want exit=$rc $v"
done
echo "logs: $WORK"; exit $fail
