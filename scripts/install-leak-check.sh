#!/bin/bash
# Install git-leak-check as a pre-commit hook.

HOOK_PATH=".git/hooks/pre-commit"

if [ ! -d ".git" ]; then
    echo "Error: Not a git repository."
    exit 1
fi

cat >> "$HOOK_PATH" <<EOF

# git-leak-check hook
./scripts/git-leak-check.py
if [ \$? -ne 0 ]; then
    echo "Pre-commit hook failed: git-leak-check detected secrets."
    exit 1
fi
EOF

chmod +x "$HOOK_PATH"
echo "git-leak-check pre-commit hook installed."
