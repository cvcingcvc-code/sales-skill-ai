"""Single source of truth for the released project version.

The repository is published as a tagged GitHub Release, so the version that the
running service reports must match the tag. Keep this the only place that
holds the number; release tooling and the health endpoint both read it.
"""

PROJECT_VERSION = "1.0.0-beta.1"
