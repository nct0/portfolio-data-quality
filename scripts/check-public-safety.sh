#!/usr/bin/env bash
# check-public-safety.sh - scan Git repositories for content that must never be published.
#
# Usage: check-public-safety.sh [--history] [REPO_PATH ...]
#
#   REPO_PATH   Repositories to scan. Default: the repository containing the current
#               directory, plus every initialized submodule listed in its .gitmodules.
#   --history   Also scan file contents, file names and commit messages of every commit
#               reachable from any ref. Mandatory before a repository becomes public.
#
# Checks: forbidden file types, secret and token patterns, credentials embedded in URLs,
# email addresses outside reserved example domains, commit identities that are not
# GitHub noreply addresses, and terms from a private denylist. Tracked and untracked
# (non-ignored) files are scanned. Findings are reported by location only; matched text
# is never printed.
#
# Private denylist: one term per line (personal email, legal name, employer or client
# names), read from $PUBLIC_SAFETY_DENYLIST or ~/.config/upwork-portfolio/denylist.txt.
# Keep that file outside every repository. Blank lines and lines starting with # are
# ignored.
#
# Exit status: 0 clean, 1 findings, 2 usage or environment error.

set -u

TAB=$(printf '\t')
history=0
repos=()
for arg in "$@"; do
  case "$arg" in
    --history) history=1 ;;
    -h|--help) sed -n '2,/^$/s/^# \{0,1\}//p' "$0"; exit 0 ;;
    -*) echo "unknown option: $arg" >&2; exit 2 ;;
    *) repos+=("$arg") ;;
  esac
done

