#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

skills_dir="skills"
failures=0
skills_checked=0
evals_checked=0
eval_ids=()
eval_files=()

fail() {
  echo "FAIL $1"
  echo "  $2"
  failures=$((failures + 1))
}

frontmatter_value() {
  local frontmatter="$1"
  local key="$2"
  printf '%s\n' "$frontmatter" \
    | awk -F': *' -v key="$key" '$1 == key {sub(/^[^:]*:[[:space:]]*/, ""); print; exit}' \
    | sed 's/^"//; s/"$//; s/^'\''//; s/'\''$//'
}

frontmatter_nested_inline_value() {
  local frontmatter="$1"
  local parent="$2"
  local key="$3"
  printf '%s\n' "$frontmatter" \
    | awk -v parent="$parent" -v key="$key" '
        $0 == parent ":" {inside_parent=1; next}
        inside_parent && /^[^[:space:]]/ {exit}
        inside_parent && $0 ~ ("^  " key ":[[:space:]]*") {
          value=$0
          sub("^  " key ":[[:space:]]*", "", value)
          print value
          exit
        }
      ' \
    | sed 's/^"//; s/"$//; s/^'\''//; s/'\''$//'
}

yaml_inline_value() {
  local file="$1"
  local key="$2"
  awk -v key="$key" '$0 ~ ("^[[:space:]]*" key ":[[:space:]]*") {
      value=$0
      sub("^[[:space:]]*" key ":[[:space:]]*", "", value)
      print value
      exit
    }' "$file" \
    | sed 's/^"//; s/"$//; s/^'\''//; s/'\''$//'
}

if [[ ! -d "$skills_dir" ]]; then
  echo "Missing skills directory: $skills_dir" >&2
  exit 1
fi

