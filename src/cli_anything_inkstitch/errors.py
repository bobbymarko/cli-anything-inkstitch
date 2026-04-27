"""Typed exceptions mapped to exit codes per SPEC.md §4.7."""


class CLIError(Exception):
    exit_code = 1
    error_type = "Error"


class UserError(CLIError):
    exit_code = 1
    error_type = "UserError"


class ProjectError(CLIError):
    exit_code = 2
    error_type = "ProjectError"


class BinaryError(CLIError):
    exit_code = 3
    error_type = "BinaryError"

    def __init__(self, extension: str, returncode: int, stderr: str):
        self.extension = extension
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(
            f"inkstitch --extension={extension} exited {returncode}: {stderr.strip()[:500]}"
        )


class ValidationError(CLIError):
    exit_code = 4
    error_type = "ValidationError"
