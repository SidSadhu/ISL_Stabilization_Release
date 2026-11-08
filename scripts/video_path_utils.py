"""Safe local video-path resolution for official manifests and archive extensions."""
from __future__ import annotations

from pathlib import Path, PurePosixPath

VIDEO_EXTENSIONS = {".mov", ".mp4", ".avi", ".m4v", ".webm"}


def resolve_video_path(data_root: Path, video_path: str | Path) -> Path:
    """Resolve an exact manifest path, or a unique same-stem video extension.

    The official INCLUDE path list may use a .MOV suffix while the published
    Zenodo archive member is the same video in an .MP4 container. We preserve
    the archive's real suffix on disk and resolve that alternate suffix here.
    Ambiguous alternatives fail closed rather than choosing arbitrarily.
    """
    raw = Path(video_path).expanduser()
    expected = raw if raw.is_absolute() else Path(data_root).expanduser() / raw
    if expected.is_file():
        return expected
    if expected.suffix.lower() not in VIDEO_EXTENSIONS or not expected.parent.is_dir():
        return expected

    stem = expected.stem.casefold()
    candidates = [
        candidate
        for candidate in expected.parent.iterdir()
        if candidate.is_file()
        and candidate.stem.casefold() == stem
        and candidate.suffix.lower() in VIDEO_EXTENSIONS
    ]
    # Exact path was checked above. Exclude it defensively on case-sensitive FS.
    candidates = [candidate for candidate in candidates if candidate != expected]
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        names = ", ".join(sorted(candidate.name for candidate in candidates))
        raise RuntimeError(
            f"Ambiguous same-stem video alternatives for {expected}: {names}. "
            "Keep only the verified archive member or resolve the ambiguity manually."
        )
    return expected


def resolve_manifest_video(data_root: Path, video_path: str) -> Path:
    """Resolve a manifest-relative path while preventing path traversal."""
    raw = PurePosixPath(str(video_path).replace("\\", "/"))
    if raw.is_absolute() or ".." in raw.parts or (raw.parts and ":" in raw.parts[0]):
        raise ValueError(f"Unsafe manifest video path: {video_path}")
    root = Path(data_root).expanduser().resolve()
    candidate = root.joinpath(*raw.parts)
    resolved = resolve_video_path(root, candidate)
    try:
        resolved.resolve().relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Manifest video path escapes data root: {video_path}") from exc
    return resolved
