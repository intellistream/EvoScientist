"""Custom backends for EvoScientist agent."""

import os
import re
import subprocess
import uuid
from pathlib import Path

from deepagents.backends import FilesystemBackend
from deepagents.backends.filesystem import WriteResult, EditResult
from deepagents.backends.protocol import (
    BackendProtocol,
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
    SandboxBackendProtocol,
)

# System path prefixes that should never appear in virtual paths.
# If the agent hallucinates an absolute system path, we block it.
_SYSTEM_PATH_PREFIXES = (
    "/Users/", "/home/", "/tmp/", "/var/", "/etc/",
    "/opt/", "/usr/", "/bin/", "/sbin/", "/dev/",
    "/proc/", "/sys/", "/root/",
)

# Dangerous patterns that could escape the workspace
BLOCKED_PATTERNS = [
    r'\.\.',              # ../ directory traversal
    r'~/',                # home directory
    r'\bcd\s+/',          # cd to absolute path
    r'\brm\s+-rf\s+/',    # rm -rf with absolute path
]

# Dangerous commands that should never be executed
BLOCKED_COMMANDS = [
    'sudo',
    'chmod',
    'chown',
    'mkfs',
    'dd',
    'shutdown',
    'reboot',
]


def validate_command(command: str) -> str | None:
    """
    Validate a shell command for safety.

    Returns:
        None if command is safe, error message string if blocked.
    """
    # Check for directory traversal and dangerous patterns
    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, command):
            return (
                f"Command blocked: contains forbidden pattern '{pattern}'. "
                f"All commands must operate within the workspace directory. "
                f"Use relative paths (e.g., './file.py') instead."
            )

    # Check for dangerous commands
    for cmd in BLOCKED_COMMANDS:
        if re.search(rf'\b{cmd}\b', command):
            return (
                f"Command blocked: '{cmd}' is not allowed in sandbox mode. "
                f"Only standard development commands are permitted."
            )

    return None


def convert_virtual_paths_in_command(command: str) -> str:
    """
    Convert virtual paths (starting with /) in commands to relative paths.

    Examples:
    - "python /main.py" -> "python ./main.py"
    - "cat /data/file.txt" -> "cat ./data/file.txt"
    - "ls /" -> "ls ."
    - "python main.py" -> "python main.py" (unchanged)

    Args:
        command: Original command

    Returns:
        Converted command
    """

    def replace_virtual_path(match):
        path = match.group(0)

        # Skip content that looks like a URL
        if '://' in command[max(0, match.start() - 10):match.end() + 10]:
            return path

        # Convert virtual path
        if path == '/':
            return '.'
        else:
            return '.' + path

    # Match pattern: paths starting with / (but not URLs)
    pattern = r'(?<=\s)/[^\s;|&<>\'"`]*|^/[^\s;|&<>\'"`]*'
    converted = re.sub(pattern, replace_virtual_path, command)

    return converted


class ReadOnlyFilesystemBackend(FilesystemBackend):
    """
    Read-only filesystem backend.

    Allows read, ls, grep, glob operations but blocks write and edit.
    Used for skills directory — agent can read skill definitions but cannot
    modify them.
    """

    def write(self, file_path: str, content: str) -> WriteResult:
        return WriteResult(
            error="This directory is read-only. Write operations are not permitted here."
        )

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        return EditResult(
            error="This directory is read-only. Edit operations are not permitted here."
        )


