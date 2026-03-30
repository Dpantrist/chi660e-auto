from __future__ import annotations

from pathlib import Path

from app.constants import (
    DEFAULT_PIPELINE_FILE_NAME,
    DIR_CONFIG,
    DIR_DEBUG,
    DIR_DEBUG_LATEST,
    DIR_IMAGE,
    DIR_LOGS,
    DIR_PIPELINE,
    DIR_REPLAY,
    DIR_RESOURCE,
    FRAMEWORK_OPTION_FILE_NAME,
    LOG_FILE_NAME,
)
from app.dto import RuntimeFolders

BASE_DIR = Path(__file__).resolve().parent.parent
RESOURCE_DIR = BASE_DIR / DIR_RESOURCE
PIPELINE_DIR = RESOURCE_DIR / DIR_PIPELINE
IMAGE_DIR = RESOURCE_DIR / DIR_IMAGE
LOG_DIR = BASE_DIR / DIR_LOGS
DEBUG_DIR = BASE_DIR / DIR_DEBUG
DEBUG_LATEST_DIR = DEBUG_DIR / DIR_DEBUG_LATEST
REPLAY_DIR = BASE_DIR / DIR_REPLAY
CONFIG_DIR = BASE_DIR / DIR_CONFIG

DEFAULT_PIPELINE_FILE = BASE_DIR / DEFAULT_PIPELINE_FILE_NAME
RESOURCE_DEFAULT_PIPELINE_FILE = RESOURCE_DIR / DEFAULT_PIPELINE_FILE_NAME
LOG_FILE = LOG_DIR / LOG_FILE_NAME
MAA_OPTION_FILE = CONFIG_DIR / FRAMEWORK_OPTION_FILE_NAME

IMAGE_SUBDIRS = (
    IMAGE_DIR / "main",
    IMAGE_DIR / "techniques",
    IMAGE_DIR / "cv",
    IMAGE_DIR / "eis",
    IMAGE_DIR / "gcd",
    IMAGE_DIR / "common",
)


def ensure_project_dirs() -> RuntimeFolders:
    for path in (
        RESOURCE_DIR,
        PIPELINE_DIR,
        IMAGE_DIR,
        LOG_DIR,
        DEBUG_DIR,
        DEBUG_LATEST_DIR,
        REPLAY_DIR,
        CONFIG_DIR,
        *IMAGE_SUBDIRS,
    ):
        path.mkdir(parents=True, exist_ok=True)

    return RuntimeFolders(
        base_dir=BASE_DIR,
        resource_dir=RESOURCE_DIR,
        pipeline_dir=PIPELINE_DIR,
        image_dir=IMAGE_DIR,
        log_dir=LOG_DIR,
        debug_dir=DEBUG_DIR,
        debug_latest_dir=DEBUG_LATEST_DIR,
        replay_dir=REPLAY_DIR,
        config_dir=CONFIG_DIR,
    )
