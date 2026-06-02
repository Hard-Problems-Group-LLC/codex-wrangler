"""Project-wide constants for codex-wrangler."""

from pathlib import Path

from ._version import __version__

SCRIPT_NAME = "codex-wrangler"
SCRIPT_VERSION = __version__
SCHEMA_VERSION = 1
SCRIPT_MARKER = "managed by codex-wrangler"

CODEX_PACKAGE_NAME = "@openai/codex"
CODEX_CHANNELS = ("stable", "beta", "alpha")
DEFAULT_INSTALL_CODEX_CHANNEL = "stable"
DEFAULT_STABLE_CODEX_SELECTOR = "latest"
DEFAULT_PREVIEW_CODEX_SELECTOR = "__preview__"
PREVIEW_DIST_TAG_CANDIDATES = ("alpha", "beta")
DEFAULT_INSTALL_CODEX_SELECTOR = DEFAULT_STABLE_CODEX_SELECTOR

DEFAULT_LOCAL_DIR = ".codex-local"
DEFAULT_HOME_DIR = ".codex-home"
DEFAULT_LAUNCHER_RELATIVE_PATH = Path("bin") / "codex-local"
DEFAULT_README_FILENAME = "README-LOCAL-Start-Codex.md"
METADATA_FILENAME = ".codex-wrangler.json"
DEFAULT_NPM_TIMEOUT_SECONDS = 300
DEFAULT_NPM_INSTALL_LOGLEVEL = "http"
NPM_INSTALL_LOGLEVELS = (
    "silent",
    "error",
    "warn",
    "notice",
    "http",
    "timing",
    "info",
    "verbose",
    "silly",
)

GITIGNORE_BEGIN = "# BEGIN managed by codex-wrangler"
GITIGNORE_END = "# END managed by codex-wrangler"
README_MARKER = "<!-- managed by codex-wrangler -->"
