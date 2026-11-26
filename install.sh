#!/usr/bin/env bash
# curl -fsSL https://raw.githubusercontent.com/19Naveen/Roastline/main/install.sh | bash
set -euo pipefail

VERSION=1.5.0
REPO=19Naveen/Roastline

note() { printf '  [%s] %s\n' "$1" "$2"; }
fail() { note FAIL "$1" >&2; exit 1; }

if [[ -t 1 && ! ${NO_COLOR+x} ]]; then
  printf '\033[38;5;208m'
fi
cat <<'BANNER'

         (  )  (  )
          ) (   ) (
        .-----------.
        | ROASTLINE |
        '-----------'
        Your statusline. Lightly toasted.

BANNER
if [[ -t 1 && ! ${NO_COLOR+x} ]]; then
  printf '\033[0m'
fi

py=
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 \
    && "$candidate" -I -c 'import sys; sys.exit(sys.version_info < (3, 8))' </dev/null 2>/dev/null; then
    py=$candidate
    break
  fi
done
[[ -n $py ]] || fail "Roastline needs Python 3.8 or newer."

ref=main
if [[ ${1:-} == --ref ]]; then
  [[ $# -ge 2 ]] || fail "Pass a Git commit SHA or release tag after --ref."
  ref=$2
  shift 2
fi
if [[ $ref == main ]]; then
  archive_ref=refs/heads/main
elif [[ $ref =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  archive_ref=refs/tags/$ref
elif [[ $ref =~ ^[0-9a-f]{40}$ ]]; then
  archive_ref=$ref
else
  fail "Unsupported ref. Use main, a full commit SHA, or a version tag."
fi

source_dir=${ROASTLINE_SRC:-}
source_file=${BASH_SOURCE[0]:-}
if [[ -z $source_dir && -n $source_file ]]; then
  local_dir=$(cd -- "$(dirname -- "$source_file")" && pwd -P)
  if [[ -f $local_dir/install.py && -f $local_dir/src/roastline.py ]]; then
    source_dir=$local_dir
  fi
fi

if [[ -z $source_dir ]]; then
  command -v curl >/dev/null 2>&1 || fail "curl is required to download Roastline."
  note '..' "Downloading Roastline v$VERSION ($ref)"
  stage=$(mktemp -d)
  trap 'rm -rf -- "$stage"' EXIT
  curl -fsSL --proto '=https' --connect-timeout 10 --max-time 60 \
    "https://codeload.github.com/$REPO/tar.gz/$archive_ref" -o "$stage/bundle.tar.gz" \
    || fail "Could not download $ref. Check repository access, or use a local checkout."
  "$py" -I - "$stage" "$VERSION" <<'PY' || fail "Could not validate the release bundle. No Claude settings were changed."
import sys
from pathlib import Path
import tarfile

stage = Path(sys.argv[1])
expected = ("VERSION=" + sys.argv[2]).encode()
files = (
    "install.py", "config.default.json", "skills/roastline/SKILL.md",
    "src/roastline.py", "src/hook.py", "src/roasts/sfw.json", "src/roasts/nsfw.json",
    "tests/fixtures/statusline_input_sample.json",
)
with tarfile.open(stage / "bundle.tar.gz", "r:gz") as archive:
    top = archive.next()
    if top is None or not top.isdir():
        raise ValueError("archive root missing")

    def contents(name):
        entry = archive.getmember(top.name + "/" + name)
        if entry.type not in (tarfile.REGTYPE, tarfile.AREGTYPE) or entry.size > 256_000:
            raise ValueError("expected a small, regular bundle file")
        with archive.extractfile(entry) as stream:
            return stream.read()

    if expected not in contents("install.sh").splitlines():
        raise ValueError("bundle version doesn't match the bootstrap")
    for name in files:
        path = stage / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents(name))
PY
  source_dir=$stage
  note OK "Release bundle ready"
else
  note OK "Using the local Roastline bundle"
fi

[[ -f $source_dir/install.py ]] || fail "No install.py in the source bundle."
# Bash may still have unread script bytes on stdin. Only the Python installer
# opens the controlling terminal for the tone choice; headless mode never does.
ROASTLINE_BOOTSTRAPPED=1 "$py" -I "$source_dir/install.py" "$@" </dev/null
