"""Pure validation for native storage fixture roots under external evidence."""
from pathlib import Path
import re
import stat

ARTIFACT_ROOT = Path("/home/dflanag3/spatial-artifacts")
ALLOWED_HOSTS = {"sb16", "sb16-2"}
EVIDENCE_ROOT_NAME = "spatial_security_evidence"
METHOD_ROOT = re.compile(r"test_native_storage_[A-Za-z0-9_]+\Z")


def _reject_symlink_components(path: Path) -> None:
    for component in reversed((path, *path.parents)):
        if component.is_symlink():
            raise ValueError(f"fixture path contains symlink: {component}")


def _absolute_normalized(value: str | Path, label: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{label} must be an absolute normalized path")
    _reject_symlink_components(path)
    return path


def validate_fixture_root(hostname: str, evidence_root: str | Path,
                          requested_root: str | Path, owner_uid: int,
                          evidence_owner_uid: int | None = None) -> Path:
    """Validate the caller-selected evidence root and its method directory."""
    if hostname not in ALLOWED_HOSTS:
        raise ValueError(f"storage fixture is not permitted on host {hostname!r}")
    raw_evidence = _absolute_normalized(evidence_root, "evidence root")
    raw_root = _absolute_normalized(requested_root, "fixture root")
    try:
        artifact_root = ARTIFACT_ROOT.resolve(strict=True)
        evidence = raw_evidence.resolve(strict=True)
        root = raw_root.resolve(strict=True)
    except OSError as error:
        raise ValueError(f"fixture path does not exist: {error}") from error
    if not artifact_root.is_dir() or not evidence.is_dir() or not root.is_dir():
        raise ValueError("artifact, evidence, and fixture roots must be directories")
    if evidence == artifact_root or not evidence.is_relative_to(artifact_root):
        raise ValueError("evidence root must remain below the spatial-artifacts root")
    if evidence.name != EVIDENCE_ROOT_NAME:
        raise ValueError("evidence root must be spatial_security_evidence")
    if root.parent != evidence:
        raise ValueError("fixture method root must be a direct child of the evidence root")
    if not METHOD_ROOT.fullmatch(root.name):
        raise ValueError("fixture root must use the native storage test method name")
    if raw_evidence != evidence or raw_root != root:
        raise ValueError("fixture paths must be canonical")
    if evidence_owner_uid is None:
        evidence_owner_uid = owner_uid
    if evidence.stat().st_uid != evidence_owner_uid:
        raise ValueError("evidence root must be owned by the operator")
    info = root.stat()
    if info.st_uid != owner_uid:
        raise ValueError("fixture root must be owned by the operator")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError("fixture root must be private to the operator")
    return root
