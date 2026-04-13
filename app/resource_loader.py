from __future__ import annotations

from pathlib import Path

from app.errors import ResourceLoadError
from app.paths import RESOURCE_DEFAULT_PIPELINE_FILE, RESOURCE_DIR


def _import_resource_type():
    try:
        from maa.resource import Resource
    except ImportError as exc:
        raise ResourceLoadError(
            "Failed to import maa.resource. Install the MaaFramework Python binding "
            "for this machine before running the project."
        ) from exc
    return Resource


def create_resource():
    resource_type = _import_resource_type()
    try:
        return resource_type()
    except Exception as exc:
        raise ResourceLoadError("Failed to create MaaFramework Resource.") from exc


def load_resource_bundle(resource, resource_dir: Path = RESOURCE_DIR) -> Path:
    if not resource_dir.exists():
        raise ResourceLoadError(f"Resource directory does not exist: {resource_dir}")

    if not RESOURCE_DEFAULT_PIPELINE_FILE.exists():
        raise ResourceLoadError(
            f"Missing resource root default pipeline file: {RESOURCE_DEFAULT_PIPELINE_FILE}"
        )

    try:
        job = resource.post_bundle(str(resource_dir)).wait()
    except Exception as exc:
        raise ResourceLoadError(
            f"Resource bundle loading raised an exception for: {resource_dir}"
        ) from exc

    if not getattr(job, "succeeded", False):
        raise ResourceLoadError(f"Failed to load resource bundle from: {resource_dir}")

    return RESOURCE_DEFAULT_PIPELINE_FILE
