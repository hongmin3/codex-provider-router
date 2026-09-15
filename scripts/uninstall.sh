#!/bin/zsh
set -euo pipefail
router_dir="$HOME/.codex/router"
bin_dir="$HOME/.local/bin"
for link in "$bin_dir/codex" "$bin_dir/codex-router" "$bin_dir/ai"; do
  [[ -L "$link" && "$(readlink "$link")" == "$router_dir/codex_router.py" ]] && rm "$link"
done
if [[ -f "$HOME/.zshrc" ]]; then
  sed -i '' '/# >>> codex-provider-router >>>/,/# <<< codex-provider-router <<</d' "$HOME/.zshrc"
fi
rm -f "$HOME/.codex/deepseek.config.toml"
rm -f "$HOME/.config/codex-router/config.toml"
rm -f "$HOME/.config/codex-router/models.toml"
security delete-generic-password -s codex-router-deepseek -a "$USER" >/dev/null 2>&1 || true
archive="$HOME/.Trash/codex-router-$(date +%Y%m%d-%H%M%S)"
[[ -d "$router_dir" ]] && mv "$router_dir" "$archive"
printf 'Router removed. Original Codex remains at /opt/homebrew/bin/codex. Router state moved to %s\n' "$archive"
