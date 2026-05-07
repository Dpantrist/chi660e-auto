WINDOW_KEYWORD = "CHI660E Electrochemical Workstation"
MAIN_WINDOW_TITLE_CANDIDATES = [
    "CHI660E Electrochemical Workstation",
    "CH Instruments Electrochemical Software",
]

DEFAULT_SCREENCAP_METHOD = "GDI"
DEFAULT_INPUT_METHOD = "Seize"

APP_NAME = "chi660e_auto"
# 版本号规则：主版本代表基础框架，次版本代表功能添加和问题修复。
APP_VERSION = "1.1"

DIR_APP = "app"
DIR_RESOURCE = "resource"
DIR_PIPELINE = "pipeline"
DIR_IMAGE = "image"
DIR_LOGS = "logs"
DIR_DEBUG = "debug"
DIR_DEBUG_LATEST = "latest"
DIR_REPLAY = "replay"
DIR_CONFIG = "config"
DIR_TESTS = "tests"

LOG_FILE_NAME = "app.log"
FRAMEWORK_OPTION_FILE_NAME = "maa_option.json"
DEFAULT_PIPELINE_FILE_NAME = "default_pipeline.json"

REPLAY_SESSION_FILE_NAME = "session.json"
REPLAY_EVENT_FILE_NAME = "events.jsonl"

DEBUG_STARTUP_CAPTURE_NAME = "startup_capture"
DEBUG_ERROR_CAPTURE_NAME = "error_capture"

# TODO: move tuneable startup values into a dedicated parameter configuration layer.
