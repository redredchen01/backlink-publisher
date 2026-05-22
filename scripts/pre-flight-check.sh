#!/bin/bash
# Pre-planning CI Gate: ensures the codebase is healthy before brainstorming/planning.

echo "Running pre-flight tests..."
make -C .. test
if [ $? -ne 0 ]; then
    echo "Tests failed. Please fix them before starting a new planning session."
    exit 1
fi
echo "Tests passed. Proceeding with planning."
