"""Tests for EvoScientist/backends.py — validate_command, path conversion, resolve_path."""


import re

from EvoScientist.backends import (
    validate_command,
    convert_virtual_paths_in_command,
    CustomSandboxBackend,
)


# === validate_command ===

class TestValidateCommand:
    def test_safe_ls(self):
        assert validate_command("ls -la") is None

    def test_safe_python(self):
        assert validate_command("python script.py") is None

    def test_safe_pip(self):
        assert validate_command("pip install pandas") is None

    def test_blocked_traversal(self):
        result = validate_command("cat ../../../etc/passwd")
        assert result is not None
        assert "blocked" in result.lower()

    def test_blocked_sudo(self):
        result = validate_command("sudo rm -rf /")
        assert result is not None
        assert "blocked" in result.lower()

    def test_blocked_chmod(self):
        result = validate_command("chmod 777 file.py")
        assert result is not None

    def test_blocked_dd(self):
        result = validate_command("dd if=/dev/zero of=file bs=1M count=100")
        assert result is not None

    def test_blocked_home_tilde(self):
        result = validate_command("cat ~/secrets.txt")
        assert result is not None

    def test_blocked_rm_rf_absolute(self):
        result = validate_command("rm -rf /important")
        assert result is not None

    def test_blocked_cd_absolute(self):
        result = validate_command("cd /etc && cat passwd")
        assert result is not None

    def test_safe_echo(self):
        assert validate_command("echo hello world") is None

    def test_safe_grep(self):
        assert validate_command("grep -r 'pattern' .") is None


# === convert_virtual_paths_in_command ===

class TestConvertVirtualPaths:
    def test_absolute_to_relative(self):
        result = convert_virtual_paths_in_command("python /main.py")
        assert result == "python ./main.py"

    def test_nested_path(self):
        result = convert_virtual_paths_in_command("cat /data/file.txt")
        assert result == "cat ./data/file.txt"

    def test_root_only(self):
        result = convert_virtual_paths_in_command("ls /")
        assert result == "ls ."

    def test_no_change_relative(self):
        result = convert_virtual_paths_in_command("python main.py")
        assert result == "python main.py"

    def test_url_preserved(self):
        result = convert_virtual_paths_in_command("curl https://example.com/path")
        # URLs should not be converted
        assert "https://example.com/path" in result

    def test_no_op_no_paths(self):
        result = convert_virtual_paths_in_command("echo hello")
        assert result == "echo hello"


# === CustomSandboxBackend._resolve_path ===

class TestResolvePath:
    def test_strip_workspace_prefix(self, tmp_workspace):
        backend = CustomSandboxBackend(root_dir=tmp_workspace, virtual_mode=True)
        # /workspace/main.py should resolve to root/main.py
        resolved = backend._resolve_path("/workspace/main.py")
        assert str(resolved).endswith("main.py")
        assert "workspace/workspace" not in str(resolved)

    def test_workspace_root(self, tmp_workspace):
        backend = CustomSandboxBackend(root_dir=tmp_workspace, virtual_mode=True)
        resolved = backend._resolve_path("/workspace")
        # Should resolve to root dir
        assert resolved == backend._resolve_path("/")

    def test_system_path_with_workspace_marker(self, tmp_workspace):
        backend = CustomSandboxBackend(root_dir=tmp_workspace, virtual_mode=True)
        resolved = backend._resolve_path("/Users/someone/project/workspace/main.py")
        assert str(resolved).endswith("main.py")

    def test_system_path_without_workspace(self, tmp_workspace):
        backend = CustomSandboxBackend(root_dir=tmp_workspace, virtual_mode=True)
        resolved = backend._resolve_path("/Users/someone/file.py")
        # Falls back to basename
        assert str(resolved).endswith("file.py")

    def test_normal_virtual_path(self, tmp_workspace):
        backend = CustomSandboxBackend(root_dir=tmp_workspace, virtual_mode=True)
        resolved = backend._resolve_path("/src/main.py")
        assert str(resolved).endswith("src/main.py")