if [ ${#repos[@]} -eq 0 ]; then
  top=$(git rev-parse --show-toplevel 2>/dev/null) || { echo "error: not inside a Git repository" >&2; exit 2; }
  repos=("$top")
  if [ -f "$top/.gitmodules" ]; then
    while read -r _ sub_path; do
      if [ -e "$top/$sub_path/.git" ]; then
        repos+=("$top/$sub_path")
      else
        echo "note: submodule $sub_path is not initialized and was skipped"
      fi
    done < <(git -C "$top" config -f .gitmodules --get-regexp '^submodule\..*\.path$')
  fi
fi

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

denylist=${PUBLIC_SAFETY_DENYLIST:-$HOME/.config/upwork-portfolio/denylist.txt}
: > "$work/deny"
if [ -f "$denylist" ]; then
  tr -d '\r' < "$denylist" | sed 's/[[:space:]]*$//' | grep -v -e '^[[:space:]]*$' -e '^[[:space:]]*#' > "$work/deny"
  echo "denylist: $(wc -l < "$work/deny" | tr -d ' ') private term(s) loaded"
else
  echo "denylist: not configured (recommended: $denylist)"
fi

# name, flags (i = case-insensitive, - = case-sensitive), extended regex (rest of line)
rules() {
  cat <<'RULES'
github-token - gh[pousr]_[A-Za-z0-9]{36,}
github-pat - github_pat_[A-Za-z0-9_]{50,}
aws-access-key - (A3T[A-Z0-9]|AKIA|ASIA)[A-Z0-9]{16}
google-api-key - AIza[0-9A-Za-z_-]{35}
google-oauth-token - ya29\.[0-9A-Za-z_-]{20,}
google-refresh-token - 1//0[0-9A-Za-z_-]{30,}
slack-token - xox[abposr]-[0-9A-Za-z-]{10,}
stripe-live-key - [rs]k_live_[0-9A-Za-z]{16,}
llm-api-key - (^|[^A-Za-z0-9])sk-(ant-|proj-)?[A-Za-z0-9_-]{32,}
private-key - -----BEGIN [A-Z ]*PRIVATE KEY-----
url-credentials - [a-z][a-z0-9+.-]*://[^/[:space:]:@]+:[^/[:space:]@]+@
conn-string-secret i ;[[:space:]]*(password|pwd)[[:space:]]*=[[:space:]]*[^;"'<>[:space:]$%{]{3,}
assigned-secret i (password|passwd|secret|api[_-]?key|access[_-]?token|client[_-]?secret)["']?[[:space:]]*[:=][[:space:]]*["'][^"'<>$%{[:space:]]{6,}["']
RULES
}

email_re='[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}'
# Reserved example domains (RFC 2606 / RFC 6761), GitHub noreply addresses, and the
# noreply address used in AI-assistant Co-Authored-By commit trailers.
allowed_email_re=':[^:@]*@(([A-Za-z0-9-]+\.)*example\.(com|org|net)|([A-Za-z0-9-]+\.)*users\.noreply\.github\.com|[A-Za-z0-9.-]*\.(test|example|invalid|localhost))$|:(noreply|git)@github\.com$|:noreply@anthropic\.com$'
forbidden_path_re='(^|/)\.env(\.[^/]*)?$|(^|/)\.clasprc\.json$|(^|/)(credentials|token|secrets|client_secret[^/]*|service[-_]account[^/]*)\.json$|\.(pem|key|pfx|p12|jks|keystore|ppk|kdbx|publishsettings)$|(^|/)id_(rsa|dsa|ecdsa|ed25519)$|\.(bak|mdf|ldf|ndf|trn)$|\.(sqlite|sqlite3|db)$|(^|/)\.(npmrc|netrc)$'
media_re='\.(png|jpe?g|gif|webp|bmp|tiff?|heic|mp4|mov|webm|pdf)$'
noreply_re='(@users\.noreply\.github\.com|^noreply@github\.com)$'

findings=0

# report RULE < locations: print up to 20 locations for a rule and count all of them.
report() {
  local rule=$1 n=0 loc
  while IFS= read -r loc; do
    n=$((n + 1))
    [ "$n" -le 20 ] && printf '  FAIL  %-20s %s\n' "$rule" "$loc"
  done
  [ "$n" -gt 20 ] && printf '  ....  %-20s %d more\n' "$rule" $((n - 20))
  findings=$((findings + n))
}

# search TARGET GREP_OPTION... : grep the given target, printing "location<TAB>text" lines.
#   tree     tracked and untracked (non-ignored) files in the working tree
#   history  every commit reachable from any ref
#   messages commit messages (materialized under $work/msg)
search() {
  local target=$1; shift
  case "$target" in
    tree)
      git -C "$repo" grep --untracked -I -n "$@" 2>/dev/null \
        | sed -E "s/^([^:]*:[0-9]+):/\1$TAB/" ;;
    history)
      git -C "$repo" rev-list --all | xargs git -C "$repo" grep -I -n "$@" 2>/dev/null \
        | sed -E "s/^([0-9a-f]{10})[0-9a-f]{30}:([^:]*:[0-9]+):/\1:\2$TAB/" ;;
    messages)
      (cd "$work/msg" && grep -r -I -n "$@" . 2>/dev/null) \
        | sed -E "s|^\./([0-9a-f]+):([0-9]+):|commit-message \1:\2$TAB|" ;;
  esac
}

scan_target() {
  local target=$1 name flags regex icase
  while read -r name flags regex; do
    icase=""; [ "$flags" = i ] && icase="-i"
    report "$name" < <(search "$target" -E $icase -e "$regex" | cut -f1 | sort -u)
  done < <(rules)

  report "email-address" < <(search "$target" -o -E -e "$email_re" \
    | tr '\t' ':' | grep -v -i -E "$allowed_email_re" | sed -E 's/:[^:]*$//' | sort -u)

  if [ -s "$work/deny" ]; then
    report "denylist-term" < <(search "$target" -i -F -f "$work/deny" | cut -f1 | sort -u)
  fi
}

check_paths() {  # check_paths LABEL < paths
  local label=$1
  sort -u > "$work/paths"
  report "forbidden-file$label" < <(grep -i -E "$forbidden_path_re" "$work/paths" | grep -v -i -E '\.(example|sample|template)$')
  if [ -s "$work/deny" ]; then
    report "denylist-filename$label" < <(grep -i -F -f "$work/deny" "$work/paths")
  fi
}

scan_repo() {
  repo=$1
  echo "== ${repo}"
  if ! git -C "$repo" rev-parse --git-dir >/dev/null 2>&1; then
    echo "  error: not a Git repository"; findings=$((findings + 1)); return
  fi
  local has_commits=0
  git -C "$repo" rev-parse --verify -q HEAD >/dev/null && has_commits=1

  check_paths "" < <(git -C "$repo" ls-files --cached --others --exclude-standard)
  scan_target tree

  if [ "$has_commits" -eq 1 ]; then
    # Commit identities: only GitHub noreply addresses may appear in history.
    report "commit-identity" < <(git -C "$repo" log --all --format='%h %ae%n%h %ce' \
      | grep -v -i -E "$noreply_re" | awk '{print "commit " $1 ": author/committer email is not a GitHub noreply address"}' | sort -u)
    if [ -s "$work/deny" ]; then
      report "denylist-identity" < <(git -C "$repo" log --all --format='%h %an%n%h %cn' \
        | grep -i -F -f "$work/deny" | awk '{print "commit " $1 ": author/committer name"}' | sort -u)
    fi
  fi

  if [ "$history" -eq 1 ] && [ "$has_commits" -eq 1 ]; then
    echo "  history: $(git -C "$repo" rev-list --all | wc -l | tr -d ' ') commit(s)"
    check_paths "(history)" < <(git -C "$repo" log --all --name-only --format= | sed '/^$/d')
    scan_target history
    rm -rf "$work/msg"; mkdir -p "$work/msg"
    local c
    for c in $(git -C "$repo" rev-list --all); do
      git -C "$repo" log -1 --format=%B "$c" > "$work/msg/${c:0:10}"
    done
    scan_target messages
  fi

  local media
  media=$(git -C "$repo" ls-files | grep -c -i -E "$media_re")
  [ "$media" -gt 0 ] && echo "  NOTE  $media media file(s) tracked: review manually for personal data and metadata"
  return 0
}

for r in "${repos[@]}"; do
  scan_repo "$r"
done

if [ "$findings" -gt 0 ]; then
  echo "== result: $findings finding(s)"
  exit 1
fi
echo "== result: clean"
