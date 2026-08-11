#!/usr/bin/env bash
# Run Claude Code against Charm Hyper (no Anthropic subscription needed).
# Source this file or copy the exports into your shell.
# Usage: source scripts/claude-charm.sh && claude
#
# Requires: HERMES_CUSTOM_HYPER_CHARM_LAND_API_KEY (or set CHARM_KEY)
set -euo pipefail
KEY="${HERMES_CUSTOM_HYPER_CHARM_LAND_API_KEY:-${CHARM_KEY:-}}"
if [ -z "$KEY" ]; then
  echo "error: set HERMES_CUSTOM_HYPER_CHARM_LAND_API_KEY or CHARM_KEY" >&2
  return 1
fi
export ANTHROPIC_BASE_URL="https://hyper.charm.land"
export ANTHROPIC_AUTH_TOKEN="$KEY"
export ANTHROPIC_MODEL="glm-5.2"
export ANTHROPIC_SMALL_FAST_MODEL="glm-5.2"
export CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT=1
echo "claude -> Charm Hyper (glm-5.2)"
