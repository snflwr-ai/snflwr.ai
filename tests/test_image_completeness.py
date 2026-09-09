"""Every module production code imports must actually be in the container.

THIS GUARDS A CLASS, NOT A CASE. Three separate instances of the same failure
were found in one week, each with the same shape: code exists in the repository,
is absent from the built image, and the resulting error is swallowed by a broad
handler, so the system reports healthy while a feature silently does nothing.

  langfuse             in requirements.txt, absent from requirements.lock, and
                       the Dockerfile installs only from the lock. Tracing never
                       ran; the ImportError was caught alongside bad-credential
                       errors and logged as one generic warning.

  compliance_screen    written under evals/, which the Dockerfile does not copy.
                       The runtime S9051B check would have been inert in
                       production while every test passed locally.

  backup_database.py   imported by tasks/background_tasks.py and executed by the
                       nightly backup sidecar as
                       `python /app/scripts/backup_database.py backup`, but the
                       Dockerfile copies only scripts/owui_connect.py. The
                       sidecar's `|| echo` swallows the missing file and its
                       heartbeat keeps the healthcheck green.

Unit tests cannot catch any of these, because the test runner has the whole
repository on its path. The only way to see them is to compare what production
code reaches for against what the image is built with — which is what this does.

Deliberately static: it parses the Dockerfile rather than building an image, so
it runs in the ordinary unit suite in under a second and cannot be skipped for
want of a Docker daemon.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "docker" / "Dockerfile"

# Packages whose code serves requests. Anything these import at runtime has to
# exist in the image.
PRODUCTION_TREES = (
    "api",
    "core",
    "safety",
    "storage",
    "utils",
    "tasks",
    "database",
    "models",
)

_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+(?P<from>[A-Za-z_][\w.]*)|import\s+(?P<import>[A-Za-z_][\w.]*))",
    re.MULTILINE,
)


def _dockerfile_text() -> str:
    return DOCKERFILE.read_text()


def _copied_paths() -> set:
    """Paths the Dockerfile actually COPYs, from COPY lines only.

    Comments are excluded on purpose: `backup_database.py` is *named* in a
    comment on the rclone install step, which is exactly how a careless grep
    concludes it ships.
    """
    copied = set()
    for line in _dockerfile_text().splitlines():
        stripped = line.strip()
        if not stripped.upper().startswith("COPY"):
            continue
        # drop `COPY`, any --flags, and the final destination argument
        parts = [p for p in stripped.split()[1:] if not p.startswith("--")]
        for source in parts[:-1]:
            copied.add(source.rstrip("/"))
    return copied


def _is_shipped(module_path: str, copied: set) -> bool:
    """True if `a/b/c.py` is covered by a copied file or a copied directory."""
    if module_path in copied:
        return True
    parts = module_path.split("/")
    for i in range(1, len(parts)):
        if "/".join(parts[:i]) in copied:
            return True
    return False


def _production_imports() -> set:
    """Top-level modules imported anywhere under the production trees."""
    found = set()
    for tree in PRODUCTION_TREES:
        for path in (ROOT / tree).rglob("*.py"):
            for match in _IMPORT_RE.finditer(path.read_text(encoding="utf-8")):
                name = match.group("from") or match.group("import")
                if name:
                    found.add(name)
    return found


# ---------------------------------------------------------------------------
# the guard
# ---------------------------------------------------------------------------


def test_dockerfile_is_parseable_and_copies_something():
    """Guard against the guard passing because parsing returned nothing."""
    copied = _copied_paths()
    assert len(copied) > 5, f"only parsed {copied} from the Dockerfile — parser bug?"
    assert "api/" in copied or "api" in copied


def test_every_first_party_module_production_imports_is_in_the_image():
    """The class-level check.

    For each first-party top-level package that production code imports, assert
    the Dockerfile copies it. Third-party packages are out of scope here — those
    are covered by the requirements/lock containment check in
    tests/test_lock_sync.py.
    """
    copied = _copied_paths()
    first_party = {
        name.split(".")[0]
        for name in _production_imports()
        if (ROOT / name / "__init__.py").is_file() or (ROOT / f"{name}.py").is_file()
    }

    missing = []
    for name in sorted(first_party):
        # Resolve the way Python does: a directory only shadows a same-named
        # module when it is a real package. The repo has BOTH `config.py` and a
        # `config/` data directory with no __init__.py — treating the directory
        # as the import target reports a false drift on a module that is copied.
        is_package = (ROOT / name / "__init__.py").is_file()
        candidate = name if is_package else f"{name}.py"
        if not (ROOT / candidate).exists():
            candidate = name
        if not _is_shipped(candidate, copied):
            missing.append(name)

    assert not missing, (
        "production code imports these, but docker/Dockerfile does not copy them "
        f"into the image: {missing}. They would raise at runtime — and if the "
        "import sits inside a broad `except`, the feature would silently do "
        "nothing while the container reported healthy."
    )


def test_submodules_imported_from_partially_copied_packages_are_shipped():
    """`scripts/` is copied file-by-file rather than wholesale, so a package
    being 'present' is not enough — the specific module has to be there.

    This is the check that catches backup_database.py: `scripts/owui_connect.py`
    is copied, which makes `scripts` look shipped, while the module production
    code actually imports is absent.
    """
    copied = _copied_paths()
    partial = {
        path.split("/")[0]
        for path in copied
        if "/" in path and path.endswith(".py")
    }

    missing = []
    for tree in PRODUCTION_TREES:
        for path in (ROOT / tree).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for package in partial:
                for match in re.finditer(
                    rf"(?:from|import)\s+{re.escape(package)}\.(\w+)", text
                ):
                    module = f"{package}/{match.group(1)}.py"
                    if not _is_shipped(module, copied):
                        missing.append(f"{module} (imported by {path.relative_to(ROOT)})")

    assert not missing, (
        "these modules are imported by production code but are not copied into "
        f"the image: {sorted(set(missing))}"
    )


@pytest.mark.parametrize(
    "script",
    [
        # Executed directly by docker/compose/docker-compose.home.yml's
        # backup-cron sidecar. Its `|| echo` swallows a missing file, and the
        # heartbeat keeps the healthcheck green, so absence is invisible.
        "scripts/backup_database.py",
        # Seeds the proxy credential and the required disclosure banner.
        "scripts/owui_connect.py",
    ],
)
def test_scripts_invoked_by_compose_are_in_the_image(script):
    assert _is_shipped(script, _copied_paths()), (
        f"{script} is run inside the container but is not copied into the image"
    )


def test_compose_referenced_app_scripts_are_shipped():
    """Whatever the compose files execute as /app/scripts/... must be present.

    Derived from the compose files rather than hardcoded, so adding a new
    sidecar command is covered automatically.
    """
    copied = _copied_paths()
    missing = []
    for compose in (ROOT / "docker" / "compose").rglob("*.yml"):
        for match in re.finditer(r"/app/(scripts/[\w/]+\.py)", compose.read_text()):
            if not _is_shipped(match.group(1), copied):
                missing.append(f"{match.group(1)} (run by {compose.name})")
    assert not missing, f"compose runs these, but they are not in the image: {missing}"
