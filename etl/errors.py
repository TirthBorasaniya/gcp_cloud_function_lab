"""
Exception types raised by the ETL pipeline.
"""


class ConfigError(ValueError):
    """Raised when schemas.yaml is malformed."""


class FileValidationError(ValueError):
    """Raised when an uploaded file fails parsing or schema validation."""

    def __init__(self, error_list):
        if not error_list:
            raise ValueError("FileValidationError requires at least one error message")
        self.error_list = list(error_list)
        super().__init__(
            f"{len(self.error_list)} validation error(s); first: {self.error_list[0]}"
        )


class LoadError(RuntimeError):
    """Raised when BigQuery rejects a load job."""