class MergedReadOnlyBackend(BackendProtocol):
    """Read-only backend that merges two directories.

    Reads from *primary* first (user skills in workspace/skills/),
    falls back to *secondary* (system skills in ./skills/).
    User skills override system skills with the same name.

    Both directories share the same virtual path namespace — the agent
    sees all skills under /skills/ regardless of which backend serves them.
    """

    def __init__(self, primary_dir: str, secondary_dir: str):
        self._primary = ReadOnlyFilesystemBackend(root_dir=primary_dir, virtual_mode=True)
        self._secondary = ReadOnlyFilesystemBackend(root_dir=secondary_dir, virtual_mode=True)

    # -- read: try primary first, fall back to secondary --

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> str:
        try:
            result = self._primary.read(file_path, offset, limit)
            if not result.startswith("Error:"):
                return result
        except (ValueError, FileNotFoundError, OSError):
            pass
        return self._secondary.read(file_path, offset, limit)

    # -- ls_info: merge both, primary wins on name conflicts --

    def ls_info(self, path: str = "/") -> list:
        secondary_items = {item["path"]: item for item in self._secondary.ls_info(path)}
        primary_items = {item["path"]: item for item in self._primary.ls_info(path)}
        secondary_items.update(primary_items)  # primary overrides
        return sorted(secondary_items.values(), key=lambda x: x["path"])

    # -- grep_raw: search both, deduplicate --

    def grep_raw(self, pattern: str, path: str | None = None, glob: str | None = None) -> list:
        results = self._secondary.grep_raw(pattern, path, glob)
        try:
            results += self._primary.grep_raw(pattern, path, glob)
        except Exception:
            pass
        return results

    # -- glob_info: merge both --

    def glob_info(self, pattern: str, path: str = "/") -> list:
        secondary = {item["path"]: item for item in self._secondary.glob_info(pattern, path)}
        try:
            primary = {item["path"]: item for item in self._primary.glob_info(pattern, path)}
            secondary.update(primary)
        except Exception:
            pass
        return sorted(secondary.values(), key=lambda x: x["path"])

    # -- write / edit: blocked --

    def write(self, file_path: str, content: str) -> WriteResult:
        return WriteResult(
            error="This directory is read-only. Write operations are not permitted here."
        )

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        return EditResult(
            error="This directory is read-only. Edit operations are not permitted here."
        )

    # -- download / upload --

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Download files, trying primary then secondary."""
        responses: list[FileDownloadResponse] = []
        for path in paths:
            resp = self._primary.download_files([path])[0]
            if resp.error is not None:
                resp = self._secondary.download_files([path])[0]
            responses.append(resp)
        return responses

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return [
            FileUploadResponse(path=path, error="permission_denied")
            for path, _ in files
        ]


class CustomSandboxBackend(FilesystemBackend, SandboxBackendProtocol):
    """
    Custom sandbox backend - inherits FilesystemBackend and implements execute method.

    Features:
    - Inherits all file operations (ls, read, write, edit, grep, glob)
    - Adds shell command execution capability
    - Command validation prevents directory traversal and dangerous operations
    - Runs commands in specified working directory
    - Compatible with LangGraph checkpointer (no thread locks)
    """

    def __init__(
        self,
        root_dir: str = ".",
        virtual_mode: bool = True,
        working_dir: str | None = None,
        timeout: int = 300,
        shell: str = "/bin/bash",
        max_output_bytes: int = 100_000,
        env: dict[str, str] | None = None,
        inherit_env: bool = True,
    ):
        """
        Initialize custom sandbox backend.

        Args:
            root_dir: File system root directory
            virtual_mode: Whether to enable virtual path mode
            working_dir: Working directory for command execution (defaults to root_dir)
            timeout: Command execution timeout in seconds
            shell: Shell program to use
            max_output_bytes: Max output size before truncation (default 100KB)
            env: Extra environment variables for subprocess
            inherit_env: Whether to inherit parent process env (default True)
        """
        super().__init__(root_dir=root_dir, virtual_mode=virtual_mode)

        self._sandbox_id = f"evosci-{uuid.uuid4().hex[:8]}"
        self.working_dir = working_dir or root_dir
        self.timeout = timeout
        self.shell = shell
        self.virtual_mode = virtual_mode
        self._max_output_bytes = max_output_bytes

        # Build subprocess environment
        if inherit_env:
            self._env = {**os.environ, **(env or {})}
        else:
            self._env = dict(env) if env else {}

        # Ensure working directory exists
        os.makedirs(self.working_dir, exist_ok=True)

    @property
    def id(self) -> str:
        """Unique identifier for the sandbox backend instance."""
        return self._sandbox_id

    def _resolve_path(self, key: str) -> Path:
        """Resolve path with sanitization to prevent nested directories.

        Intercepts all file operations (read, write, edit, ls, grep, glob).
        Auto-corrects common LLM path mistakes instead of crashing:
          1. /workspace/file.py           → /file.py
          2. /Users/name/.../workspace/f  → /f  (strip up to workspace/)
          3. /Users/name/file.py          → /file.py (keep basename)
        """
        # Auto-strip /workspace/ prefix to prevent nesting
        if key.startswith("/workspace/"):
            key = key[len("/workspace"):]  # "/workspace/main.py" → "/main.py"
        elif key == "/workspace":
            key = "/"

        # Auto-correct system absolute paths
        for prefix in _SYSTEM_PATH_PREFIXES:
            if key.startswith(prefix):
                # Try to extract path after "workspace/" or "workspace" at end
                marker = "/workspace/"
                idx = key.find(marker)
                if idx != -1:
                    key = "/" + key[idx + len(marker):]
                elif key.endswith("/workspace"):
                    key = "/"
                else:
                    # Fall back to basename
                    key = "/" + Path(key).name
                break

        return super()._resolve_path(key)

    def execute(self, command: str) -> ExecuteResponse:
        """
        Execute shell command in sandbox environment.

        Commands are validated before execution to prevent:
        - Directory traversal (../)
        - Access to paths outside workspace
        - Dangerous system commands

        Args:
            command: Command string to execute

        Returns:
            ExecuteResponse containing output, exit_code, and truncated flag
        """
        try:
            # Validate command safety
            error = validate_command(command)
            if error:
                return ExecuteResponse(
                    output=error,
                    exit_code=1,
                    truncated=False,
                )

            # Convert virtual paths to relative paths
            if self.virtual_mode:
                command = convert_virtual_paths_in_command(command=command)

            result = subprocess.run(
                command,
                shell=True,
                executable=self.shell,
                cwd=self.working_dir,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=self._env,
            )

            output_parts = []
            if result.stdout:
                output_parts.append(result.stdout)
            if result.stderr:
                stderr_lines = result.stderr.strip().split("\n")
                output_parts.extend(f"[stderr] {line}" for line in stderr_lines)
            output = "\n".join(output_parts) if output_parts else ""

            if result.returncode != 0:
                output = f"{output.rstrip()}\n\nExit code: {result.returncode}"

            truncated = False
            if len(output) > self._max_output_bytes:
                output = output[:self._max_output_bytes]
                output += f"\n\n... Output truncated at {self._max_output_bytes} bytes."
                truncated = True

            return ExecuteResponse(
                output=output,
                exit_code=result.returncode,
                truncated=truncated,
            )

        except subprocess.TimeoutExpired:
            return ExecuteResponse(
                output=f"Command timed out after {self.timeout} seconds",
                exit_code=-1,
                truncated=False,
            )
        except Exception as e:
            return ExecuteResponse(
                output=f"Error executing command: {str(e)}",
                exit_code=-1,
                truncated=False,
            )

    async def aexecute(self, command: str) -> ExecuteResponse:
        """Async version of execute (runs sync version in thread)."""
        import asyncio
        return await asyncio.to_thread(self.execute, command)
