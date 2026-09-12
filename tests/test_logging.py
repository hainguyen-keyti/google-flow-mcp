import os
import sys

import structlog


def test_import_video_routes_gflow_logs_to_stderr_not_stdout():
    import video  # noqa: F401

    factory = structlog.get_config()["logger_factory"]
    assert isinstance(factory, structlog.PrintLoggerFactory)
    assert factory._file is sys.stderr


def test_import_video_defaults_gflow_log_level_to_warning():
    import video  # noqa: F401

    assert os.environ.get("GFLOW_CLI_LOG_LEVEL") in ("WARNING", "ERROR")
