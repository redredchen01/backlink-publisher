# Developer tooling targets — experimental, not part of the publishing pipeline.
# Prerequisite: pip install -e ".[dev-webwright]"
#
# Usage:
#   make scaffold PLATFORM=devto [LOGIN_URL=https://dev.to/enter]
#   make diagnose CHANNEL=velog

.PHONY: scaffold diagnose

scaffold:
ifndef PLATFORM
	$(error PLATFORM is required. Usage: make scaffold PLATFORM=devto [LOGIN_URL=https://...])
endif
	@python -c "import webwright" 2>/dev/null || \
		(echo "Error: webwright not installed. Run: pip install -e '.[dev-webwright]'" && exit 1)
	@python scripts/webwright_scaffold.py

diagnose:
ifndef CHANNEL
	$(error CHANNEL is required. Usage: make diagnose CHANNEL=velog)
endif
	@python -c "import webwright" 2>/dev/null || \
		(echo "Error: webwright not installed. Run: pip install -e '.[dev-webwright]'" && exit 1)
	@python scripts/webwright_diagnose.py
