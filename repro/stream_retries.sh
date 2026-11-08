#!/bin/zsh
# Counts the chat requests Hermes sends when a stream breaks, against repro/fake_stream.py.
# Offline: a disposable HERMES_HOME per case, a fake key, and 127.0.0.1 only.
#
# Usage: HERMES_BIN=/path/to/hermes repro/stream_retries.sh [characterize|acceptance]
#   characterize (default): compare with the counts Hermes v0.23.0 is known to produce. Passing
#     means the reproduction is stable, not that retries are correct.
#   acceptance: compare with the required behaviour: with retries disabled, one request per
#     failure and a completed tool run exactly once. Expected to FAIL on Hermes v0.23.0.
set -u
CHECK=${1:-characterize}
DIR=${0:A:h}; WORK=$(mktemp -d); PORT=${PORT:-18480}; fail=0
: ${HERMES_BIN:?set HERMES_BIN to the hermes executable of a Hermes v0.23.0 environment}
# mode : observed requests : observed tool runs : required requests : required tool runs [: auto_recovery_cycles, default 0]
# Requests count main-turn chat requests (they carry the tool list; the auxiliary title call does not).
# tool_done_eof needs two requests even when correct: the tool call, then the answer that breaks.
# tool_done_error runs with one auto-recovery cycle, which waits 15-120 s before it resends.
CASES=(
  ok:1:0:1:0
  error_frame:2:0:1:0
  eof:4:0:1:0
  tool_eof:4:0:1:0
  tool_done_eof:5:1:2:1
  tool_done_error:4:1:2:1:1
)
for c in $CASES; do
  parts=(${(s.:.)c}); mode=$parts[1]; cycles=${parts[6]:-0}; PORT=$((PORT+1))
  if [ $CHECK = acceptance ]; then want_req=$parts[4]; want_runs=$parts[5]; else want_req=$parts[2]; want_runs=$parts[3]; fi
  H=$WORK/$mode; mkdir -p $H; LOG=$H/requests.jsonl; RUNS=$H/tool-runs.txt; : > $LOG; : > $RUNS
  printf 'model:\n  provider: custom\n  base_url: http://127.0.0.1:%s/v1\n  default: m\n  api_key: sk-fake-local\n  context_length: 131072\nagent:\n  api_max_retries: 0\n  auto_recovery_cycles: %s\ncompression:\n  enabled: false\n' $PORT $cycles > $H/config.yaml
  TOOL_COUNTER=$RUNS python3 $DIR/fake_stream.py $mode $LOG $PORT & fp=$!; sleep 1
  env -i HOME=$H PATH=/usr/bin:/bin HERMES_HOME=$H /usr/bin/perl -e 'alarm shift; exec @ARGV' $((120 + cycles * 180)) \
    $HERMES_BIN chat -q "Say hi" --oneshot -Q --yolo > $H/out.txt 2>&1; rc=$?
  env -i HOME=$H PATH=/usr/bin:/bin HERMES_HOME=$H $HERMES_BIN gateway stop --yes > /dev/null 2>&1
  kill $fp 2>/dev/null; wait $fp 2>/dev/null
  got_req=$(grep -c '"tools": [1-9]' $LOG); got_runs=$(grep -c ran $RUNS)
  v=ok
  [ "$want_req" = OBSERVED ] || [ "$got_req" = "$want_req" ] || v=DIFFERENT
  [ "$want_runs" = OBSERVED ] || [ "$got_runs" = "$want_runs" ] || v=DIFFERENT
  [ $v = ok ] || fail=1
  echo "$CHECK $mode requests=$got_req (want $want_req) tool_runs=$got_runs (want $want_runs) exit=$rc $v"
done
echo "logs: $WORK"; exit $fail
