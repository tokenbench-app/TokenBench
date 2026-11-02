#!/bin/bash
# Chain verifier, run INSIDE the task image. Layout: /chain/{model.patch,test.patch,tasks/NN/{run_script.sh,parser.py,args.txt}}
# Output: /chain/out/{apply.txt,tree.txt,NN.json,NN.stdout,NN.stderr}
ulimit -n 65536 2>/dev/null || true
set -uo pipefail
mkdir -p /chain/out
cd /app 2>/dev/null || cd /testbed 2>/dev/null || { echo "no /app" > /chain/out/apply.txt; exit 1; }
git config --global --add safe.directory '*' 2>/dev/null || true
rm -f .git/index.lock 2>/dev/null || true
git rev-parse 'HEAD^{tree}' > /chain/out/tree.txt 2>/dev/null || true

APPLY=empty
if [ -s /chain/model.patch ]; then
  if git apply --whitespace=nowarn --binary /chain/model.patch 2>/chain/out/apply.err; then APPLY=clean
  elif patch -p1 --forward --batch --fuzz=3 < /chain/model.patch >>/chain/out/apply.err 2>&1; then APPLY=fuzz
  else APPLY=failed; fi
fi
echo "$APPLY" > /chain/out/apply.txt

find . -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find . -name '*.pyc' -delete 2>/dev/null || true

# ---- reset test space (same rules as the SWE-bench Pro V2 tests/test.sh) ----
python3 - <<'PYRESET' > /tmp/reset_list.txt
import subprocess, re
CODE_DIRS = {'tests', 'test', '__tests__', 'spec', 'specs'}
DATA_DIRS = {'testdata', 'test_data', 'fixtures', 'fixture', 'mocks', 'mock', '__snapshots__'}
def is_test_code(p):
    segs = p.split('/')
    parents, b = segs[:-1], segs[-1]
    if any(s in DATA_DIRS for s in parents):
        return False
    if any(s in CODE_DIRS for s in parents):
        return True
    if b == 'conftest.py':
        return True
    if re.search(r'_test\.(go|py|js|jsx|ts|tsx)$', b):
        return True
    if re.search(r'^test_.*\.py$', b):
        return True
    if re.search(r'\.(test|spec)\.(js|jsx|ts|tsx)$', b):
        return True
    return False
def git(a):
    return subprocess.run(['git'] + a, capture_output=True, text=True).stdout.splitlines()
for p in git(['ls-files']):
    if is_test_code(p):
        print('T ' + p)
for p in git(['ls-files', '--others', '--exclude-standard']):
    if is_test_code(p):
        print('U ' + p)
PYRESET
while IFS=' ' read -r kind path; do
  [ -z "$kind" ] && continue
  case "$kind" in
    T) git checkout HEAD -- "$path" 2>/dev/null || true ;;
    U) rm -f -- "$path" 2>/dev/null || true ;;
  esac
done < /tmp/reset_list.txt
TP=/chain/test.patch
{ grep -E '^(---|\+\+\+) [ab]/' $TP | sed -E 's@^(---|\+\+\+) [ab]/@@'; \
  grep -E '^rename (from|to) ' $TP | sed -E 's@^rename (from|to) @@'; } | sort -u > /tmp/v5_paths.txt
{ grep -E '^--- /dev/null' -A1 $TP | grep -E '^\+\+\+ b/' | sed 's@^+++ b/@@'; \
  grep -E '^rename to ' $TP | sed -E 's@^rename to @@'; } | sort -u > /tmp/v5_created.txt
while read -r p; do
  [ -z "$p" ] && continue
  if git ls-files --error-unmatch -- "$p" >/dev/null 2>&1; then
    git checkout HEAD -- "$p" 2>/dev/null || true
  elif grep -qxF "$p" /tmp/v5_created.txt; then
    rm -f -- "$p" 2>/dev/null || true
  fi
done < /tmp/v5_paths.txt
git diff --name-only HEAD -- > /tmp/v6_modified.txt
while read -r p; do
  case "$p" in
    *testdata/*|*test_data/*|*fixtures/*|*fixture/*|*mocks/*|*mock/*|*__snapshots__/*)
      git checkout HEAD -- "$p" 2>/dev/null || true ;;
  esac
done < /tmp/v6_modified.txt
if [ -f /chain/skip_testpatch ]; then
  echo "testpatch_skipped" >> /chain/out/apply.txt
elif ! git apply --whitespace=nowarn --binary $TP 2>/chain/out/testpatch.err; then
  echo "testpatch_failed" >> /chain/out/apply.txt
  exit 2
fi

for d in /chain/tasks/*/; do
  k=$(basename "$d")
  ARGS=$(head -n1 "$d/args.txt" | tr -d '\r')
  START=$(date +%s)
  if [ -n "$ARGS" ]; then
    timeout 2400 bash "$d/run_script.sh" "$ARGS" > /chain/out/$k.stdout 2> /chain/out/$k.stderr
  else
    timeout 2400 bash "$d/run_script.sh" > /chain/out/$k.stdout 2> /chain/out/$k.stderr
  fi
  echo "$k rc=$? secs=$(( $(date +%s) - START ))" >> /chain/out/runs.txt
  python3 "$d/parser.py" /chain/out/$k.stdout /chain/out/$k.stderr /chain/out/$k.json || echo '{"tests":[]}' > /chain/out/$k.json
done
exit 0