shopt -s nullglob
skill_dirs=("$skills_dir"/*/)
if [[ ${#skill_dirs[@]} -eq 0 ]]; then
  echo "No skill directories found in $skills_dir" >&2
  exit 1
fi

for skill_dir in "${skill_dirs[@]}"; do
  skill_name="$(basename "$skill_dir")"
  skill_file="${skill_dir}SKILL.md"
  skills_checked=$((skills_checked + 1))

  if [[ ! -f "$skill_file" ]]; then
    fail "$skill_name" "Missing SKILL.md"
    continue
  fi

  if [[ "$(sed -n '1p' "$skill_file")" != "---" ]]; then
    fail "$skill_file" "Frontmatter must start on the first line"
    continue
  fi

  frontmatter_end="$(awk 'NR > 1 && $0 == "---" {print NR; exit}' "$skill_file")"
  if [[ -z "$frontmatter_end" ]]; then
    fail "$skill_file" "Frontmatter is not closed"
    continue
  fi

  frontmatter="$(sed -n "2,$((frontmatter_end - 1))p" "$skill_file")"
  declared_name="$(frontmatter_value "$frontmatter" "name")"
  description="$(frontmatter_value "$frontmatter" "description")"
  disable_model_invocation="$(frontmatter_value "$frontmatter" "disable-model-invocation")"
  author="$(frontmatter_nested_inline_value "$frontmatter" "metadata" "author")"

  if [[ -z "$declared_name" ]]; then
    fail "$skill_file" "Frontmatter is missing name"
  elif [[ "$declared_name" != "$skill_name" ]]; then
    fail "$skill_file" "Frontmatter name '$declared_name' does not match directory '$skill_name'"
  elif ! [[ "$declared_name" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]]; then
    fail "$skill_file" "Name must use lowercase hyphenated words"
  fi

  if [[ -z "$description" \
    || "$description" == \|* \
    || "$description" == \>* \
    || "$description" == \#* \
    || "$description" == "~" \
    || "$description" =~ ^[Nn][Uu][Ll][Ll]$ ]]; then
    fail "$skill_file" "Frontmatter requires a non-empty inline description"
  fi

  if [[ -z "$author" ]]; then
    fail "$skill_file" "Frontmatter requires metadata.author as a non-empty inline scalar"
  fi

  if printf '%s\n' "$frontmatter" | grep -Eq '^[[:space:]]*version:'; then
    fail "$skill_file" "Skill versions belong to repository releases, not skill frontmatter"
  fi

  openai_metadata="${skill_dir}agents/openai.yaml"
  if [[ ! -f "$openai_metadata" ]]; then
    fail "$skill_name" "Missing agents/openai.yaml"
  else
    for icon_key in icon_small icon_large; do
      icon_path="$(yaml_inline_value "$openai_metadata" "$icon_key")"
      if [[ -z "$icon_path" ]]; then
        fail "$openai_metadata" "Missing interface.$icon_key"
      elif [[ ! -f "${skill_dir}${icon_path#./}" ]]; then
        fail "$openai_metadata" "$icon_key does not resolve to a file: $icon_path"
      fi
    done

    default_prompt="$(yaml_inline_value "$openai_metadata" "default_prompt")"
    if [[ "$default_prompt" != *"\$$skill_name"* ]]; then
      fail "$openai_metadata" "default_prompt must invoke \$$skill_name"
    fi

    if [[ "$disable_model_invocation" == "true" ]]; then
      if ! grep -Eq '^[[:space:]]*allow_implicit_invocation:[[:space:]]*false[[:space:]]*$' "$openai_metadata"; then
        fail "$openai_metadata" "Command-only skills must disable implicit invocation"
      fi
    elif grep -Eq '^[[:space:]]*allow_implicit_invocation:[[:space:]]*false[[:space:]]*$' "$openai_metadata"; then
      fail "$openai_metadata" "Implicit invocation is disabled but SKILL.md does not set disable-model-invocation: true"
    fi
  fi

  for script in "${skill_dir}"scripts/*.py; do
    if grep -q -- '--selftest' "$script" && ! python3 "$script" --selftest >/dev/null; then
      fail "$script" "Self-test failed"
    fi
  done

  eval_dir="${skill_dir}evals"
  if [[ -d "$eval_dir" ]]; then
    while IFS= read -r -d '' eval_file; do
      evals_checked=$((evals_checked + 1))
      eval_id="$(sed -n 's/^- ID: `\([^`]*\)`.*/\1/p' "$eval_file" | head -n 1)"

      if [[ -z "$eval_id" ]]; then
        fail "$eval_file" "Missing stable ID"
      else
        expected_file="${eval_id}.md"
        if [[ "$(basename "$eval_file")" != "$expected_file" ]]; then
          fail "$eval_file" "Filename must match ID: $expected_file"
        fi
        duplicate_file=""
        for index in "${!eval_ids[@]}"; do
          if [[ "${eval_ids[$index]}" == "$eval_id" ]]; then
            duplicate_file="${eval_files[$index]}"
            break
          fi
        done
        if [[ -n "$duplicate_file" ]]; then
          fail "$eval_file" "Duplicate eval ID also used by $duplicate_file"
        else
          eval_ids+=("$eval_id")
          eval_files+=("$eval_file")
        fi
      fi

      for heading in "Запрос пользователя" "Входной текст" "Должно измениться" "Должно сохраниться" "Запрещено"; do
        if ! grep -Fqx "## $heading" "$eval_file"; then
          fail "$eval_file" "Missing section: ## $heading"
        fi
      done
    done < <(find "$eval_dir" -mindepth 2 -maxdepth 2 -type f -name '*.md' -print0)
  fi
done

if ! python3 scripts/validate-links.py; then
  failures=$((failures + 1))
fi

if ! python3 scripts/validate-marketplaces.py; then
  failures=$((failures + 1))
fi

echo
echo "Summary: $skills_checked skills checked, $evals_checked eval cases checked, $failures failures"

if [[ $failures -gt 0 ]]; then
  exit 1
fi
