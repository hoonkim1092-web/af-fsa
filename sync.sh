#!/usr/bin/env bash
# sync.sh — bash equivalent of sync.cmd + sync_easy.ps1
# Usage: ./sync.sh [up|down] [git|db] [target] [agent]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"

ACTION="${1:-up}"
BACKEND="${2:-git}"
TARGET="${3:-all}"
AGENT="${4:-}"

to_mode() {
    if [[ "$1" == "up" ]]; then echo "push"; else echo "pull"; fi
}

to_project_args() {
    local raw="${1:-all}"
    local key="${raw,,}"  # lowercase
    if [[ "$key" == "all" ]]; then
        echo "multi:logi-mind-v22,agent-factory,@repo"
    else
        local count
        count=$(echo "$raw" | tr ',' '\n' | sed '/^$/d' | sort -u | wc -l)
        if [[ "$count" -gt 1 ]]; then
            echo "multi:$raw"
        else
            echo "single:$raw"
        fi
    fi
}

MODE=$(to_mode "$ACTION")
PROJ=$(to_project_args "$TARGET")
PROJ_TYPE="${PROJ%%:*}"
PROJ_VALUE="${PROJ#*:}"

DB_SCRIPT="$SCRIPT_DIR/scripts/project_context_sync.py"
RESUME_SCRIPT="$SCRIPT_DIR/scripts/write_resume_brief.py"
GIT_SCRIPT="$SCRIPT_DIR/scripts/project_context_git_sync.ps1"

if [[ "$BACKEND" == "git" ]]; then
    # git backend still needs PowerShell
    if [[ "$PROJ_TYPE" == "multi" ]]; then
        if [[ -n "$AGENT" ]]; then
            powershell.exe -ExecutionPolicy Bypass -File "$GIT_SCRIPT" -Mode "$MODE" -Projects "$PROJ_VALUE" -Agent "$AGENT"
        else
            powershell.exe -ExecutionPolicy Bypass -File "$GIT_SCRIPT" -Mode "$MODE" -Projects "$PROJ_VALUE"
        fi
    else
        if [[ -n "$AGENT" ]]; then
            powershell.exe -ExecutionPolicy Bypass -File "$GIT_SCRIPT" -Mode "$MODE" -Project "$PROJ_VALUE" -Agent "$AGENT"
        else
            powershell.exe -ExecutionPolicy Bypass -File "$GIT_SCRIPT" -Mode "$MODE" -Project "$PROJ_VALUE"
        fi
    fi
    exit $?
fi

# db backend — pure python, works natively in bash
cd "$REPO_ROOT"

if [[ "$MODE" == "push" ]]; then
    if [[ "$PROJ_TYPE" == "multi" ]]; then
        python "$RESUME_SCRIPT" --projects "$PROJ_VALUE" --trigger sync_push
    else
        python "$RESUME_SCRIPT" --project "$PROJ_VALUE" --trigger sync_push
    fi
fi

if [[ "$PROJ_TYPE" == "multi" ]]; then
    if [[ -n "$AGENT" ]]; then
        python "$DB_SCRIPT" --projects "$PROJ_VALUE" --mode "$MODE" --agent "$AGENT"
    else
        python "$DB_SCRIPT" --projects "$PROJ_VALUE" --mode "$MODE"
    fi
else
    if [[ -n "$AGENT" ]]; then
        python "$DB_SCRIPT" --project "$PROJ_VALUE" --mode "$MODE" --agent "$AGENT"
    else
        python "$DB_SCRIPT" --project "$PROJ_VALUE" --mode "$MODE"
    fi
fi

# Global user sync
GLOBAL_USER_KEY="${AGENT_GLOBAL_USER_KEY:-}"
if [[ -z "$GLOBAL_USER_KEY" && -f "$REPO_ROOT/.env" ]]; then
    GLOBAL_USER_KEY=$(grep -E '^\s*AGENT_GLOBAL_USER_KEY\s*=' "$REPO_ROOT/.env" | head -1 | sed "s/.*=\s*//" | tr -d "\"'" | xargs)
fi

if [[ -n "$GLOBAL_USER_KEY" ]]; then
    echo "[SYNC GLOBAL] syncing global profile for user_key=$GLOBAL_USER_KEY (mode=$MODE)..."
    python "$DB_SCRIPT" --mode "$MODE" --scope global --user-key "$GLOBAL_USER_KEY"
fi
