#!/bin/sh
# deploy/release/validate_digest_uri.sh
#
# Strict POSIX-compatible OCI @sha256 digest URI validator (P4-02 / F-P4-REL-02).
# Uses POSIX ERE character classes (`[:space:]`, `[:print:]`) under `LC_ALL=C` — never `\s`,
# which in POSIX bracket expressions `[^@\s]` treats `s` literally and breaks `crm-cskh`.
#
# Accepts:
#   <registry-host>[:port]/<namespace>/<repo>[:tag]@sha256:<64-lowercase-hex>
# Rejects:
#   - Empty strings, spaces, tabs, CR/LF, multi-line strings, non-printable ASCII
#   - Floating tags without `@sha256:<64-hex>` (`:latest`, `:0.0.1`, bare repo)
#   - Placeholder/sentinel values (`BLOCKED`, `ACTUAL_DIGEST`, `<...>`, all-zero sha256)
#   - Uppercase hex, wrong digest length, non-sha256 algorithms, multiple `@` symbols

set -eu
LC_ALL=C
export LC_ALL

if [ "$#" -ne 1 ]; then
  echo "ERROR: usage: validate_digest_uri.sh <registry>/<repo>@sha256:<64-hex>" >&2
  exit 2
fi

URI="$1"

if [ -z "${URI}" ]; then
  echo "ERROR: empty image digest URI" >&2
  exit 1
fi

# Reject embedded newlines / CR before line-oriented tools run (POSIX literal newline)
NL='
'
CR="$(printf '\r')"
case "${URI}" in
  *"${NL}"* | *"${CR}"*)
    echo "ERROR: newline/CR forbidden in image digest URI" >&2
    exit 1
    ;;
esac

# Reject any POSIX whitespace, control chars, angle-bracket placeholders, or all-zero digest
ZERO_SUFFIX="@sha256:0000000000000000000000000000000000000000000000000000000000000000"
case "${URI}" in
  *[[:space:]]* | *[![:print:]]*)
    echo "ERROR: whitespace or non-printable character forbidden in image digest URI" >&2
    exit 1
    ;;
  *"<"* | *">"*)
    echo "ERROR: angle-bracket placeholder forbidden in image digest URI" >&2
    exit 1
    ;;
  *"${ZERO_SUFFIX}")
    echo "ERROR: all-zero placeholder digest forbidden" >&2
    exit 1
    ;;
esac

if printf '%s\n' "${URI}" | grep -Eiq '(blocked|actual_digest)'; then
  echo "ERROR: BLOCKED or placeholder token forbidden in image digest URI" >&2
  exit 1
fi

if ! printf '%s\n' "${URI}" | grep -Eq '^[^/@[:space:]:]+(:[0-9]+)?(/[^/@[:space:]:]+)+(:[A-Za-z0-9._-]+)?@sha256:[0-9a-f]{64}$'; then
  echo "ERROR: invalid OCI digest URI '${URI}' (expected <registry>/<repo>@sha256:<64-lowercase-hex>)" >&2
  exit 1
fi
