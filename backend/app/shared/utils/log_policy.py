"""Shared retention policy for application and subprocess logs."""

LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_ARCHIVES = 5
LOG_MAX_AGE_S = 7 * 24 * 60 * 60
LOG_ARCHIVE_BYTES = LOG_MAX_BYTES * LOG_ARCHIVES
