"""Version + git sha for ce_get_health and tool.call telemetry."""
import os

SPEC_VERSION = "1.0.0-rc4"
SERVER_VERSION = "1.0.0"

GIT_SHA = (os.environ.get("VERCEL_GIT_COMMIT_SHA") or "local")[:7]
