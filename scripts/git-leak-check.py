#!/usr/bin/env python3
"""git-leak-check — Simple regex-based leak detection for staged changes."""

import subprocess
import sys
import re

# Patterns to look for
LEAK_PATTERNS = [
    r'client_secret\s*=\s*["\'][a-zA-Z0-9_\-\.]+["\']',
    r'access_token\s*=\s*["\'][a-zA-Z0-9_\-\.]+["\']',
    r'api_key\s*=\s*["\'][a-zA-Z0-9_\-\.]+["\']',
    r'refresh_token\s*=\s*["\'][a-zA-Z0-9_\-\.]+["\']',
    r'id_token\s*=\s*["\'][a-zA-Z0-9_\-\.]+["\']',
    r'sk-[a-zA-Z0-9]{20,}', # OpenAI keys
]

def get_staged_diff():
    cmd = ["git", "diff", "--cached", "--unified=0"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.stdout

def main():
    diff = get_staged_diff()
    leaks_found = []
    
    for line in diff.splitlines():
        if not line.startswith('+'):
            continue
        # Strip the '+' prefix
        content = line[1:]
        for pattern in LEAK_PATTERNS:
            if re.search(pattern, content, re.IGNORECASE):
                # Check if it's a known redacted value or placeholder
                if '"***"' in content or "'***'" in content:
                    continue
                leaks_found.append(line)
                break

    if leaks_found:
        print("CRITICAL: Potential secrets detected in staged changes!", file=sys.stderr)
        for leak in leaks_found:
            print(f"  {leak}", file=sys.stderr)
        print("\nPlease redact these secrets before committing.", file=sys.stderr)
        sys.exit(1)

    print("No secrets detected in staged changes.")
    sys.exit(0)

if __name__ == "__main__":
    main()
