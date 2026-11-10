#!/usr/bin/env bash
# Writes a Markdown review of a saved plan to a file: a count, a table of each record that changes (what it is now and
# what it becomes), and the full plan, folded, as a coloured diff. The workflow puts it in the job summary and the PR
# comment, and raises a warning annotation when a plan destroys or replaces anything, so it shows on the run page that
# asks for approval.
#
#   plan-summary.sh <plan file> <environment> <Markdown file to write> [max characters of the full plan]
#
# Run from an initialised infra/cloudflare (terraform show needs the providers). Needs jq.
set -euo pipefail

plan_file=$1
environment=$2
out=$3
max_full=${4:-50000}

json=$(terraform show -json "$plan_file")
text=$(terraform show -no-color "$plan_file")

# One row per resource that changes. For DNS records the row says "TYPE name → content"; an update or a replacement
# lists each attribute that changes as "attribute: old → new" (sensitive values hidden; values only known after apply,
# like a new record's id, are left to the full plan). Cells are code spans, so a | or a backtick in a value can't break the table.
rows=$(jq -r '
  def cell: tostring | gsub("`"; "'"'"'") | gsub("\\|"; "\\|") | gsub("\n"; " ");
  def code: "`" + cell + "`";
  def act:
    if . == ["create"] then "🟢 create"
    elif . == ["delete"] then "🔴 destroy"
    elif . == ["update"] then "🟡 update"
    elif . == ["delete","create"] or . == ["create","delete"] then "🟠 replace"
    else join("/") end;
  def record(r): if r == null then "" else ((r.type // "") + " " + (r.name // "") + " → " + (r.content // "") | code) end;
  def changed:
    . as $c
    | (($c.before // {}) | keys) + (($c.after // {}) | keys) | unique
    | map(select(
        . as $k
        | $c.before[$k] != $c.after[$k]
        and ($c.after_unknown[$k] // false) != true
        and ($c.before[$k] != null or $c.after[$k] != null)))
    | map(. as $k
        | if ($c.before_sensitive[$k] // false) == true or ($c.after_sensitive[$k] // false) == true then
            ($k + ": (sensitive)" | code)
          else
            ($k + ": "
              + ($c.before[$k] | if . == null then "null" else tojson end)
              + " → "
              + ($c.after[$k] | if . == null then "null" else tojson end)
            | code)
          end)
    | join("<br>");
  [.resource_changes[]? | select(.change.actions != ["no-op"] and .change.actions != ["read"])]
  | sort_by((.change.actions | act | {"🔴 destroy": 0, "🟠 replace": 1, "🟡 update": 2, "🟢 create": 3}[.] // 4), .address)
  | .[]
  | .change as $c
  | ($c.actions | act) as $action
  | (if .type == "cloudflare_dns_record" then
       if $c.actions == ["create"] then record($c.after)
       elif $c.actions == ["delete"] then record($c.before)
       else record($c.before) + "<br>" + ($c | changed) end
     elif $c.actions == ["create"] or $c.actions == ["delete"] then ""
     else ($c | changed) end) as $details
  | "| \($action) | \(.address | code) | \($details) |"
' <<<"$json")

counts=$(jq -r '
  [.resource_changes[]?.change.actions] as $a
  | {
      add: ($a | map(select(. == ["create"] or . == ["delete","create"] or . == ["create","delete"])) | length),
      change: ($a | map(select(. == ["update"])) | length),
      destroy: ($a | map(select(. == ["delete"] or . == ["delete","create"] or . == ["create","delete"])) | length),
      drift: ([.resource_drift[]?] | length)
    }
  | "\(.add) \(.change) \(.destroy) \(.drift)"
' <<<"$json")
read -r add change destroy drift <<<"$counts"

if [ "$destroy" -gt 0 ]; then
  echo "::warning title=Cloudflare DNS plan (${environment})::${destroy} record(s) will be destroyed or replaced. Check the table in the run summary before approving."
fi

# Terraform's markers moved to the start of the line, so GitHub's diff highlighting colours them: + green, - red, and
# ~ (update in place) and -/+ (replace) as !, orange.
full=$(sed -E \
  -e 's/^([[:space:]]*)(-\/\+|\+\/-)/!\1  /' \
  -e 's/^([[:space:]]*)~/!\1/' \
  -e 's/^([[:space:]]*)([-+])/\2\1/' <<<"$text")
if [ "${#full}" -gt "$max_full" ]; then
  full="${full:0:$max_full}
... (cut here; the whole plan is in the log of the Plan step)"
fi

{
  echo "### Cloudflare DNS plan (${environment})"
  echo
  if [ -z "$rows" ]; then
    echo "**No changes.** The records match the configuration."
  else
    echo "**${add} to add, ${change} to change, ${destroy} to destroy.**"
    echo
    echo "| Action | Resource | Details |"
    echo "| --- | --- | --- |"
    echo "$rows"
  fi
  if [ "$drift" -gt 0 ]; then
    echo
    echo "⚠️ ${drift} resource(s) were changed outside Terraform since the last apply (the full plan has the details)."
  fi

  echo
  echo "<details><summary>Full plan</summary>"
  echo
  echo '```diff'
  echo "$full"
  echo '```'
  echo
  echo "</details>"
} >"$out"
