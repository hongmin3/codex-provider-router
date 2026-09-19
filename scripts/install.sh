#!/bin/zsh
set -euo pipefail
project_dir=${0:A:h:h}

usage() {
  cat <<'EOF'
Usage: ./scripts/install.sh [--key-file PATH]

Options:
  --key-file PATH  Import a one-line DeepSeek API key into macOS Keychain.
  -h, --help       Show this help.

If --key-file is omitted, DEEPSEEK_KEY_FILE is used when set. Otherwise,
./deepseek-api-key.txt is imported automatically when that file exists.
The key file is never copied into the installed Router configuration.
EOF
}

key_file=${DEEPSEEK_KEY_FILE:-}
while (( $# > 0 )); do
  case "$1" in
    --key-file)
      if (( $# < 2 )); then
        print -u2 "Missing path after --key-file"
        usage >&2
        exit 2
      fi
      key_file=$2
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      print -u2 "Unknown option: $1"
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$key_file" && -f "$project_dir/deepseek-api-key.txt" ]]; then
  key_file="$project_dir/deepseek-api-key.txt"
fi

if [[ "$(uname -s)" != "Darwin" ]]; then
  print -u2 "This installer requires macOS (the Router stores its key in Keychain)."
  exit 1
fi
for required_command in curl python3 security; do
  if ! command -v "$required_command" >/dev/null 2>&1; then
    print -u2 "Required command not found: $required_command"
    exit 1
  fi
done

key_value=''
if [[ -n "$key_file" ]]; then
  key_file=${key_file:A}
  if [[ ! -f "$key_file" || -L "$key_file" ]]; then
    print -u2 "Key file must be a regular, non-symlink file: $key_file"
    exit 1
  fi
  if (( $(wc -c < "$key_file") > 4096 )); then
    print -u2 "Key file is unexpectedly large (maximum: 4096 bytes)."
    exit 1
  fi
  key_value=$(<"$key_file")
  if [[ -z "$key_value" || "$key_value" == *[[:space:]]* ]]; then
    print -u2 "Key file must contain exactly one non-empty API key without spaces."
    exit 1
  fi
  chmod 600 "$key_file" 2>/dev/null || true
fi

stamp=$(date +%Y%m%d-%H%M%S)
backup_dir="$HOME/.codex-backup/$stamp"
router_dir="$HOME/.codex/router"
config_dir="$HOME/.config/codex-router"
bin_dir="$HOME/.local/bin"
mkdir -p "$backup_dir" "$router_dir/logs" "$router_dir/fallback-state" "$bin_dir" "$config_dir"
chmod 700 "$backup_dir" "$router_dir" "$router_dir/logs" "$router_dir/fallback-state" "$bin_dir" "$config_dir"
[[ -f "$HOME/.codex/config.toml" ]] && cp -p "$HOME/.codex/config.toml" "$backup_dir/codex-config.toml"
[[ -f "$HOME/.codex/deepseek.config.toml" ]] && cp -p "$HOME/.codex/deepseek.config.toml" "$backup_dir/deepseek.config.toml"
[[ -f "$HOME/.zshrc" ]] && cp -p "$HOME/.zshrc" "$backup_dir/zshrc"
[[ -f "$HOME/.zprofile" ]] && cp -p "$HOME/.zprofile" "$backup_dir/zprofile"
[[ -f "$config_dir/config.toml" ]] && cp -p "$config_dir/config.toml" "$backup_dir/router-config.toml"
[[ -f "$config_dir/models.toml" ]] && cp -p "$config_dir/models.toml" "$backup_dir/router-models.toml"
[[ -f "$router_dir/codex_router.py" ]] && cp -p "$router_dir/codex_router.py" "$backup_dir/installed-codex-router.py"
[[ -f "$router_dir/model_router.py" ]] && cp -p "$router_dir/model_router.py" "$backup_dir/installed-model-router.py"
cp "$project_dir/src/codex_router.py" "$router_dir/codex_router.py"
cp "$project_dir/src/model_router.py" "$router_dir/model_router.py"
[[ -f "$config_dir/config.toml" ]] || cp "$project_dir/config/config.toml" "$config_dir/config.toml"
if ! grep -q '^\[model_router\]$' "$config_dir/config.toml"; then
  sed -n '/^\[model_router\]$/,$p' "$project_dir/config/config.toml" >> "$config_dir/config.toml"
fi
cp "$project_dir/config/models.toml" "$config_dir/models.toml"
cp "$project_dir/config/deepseek.config.toml" "$HOME/.codex/deepseek.config.toml"
chmod 700 "$router_dir/codex_router.py" "$router_dir/model_router.py"
chmod 600 "$config_dir/config.toml" "$config_dir/models.toml" "$HOME/.codex/deepseek.config.toml"
setup_tmp=$(mktemp)
trap 'rm -f "$setup_tmp"' EXIT
curl -LfsS 'https://cdn.deepseek.com/api-docs/codex-deepseek-setup-en.sh' -o "$setup_tmp"
awk '/<<'"'"'CODEX_MODELS_JSON'"'"'/{copy=1; next} /^CODEX_MODELS_JSON$/{if (copy) exit} copy{print}' "$setup_tmp" > "$router_dir/deepseek-models.json"
python3 -m json.tool "$router_dir/deepseek-models.json" >/dev/null
chmod 600 "$router_dir/deepseek-models.json"
if [[ -n "$key_value" ]]; then
  keychain_account=${USER:-$(id -un)}
  security add-generic-password -U -s codex-router-deepseek -a "$keychain_account" -w "$key_value" >/dev/null
  key_value=''
fi
ln -sfn "$router_dir/codex_router.py" "$bin_dir/codex"
ln -sfn "$router_dir/codex_router.py" "$bin_dir/codex-router"
if [[ ! -e "$bin_dir/deep" || ( -L "$bin_dir/deep" && "$(readlink "$bin_dir/deep")" == "$router_dir/codex_router.py" ) ]]; then
  ln -sfn "$router_dir/codex_router.py" "$bin_dir/deep"
else
  print -u2 "Not replacing existing command: $bin_dir/deep"
  exit 1
fi
if [[ ! -e "$bin_dir/ai" || ( -L "$bin_dir/ai" && "$(readlink "$bin_dir/ai")" == "$router_dir/codex_router.py" ) ]]; then
  ln -sfn "$router_dir/codex_router.py" "$bin_dir/ai"
else
  print -u2 "Not replacing existing command: $bin_dir/ai"
  exit 1
fi
shell_marker='# >>> codex-provider-router >>>'
if ! grep -Fq "$shell_marker" "$HOME/.zshrc" 2>/dev/null; then
  {
    printf '\n%s\n' "$shell_marker"
    printf 'export PATH="$HOME/.local/bin:$PATH"\n'
    printf '# <<< codex-provider-router <<<\n'
  } >> "$HOME/.zshrc"
fi
cp "$project_dir/scripts/uninstall.sh" "$router_dir/uninstall.sh"
chmod 700 "$router_dir/uninstall.sh"
printf '%s\n' "$backup_dir" > "$router_dir/install-backup-path"
chmod 600 "$router_dir/install-backup-path"
printf 'Installed. Backup: %s\n' "$backup_dir"
if [[ -n "$key_file" ]]; then
  printf 'DeepSeek key imported from %s into macOS Keychain.\n' "$key_file"
else
  printf 'DeepSeek key was not provided. Configure it later with: codex-router key set\n'
fi
printf 'Open a new shell or run: source ~/.zshrc\n'
