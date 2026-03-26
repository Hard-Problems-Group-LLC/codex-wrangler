"""Project-wide constants for codex-wrangler."""

from pathlib import Path

from ._version import __version__


SCRIPT_NAME = "codex-wrangler"
SCRIPT_VERSION = __version__
SCHEMA_VERSION = 1
SCRIPT_MARKER = "managed by codex-wrangler"

DEFAULT_STABLE_CODEX_VERSION = "0.116.0"
DEFAULT_ALPHA_CODEX_VERSION = "0.117.0-alpha.19"
DEFAULT_INSTALL_CODEX_VERSION = DEFAULT_ALPHA_CODEX_VERSION

DEFAULT_LOCAL_DIR = ".codex-local"
DEFAULT_HOME_DIR = ".codex-home"
DEFAULT_LAUNCHER_RELATIVE_PATH = Path("bin") / "codex-local"
DEFAULT_README_FILENAME = "README-LOCAL-Start-Codex.md"
METADATA_FILENAME = ".codex-wrangler.json"

GITIGNORE_BEGIN = "# BEGIN managed by codex-wrangler"
GITIGNORE_END = "# END managed by codex-wrangler"
README_MARKER = "<!-- managed by codex-wrangler -->"