# === CustomSandboxBackend.id ===

class TestSandboxId:
    def test_sandbox_has_id(self, tmp_workspace):
        backend = CustomSandboxBackend(root_dir=tmp_workspace, virtual_mode=True)
        assert isinstance(backend.id, str)
        assert backend.id.startswith("evosci-")
        assert len(backend.id) == len("evosci-") + 8

    def test_sandbox_id_is_stable(self, tmp_workspace):
        backend = CustomSandboxBackend(root_dir=tmp_workspace, virtual_mode=True)
        assert backend.id == backend.id  # same instance → same id

    def test_sandbox_id_unique(self, tmp_workspace):
        b1 = CustomSandboxBackend(root_dir=tmp_workspace, virtual_mode=True)
        b2 = CustomSandboxBackend(root_dir=tmp_workspace, virtual_mode=True)
        assert b1.id != b2.id

    def test_sandbox_id_hex_suffix(self, tmp_workspace):
        backend = CustomSandboxBackend(root_dir=tmp_workspace, virtual_mode=True)
        suffix = backend.id[len("evosci-"):]
        assert re.fullmatch(r"[0-9a-f]{8}", suffix)


# === execute() output truncation ===

class TestExecuteTruncation:
    def test_execute_truncates_large_output(self, tmp_workspace):
        backend = CustomSandboxBackend(
            root_dir=tmp_workspace, working_dir=tmp_workspace,
            virtual_mode=True, max_output_bytes=100,
        )
        # Generate output larger than 100 bytes
        resp = backend.execute("python3 -c \"print('A' * 200)\"")
        assert resp.truncated is True
        assert "... Output truncated at 100 bytes" in resp.output
        # Output body (before truncation message) should be ≤ 100 bytes
        before_marker = resp.output.split("\n\n... Output truncated")[0]
        assert len(before_marker) <= 100

    def test_execute_no_truncation_small_output(self, tmp_workspace):
        backend = CustomSandboxBackend(
            root_dir=tmp_workspace, working_dir=tmp_workspace,
            virtual_mode=True, max_output_bytes=100_000,
        )
        resp = backend.execute("echo hello")
        assert resp.truncated is False
        assert "truncated" not in resp.output.lower()


# === execute() stderr attribution ===

class TestExecuteStderr:
    def test_execute_stderr_attribution(self, tmp_workspace):
        backend = CustomSandboxBackend(
            root_dir=tmp_workspace, working_dir=tmp_workspace, virtual_mode=True,
        )
        resp = backend.execute("python3 -c \"import sys; sys.stderr.write('warning\\n')\"")
        assert "[stderr] warning" in resp.output

    def test_execute_nonzero_exit_code_in_output(self, tmp_workspace):
        backend = CustomSandboxBackend(
            root_dir=tmp_workspace, working_dir=tmp_workspace, virtual_mode=True,
        )
        resp = backend.execute("python3 -c \"raise SystemExit(42)\"")
        assert resp.exit_code == 42
        assert "Exit code: 42" in resp.output

    def test_execute_mixed_stdout_stderr(self, tmp_workspace):
        backend = CustomSandboxBackend(
            root_dir=tmp_workspace, working_dir=tmp_workspace, virtual_mode=True,
        )
        resp = backend.execute(
            "python3 -c \"import sys; print('out'); sys.stderr.write('err\\n')\""
        )
        assert "out" in resp.output
        assert "[stderr] err" in resp.output

    def test_execute_success_no_exit_code(self, tmp_workspace):
        backend = CustomSandboxBackend(
            root_dir=tmp_workspace, working_dir=tmp_workspace, virtual_mode=True,
        )
        resp = backend.execute("echo ok")
        assert resp.exit_code == 0
        assert "Exit code:" not in resp.output
