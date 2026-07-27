from __future__ import annotations

import ast
import os
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.support import (
    CLAUDE_ADAPTER,
    CODEX_ADAPTER,
    REPO_ROOT,
    assert_directory,
    assert_file,
    read_text,
    run_command,
    tree_digest,
)


class InstallerContractTests(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._trusted_python_temp = tempfile.TemporaryDirectory(
            prefix="atlas trusted test python "
        )
        self.addCleanup(self._trusted_python_temp.cleanup)
        trusted_bin = Path(self._trusted_python_temp.name) / "bin"
        self.write_fake_command(
            trusted_bin,
            "python3",
            'exec "$ATLAS_TEST_MATRIX_PYTHON" "$@"',
        )
        trusted_bin.chmod(0o700)
        environment = mock.patch.dict(
            os.environ,
            {
                "ATLAS_TEST_MATRIX_PYTHON": sys.executable,
                "PATH": f"{trusted_bin}{os.pathsep}{os.environ['PATH']}",
            },
        )
        environment.start()
        self.addCleanup(environment.stop)

    def assert_bundle_installed(self, source: Path, target: Path) -> None:
        assert_directory(self, source)
        assert_directory(self, target)
        self.assertEqual(
            tree_digest(target, excluded_names={"__pycache__"}),
            tree_digest(source, excluded_names={"__pycache__"}),
            "installed skill does not match its adapter bundle",
        )

    def assert_codex_standalone_installed(self, target: Path) -> None:
        source = CODEX_ADAPTER / "skills" / "map-project"
        assert_directory(self, source)
        assert_directory(self, target)
        source_files = {
            path.relative_to(source): path.read_bytes()
            for path in source.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        }
        target_files = {
            path.relative_to(target): path.read_bytes()
            for path in target.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        }
        self.assertEqual(set(target_files), set(source_files))
        metadata = Path("agents/openai.yaml")
        for relative, content in source_files.items():
            with self.subTest(path=relative):
                if relative == metadata:
                    expected = content.replace(b"$project-atlas:map-project", b"$map-project")
                    self.assertNotEqual(expected, content, "plugin metadata has no namespaced invocation")
                    self.assertEqual(target_files[relative], expected)
                else:
                    self.assertEqual(target_files[relative], content)
        self.assertFalse(any(path.is_symlink() for path in target.rglob("*")))

    def write_fake_command(self, directory: Path, name: str, body: str) -> Path:
        directory.mkdir(parents=True)
        command = directory / name
        command.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        command.chmod(0o755)
        return command

    def write_fake_diff(self, directory: Path, body: str) -> Path:
        return self.write_fake_command(directory, "diff", body)

    def create_hardlink_or_skip(self, source: Path, target: Path) -> None:
        target.parent.mkdir(parents=True)
        try:
            os.link(source, target)
        except OSError as exc:
            self.skipTest(f"hardlink regression is unsupported: {exc}")
        self.assertTrue(source.samefile(target))
        self.assertGreater(source.stat().st_nlink, 1)

    def require_case_alias(self, path: Path) -> Path:
        alias = path.with_name(path.name.swapcase())
        self.assertNotEqual(alias, path, "case-alias probe did not change the path spelling")
        try:
            aliases_same_file = alias.samefile(path)
        except OSError:
            aliases_same_file = False
        if not aliases_same_file:
            self.skipTest("case-alias regression requires a case-insensitive filesystem")
        return alias

    def write_failing_find(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        command = directory / "find"
        command.write_text("#!/bin/sh\nexit 97\n", encoding="utf-8")
        command.chmod(0o755)
        return command

    def write_counted_diff(self, directory: Path) -> Path:
        return self.write_fake_diff(
            directory,
            'count=0\n'
            'if [ -f "$ATLAS_TEST_DIFF_COUNT" ]; then count="$(cat "$ATLAS_TEST_DIFF_COUNT")"; fi\n'
            'count=$((count + 1))\n'
            'printf "%s\\n" "$count" > "$ATLAS_TEST_DIFF_COUNT"\n'
            'if [ "$count" -eq "${ATLAS_TEST_BLOCK_CALL:-0}" ]; then\n'
            '  : > "$ATLAS_TEST_BARRIER"\n'
            '  while [ ! -e "$ATLAS_TEST_RELEASE" ]; do sleep 0.05; done\n'
            'fi\n'
            'if [ "$count" -eq "${ATLAS_TEST_SIGNAL_CALL:-0}" ]; then\n'
            '  kill -TERM "$PPID"\n'
            '  sleep 1\n'
            '  exit 1\n'
            'fi\n'
            'if [ "$count" -eq "${ATLAS_TEST_FAIL_CALL:-0}" ]; then exit 1; fi\n'
            'exec "$ATLAS_REAL_DIFF" "$@"',
        )

    def write_stalling_diff(self, directory: Path) -> Path:
        return self.write_fake_diff(
            directory,
            'count=0\n'
            'if [ -f "$ATLAS_TEST_DIFF_COUNT" ]; then count="$(cat "$ATLAS_TEST_DIFF_COUNT")"; fi\n'
            'count=$((count + 1))\n'
            'printf "%s\\n" "$count" > "$ATLAS_TEST_DIFF_COUNT"\n'
            'if [ "$count" -eq "$ATLAS_TEST_STALL_CALL" ]; then\n'
            '  printf "%s\\n" "$$" > "$ATLAS_TEST_STALLED_DIFF_PID"\n'
            "  trap '' HUP INT TERM\n"
            '  sleep "${ATLAS_TEST_STALL_SECONDS:-2}"\n'
            'fi\n'
            'exec "$ATLAS_REAL_DIFF" "$@"',
        )

    def write_blocking_python(self, directory: Path) -> Path:
        directory.mkdir(parents=True)
        command = directory / "python3"
        command.write_text(
            "#!/bin/sh\n"
            ': > "$ATLAS_TEST_BARRIER"\n'
            'while [ ! -e "$ATLAS_TEST_RELEASE" ]; do sleep 0.05; done\n'
            'exec "$ATLAS_REAL_PYTHON" "$@"\n',
            encoding="utf-8",
        )
        command.chmod(0o755)
        return command

    def wait_for_process_barrier(
        self,
        process: subprocess.Popen[str],
        barrier: Path,
        message: str,
        *,
        timeout: float = 5,
    ) -> None:
        deadline = time.monotonic() + timeout
        while not barrier.exists() and time.monotonic() < deadline:
            if process.poll() is not None:
                break
            time.sleep(0.02)
        if barrier.exists():
            return
        if process.poll() is None:
            process.terminate()
        stdout, stderr = process.communicate(timeout=10)
        self.fail(f"{message}\nstdout:\n{stdout}\nstderr:\n{stderr}")

    def run_installer_until_stalled_diff_timeout(
        self,
        *,
        script: Path,
        env: dict[str, str],
        stalled_pid_path: Path,
        phase: str,
    ) -> tuple[subprocess.CompletedProcess[str], float]:
        merged_env = os.environ.copy()
        merged_env.update(env)
        process = subprocess.Popen(
            ["bash", str(script), "--force"],
            cwd=REPO_ROOT,
            env=merged_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        completed = False
        try:
            self.wait_for_process_barrier(
                process,
                stalled_pid_path,
                f"installer did not reach the {phase} diff within 10 seconds",
                timeout=10,
            )

            started = time.monotonic()
            try:
                stdout, stderr = process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                self.fail(
                    f"installer did not finish within 3 seconds after the {phase} "
                    "diff stalled"
                )
            elapsed = time.monotonic() - started
            result = subprocess.CompletedProcess(
                process.args,
                process.returncode,
                stdout,
                stderr,
            )
            completed = True
            return result, elapsed
        finally:
            if not completed:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.communicate(timeout=1)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.communicate(timeout=5)
                else:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def assert_process_group_exited(self, process_group: int) -> None:
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            try:
                os.killpg(process_group, 0)
            except ProcessLookupError:
                return
            time.sleep(0.02)
        try:
            os.killpg(process_group, signal.SIGKILL)
        except ProcessLookupError:
            pass
        self.fail(f"external verifier process group {process_group} remained alive")

    def assert_critical_move_failure_restores_previous_version(
        self,
        *,
        script: Path,
        config_variable: str,
        adapter_name: str,
        test_point: str,
        inject_signal: bool,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix=f"atlas {test_point.lower()} {adapter_name} "
        ) as temp_dir:
            root = Path(temp_dir)
            config = root / f"{adapter_name} config"
            skills = config / "skills"
            target = skills / "map-project"
            target.mkdir(parents=True)
            old_marker = target / "old-marker.txt"
            old_marker.write_text("preserve-original\n", encoding="utf-8")

            foreign = skills / "foreign-owned-directory"
            foreign.mkdir()
            foreign_marker = foreign / "foreign-marker.txt"
            foreign_marker.write_text("do-not-touch\n", encoding="utf-8")
            foreign_identity = (foreign.stat().st_dev, foreign.stat().st_ino)

            env = os.environ.copy()
            env.update(
                {
                    "HOME": str(root / "home"),
                    config_variable: str(config),
                }
            )
            barrier = root / f"{test_point.lower()}-barrier"
            release = root / f"{test_point.lower()}-release"
            if inject_signal:
                env[f"ATLAS_TEST_{test_point}_BARRIER"] = str(barrier)
                env[f"ATLAS_TEST_{test_point}_RELEASE"] = str(release)
            else:
                env[f"ATLAS_TEST_{test_point}_FAIL"] = "1"

            process = subprocess.Popen(
                ["bash", str(script), "--force"],
                cwd=REPO_ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if inject_signal:
                self.wait_for_process_barrier(
                    process,
                    barrier,
                    f"installer never reached {test_point} after the atomic move",
                )
                backup_root = config / ".skill-backups" / "project-atlas"
                backups_at_barrier = list(backup_root.glob("map-project-*"))
                self.assertEqual(len(backups_at_barrier), 1)
                self.assertEqual(
                    (backups_at_barrier[0] / "old-marker.txt").read_text(encoding="utf-8"),
                    "preserve-original\n",
                )
                if test_point == "AFTER_BACKUP_MOVE":
                    self.assertFalse(target.exists())
                else:
                    self.assertTrue((target / "SKILL.md").is_file())
                python_pid = int(barrier.read_text(encoding="utf-8").strip())
                os.kill(python_pid, signal.SIGTERM)
                release.touch()
            stdout, stderr = process.communicate(timeout=10)

            self.assertNotEqual(process.returncode, 0, stderr or stdout)
            if inject_signal:
                self.assertEqual(process.returncode, 128 + signal.SIGTERM, stderr or stdout)
                self.assertIn(f"interrupted by signal {signal.SIGTERM}", stderr)
            else:
                self.assertIn(f"injected failure at test point {test_point}", stderr)
            self.assertEqual(old_marker.read_text(encoding="utf-8"), "preserve-original\n")
            self.assertFalse(
                (target / "SKILL.md").exists(),
                "failed transaction left the promoted version installed",
            )
            self.assertEqual(
                (foreign.stat().st_dev, foreign.stat().st_ino),
                foreign_identity,
                "rollback replaced a foreign neighboring path",
            )
            self.assertEqual(foreign_marker.read_text(encoding="utf-8"), "do-not-touch\n")
            backup_root = config / ".skill-backups" / "project-atlas"
            self.assertEqual(
                list(backup_root.glob("map-project-*")),
                [],
                "restored previous version remained duplicated or stranded in backup storage",
            )

    def test_faults_immediately_after_atomic_moves_restore_previous_version(self) -> None:
        cases = (
            ("codex", REPO_ROOT / "scripts" / "install.sh", "CODEX_HOME"),
            ("claude", REPO_ROOT / "scripts" / "install-claude.sh", "CLAUDE_CONFIG_DIR"),
        )
        for test_point in ("AFTER_BACKUP_MOVE", "AFTER_PROMOTION_MOVE"):
            for adapter_name, script, config_variable in cases:
                with self.subTest(adapter=adapter_name, test_point=test_point):
                    self.assert_critical_move_failure_restores_previous_version(
                        script=script,
                        config_variable=config_variable,
                        adapter_name=adapter_name,
                        test_point=test_point,
                        inject_signal=False,
                    )

    def test_signals_immediately_after_atomic_moves_restore_previous_version(self) -> None:
        cases = (
            ("codex", REPO_ROOT / "scripts" / "install.sh", "CODEX_HOME"),
            ("claude", REPO_ROOT / "scripts" / "install-claude.sh", "CLAUDE_CONFIG_DIR"),
        )
        for test_point in ("AFTER_BACKUP_MOVE", "AFTER_PROMOTION_MOVE"):
            for adapter_name, script, config_variable in cases:
                with self.subTest(adapter=adapter_name, test_point=test_point):
                    self.assert_critical_move_failure_restores_previous_version(
                        script=script,
                        config_variable=config_variable,
                        adapter_name=adapter_name,
                        test_point=test_point,
                        inject_signal=True,
                    )

    def test_codex_installer_supports_safe_flags(self) -> None:
        script = REPO_ROOT / "scripts" / "install.sh"
        assert_file(self, script)
        result = run_command(["bash", script, "--help"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--user-scope", result.stdout)
        self.assertIn("--force", result.stdout)

    def test_installer_rejects_repository_local_diff_from_path(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas repository local diff ") as temp_dir:
            root = Path(temp_dir)
            clone = root / "project-atlas"
            shutil.copytree(
                REPO_ROOT,
                clone,
                ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", ".scratch"),
            )
            fake_bin = clone / "untrusted-bin"
            fake_bin.mkdir()
            canary = root / "diff-executed"
            fake_diff = fake_bin / "diff"
            fake_diff.write_text(
                "#!/bin/sh\n"
                f"touch {str(canary)!r}\n"
                "exit 0\n",
                encoding="utf-8",
            )
            fake_diff.chmod(0o755)
            result = run_command(
                ["bash", clone / "scripts" / "install.sh"],
                env={
                    "HOME": str(root / "home"),
                    "CODEX_HOME": str(root / "codex"),
                    "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                },
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unsafe", result.stderr.lower())
            self.assertFalse(canary.exists(), "repository-local diff was launched")

    @unittest.skipUnless(os.name == "posix", "POSIX hardlink regression")
    def test_installers_reject_user_python_hardlinked_into_source_repository(
        self,
    ) -> None:
        effective_uid_getter = getattr(os, "geteuid", None)
        self.assertTrue(callable(effective_uid_getter))
        if effective_uid_getter() == 0:
            self.skipTest("user-owned executable regression requires a non-root runner")
        installers = (
            ("codex", Path("scripts/install.sh"), "CODEX_HOME"),
            ("claude", Path("scripts/install-claude.sh"), "CLAUDE_CONFIG_DIR"),
        )
        for adapter_name, relative_script, config_variable in installers:
            with self.subTest(adapter=adapter_name), tempfile.TemporaryDirectory(
                prefix=f"atlas hardlinked python {adapter_name} "
            ) as temp_dir:
                root = Path(temp_dir)
                repository = root / "project-atlas"
                shutil.copytree(
                    REPO_ROOT,
                    repository,
                    ignore=shutil.ignore_patterns(
                        ".git", "__pycache__", "*.pyc", ".scratch"
                    ),
                )
                host_bin = root / "host-tools" / "bin"
                canary = root / f"{adapter_name}-python-executed"
                executable = self.write_fake_command(
                    host_bin,
                    "python3",
                    ': > "$ATLAS_TEST_EXECUTABLE_CANARY"\nexit 97',
                )
                self.assertEqual(executable.stat().st_uid, effective_uid_getter())
                self.create_hardlink_or_skip(
                    executable,
                    repository / "untrusted-hardlinks" / "python3",
                )
                config = root / f"{adapter_name} config"
                result = run_command(
                    [repository / relative_script],
                    env={
                        "HOME": str(root / "home"),
                        config_variable: str(config),
                        "PATH": f"{host_bin}{os.pathsep}{os.environ['PATH']}",
                        "ATLAS_TEST_EXECUTABLE_CANARY": str(canary),
                    },
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(
                    canary.exists(),
                    f"{adapter_name} installer launched hardlinked user Python",
                )
                self.assertIn("unsafe", result.stderr.lower())

    @unittest.skipUnless(os.name == "posix", "POSIX hardlink regression")
    def test_installers_reject_user_diff_hardlinked_into_source_repository(
        self,
    ) -> None:
        effective_uid_getter = getattr(os, "geteuid", None)
        self.assertTrue(callable(effective_uid_getter))
        if effective_uid_getter() == 0:
            self.skipTest("user-owned executable regression requires a non-root runner")
        installers = (
            ("codex", Path("scripts/install.sh"), "CODEX_HOME"),
            ("claude", Path("scripts/install-claude.sh"), "CLAUDE_CONFIG_DIR"),
        )
        for adapter_name, relative_script, config_variable in installers:
            with self.subTest(adapter=adapter_name), tempfile.TemporaryDirectory(
                prefix=f"atlas hardlinked diff {adapter_name} "
            ) as temp_dir:
                root = Path(temp_dir)
                repository = root / "project-atlas"
                shutil.copytree(
                    REPO_ROOT,
                    repository,
                    ignore=shutil.ignore_patterns(
                        ".git", "__pycache__", "*.pyc", ".scratch"
                    ),
                )
                host_bin = root / "host-tools" / "bin"
                canary = root / f"{adapter_name}-diff-executed"
                executable = self.write_fake_diff(
                    host_bin,
                    ': > "$ATLAS_TEST_EXECUTABLE_CANARY"\nexit 97',
                )
                self.assertEqual(executable.stat().st_uid, effective_uid_getter())
                self.create_hardlink_or_skip(
                    executable,
                    repository / "untrusted-hardlinks" / "diff",
                )
                config = root / f"{adapter_name} config"
                result = run_command(
                    [repository / relative_script],
                    env={
                        "HOME": str(root / "home"),
                        config_variable: str(config),
                        "PATH": f"{host_bin}{os.pathsep}{os.environ['PATH']}",
                        "ATLAS_TEST_EXECUTABLE_CANARY": str(canary),
                    },
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(
                    canary.exists(),
                    f"{adapter_name} installer launched hardlinked user diff",
                )
                self.assertIn("unsafe", result.stderr.lower())

    def test_installers_reject_case_aliased_repository_python_from_path(self) -> None:
        installers = (
            ("codex", Path("scripts/install.sh"), "CODEX_HOME"),
            ("claude", Path("scripts/install-claude.sh"), "CLAUDE_CONFIG_DIR"),
        )
        for adapter_name, relative_script, config_variable in installers:
            with self.subTest(adapter=adapter_name), tempfile.TemporaryDirectory(
                prefix=f"atlas case alias python {adapter_name} "
            ) as temp_dir:
                root = Path(temp_dir)
                repository = root / "Project-Atlas-Case"
                shutil.copytree(
                    REPO_ROOT,
                    repository,
                    ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", ".scratch"),
                )
                alias = self.require_case_alias(repository)
                repository_bin = repository / "untrusted-bin"
                canary = root / f"{adapter_name}-python-executed"
                self.write_fake_command(
                    repository_bin,
                    "python3",
                    f": > {str(canary)!r}\nexit 97",
                )
                config = root / f"{adapter_name} config"
                result = run_command(
                    [repository / relative_script],
                    env={
                        "HOME": str(root / "home"),
                        config_variable: str(config),
                        "PATH": (
                            f"{alias / 'untrusted-bin'}"
                            f"{os.pathsep}{os.environ['PATH']}"
                        ),
                    },
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("unsafe", result.stderr.lower())
                self.assertFalse(
                    canary.exists(),
                    f"{adapter_name} installer launched case-aliased repository python",
                )

    def test_installers_reject_case_aliased_repository_diff_from_path(self) -> None:
        installers = (
            ("codex", Path("scripts/install.sh"), "CODEX_HOME"),
            ("claude", Path("scripts/install-claude.sh"), "CLAUDE_CONFIG_DIR"),
        )
        for adapter_name, relative_script, config_variable in installers:
            with self.subTest(adapter=adapter_name), tempfile.TemporaryDirectory(
                prefix=f"atlas case alias diff {adapter_name} "
            ) as temp_dir:
                root = Path(temp_dir)
                repository = root / "Project-Atlas-Case"
                shutil.copytree(
                    REPO_ROOT,
                    repository,
                    ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", ".scratch"),
                )
                alias = self.require_case_alias(repository)
                repository_bin = repository / "untrusted-bin"
                canary = root / f"{adapter_name}-diff-executed"
                self.write_fake_diff(
                    repository_bin,
                    f": > {str(canary)!r}\nexit 97",
                )
                config = root / f"{adapter_name} config"
                result = run_command(
                    [repository / relative_script],
                    env={
                        "HOME": str(root / "home"),
                        config_variable: str(config),
                        "PATH": (
                            f"{alias / 'untrusted-bin'}"
                            f"{os.pathsep}{os.environ['PATH']}"
                        ),
                    },
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("unsafe", result.stderr.lower())
                self.assertFalse(
                    canary.exists(),
                    f"{adapter_name} installer launched case-aliased repository diff",
                )

    def test_installers_reject_outer_repository_diff_from_path(self) -> None:
        cases = (
            ("codex", Path("scripts/install.sh"), "CODEX_HOME"),
            ("claude", Path("scripts/install-claude.sh"), "CLAUDE_CONFIG_DIR"),
        )
        for adapter_name, relative_script, config_variable in cases:
            with self.subTest(adapter=adapter_name), tempfile.TemporaryDirectory(
                prefix=f"atlas outer repository diff {adapter_name} "
            ) as temp_dir:
                root = Path(temp_dir)
                repository = root / "untrusted repository"
                (repository / ".git").mkdir(parents=True)
                clone = repository / "packages" / "project-atlas"
                shutil.copytree(
                    REPO_ROOT,
                    clone,
                    ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", ".scratch"),
                )
                fake_bin = repository / "bin"
                canary = root / f"{adapter_name}-diff-executed"
                self.write_fake_diff(fake_bin, f"touch {str(canary)!r}\nexit 0")
                config = root / f"{adapter_name} config"
                result = run_command(
                    [clone / relative_script],
                    env={
                        "HOME": str(root / "home"),
                        config_variable: str(config),
                        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                    },
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("unsafe", result.stderr.lower())
                self.assertFalse(
                    canary.exists(),
                    f"{adapter_name} installer launched outer-repository diff",
                )

    def test_installers_accept_safe_tools_from_unrelated_repository(self) -> None:
        """A separate Homebrew-style checkout is not part of the Atlas trust boundary."""

        cases = (
            ("codex", Path("scripts/install.sh"), "CODEX_HOME"),
            ("claude", Path("scripts/install-claude.sh"), "CLAUDE_CONFIG_DIR"),
        )
        real_diff = shutil.which("diff")
        self.assertIsNotNone(real_diff, "unrelated-repository regression requires system diff")
        for adapter_name, relative_script, config_variable in cases:
            with self.subTest(adapter=adapter_name), tempfile.TemporaryDirectory(
                prefix=f"atlas unrelated host tools {adapter_name} "
            ) as temp_dir:
                root = Path(temp_dir)
                host_repository = root / "unrelated-host-tools"
                (host_repository / ".git").mkdir(parents=True)
                python_bin = host_repository / "python-bin"
                diff_bin = host_repository / "diff-bin"
                python_canary = root / f"{adapter_name}-python-executed"
                diff_canary = root / f"{adapter_name}-diff-executed"
                self.write_fake_command(
                    python_bin,
                    "python3",
                    (
                        f": > {str(python_canary)!r}\n"
                        'exec "$ATLAS_TEST_MATRIX_PYTHON" "$@"'
                    ),
                )
                self.write_fake_diff(
                    diff_bin,
                    (
                        f": > {str(diff_canary)!r}\n"
                        'exec "$ATLAS_TEST_REAL_DIFF" "$@"'
                    ),
                )
                config = root / f"{adapter_name} config"
                result = run_command(
                    [relative_script],
                    env={
                        "HOME": str(root / "home"),
                        config_variable: str(config),
                        "PATH": (
                            f"{python_bin}{os.pathsep}{diff_bin}"
                            f"{os.pathsep}{os.environ['PATH']}"
                        ),
                        "ATLAS_TEST_REAL_DIFF": str(real_diff),
                    },
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(python_canary.is_file())
                self.assertTrue(diff_canary.is_file())
                self.assertTrue(
                    (config / "skills" / "map-project" / "SKILL.md").is_file()
                )

    def test_installers_accept_python_from_standard_homebrew_cellar(self) -> None:
        """A standard Homebrew Cellar may be group-managed by the invoking user."""

        homebrew_bins = (
            Path("/opt/homebrew/bin"),
            Path("/usr/local/bin"),
            Path("/home/linuxbrew/.linuxbrew/bin"),
        )
        homebrew_python: Path | None = None
        homebrew_cellar: Path | None = None
        for bin_directory in homebrew_bins:
            candidate = bin_directory / "python3"
            try:
                resolved = candidate.resolve(strict=True)
            except OSError:
                continue
            for cellar in (
                Path("/opt/homebrew/Cellar"),
                Path("/usr/local/Cellar"),
                Path("/home/linuxbrew/.linuxbrew/Cellar"),
            ):
                if resolved == cellar or cellar in resolved.parents:
                    if cellar.stat().st_mode & stat.S_IWGRP:
                        homebrew_python = candidate
                        homebrew_cellar = cellar
                    break
            if homebrew_python is not None:
                break
        if homebrew_python is None or homebrew_cellar is None:
            self.skipTest(
                "dynamic Homebrew regression requires python3 below a "
                "group-writable standard Cellar"
            )

        self.assertFalse(homebrew_cellar.stat().st_mode & stat.S_IWOTH)
        self.assertIn(
            homebrew_cellar.stat().st_gid,
            {*os.getgroups(), os.getegid()},
        )
        cases = (
            ("codex", Path("scripts/install.sh"), "CODEX_HOME"),
            ("claude", Path("scripts/install-claude.sh"), "CLAUDE_CONFIG_DIR"),
        )
        for adapter_name, relative_script, config_variable in cases:
            with self.subTest(adapter=adapter_name), tempfile.TemporaryDirectory(
                prefix=f"atlas homebrew python {adapter_name} "
            ) as temp_dir:
                root = Path(temp_dir)
                config = root / f"{adapter_name} config"
                result = run_command(
                    [relative_script],
                    env={
                        "HOME": str(root / "home"),
                        config_variable: str(config),
                        "PATH": (
                            f"{homebrew_python.parent}"
                            f"{os.pathsep}/usr/bin{os.pathsep}/bin"
                        ),
                    },
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(
                    (config / "skills" / "map-project" / "SKILL.md").is_file()
                )

    @unittest.skipUnless(os.name == "posix", "POSIX sticky-directory regression")
    def test_installers_accept_python_below_a_sticky_world_writable_ancestor(
        self,
    ) -> None:
        tmp_root = Path("/tmp")
        try:
            tmp_mode = tmp_root.stat().st_mode
        except OSError:
            self.skipTest("/tmp is unavailable")
        if not (
            tmp_mode & stat.S_ISVTX
            and tmp_mode & stat.S_IWGRP
            and tmp_mode & stat.S_IWOTH
        ):
            self.skipTest("/tmp is not a sticky world-writable directory")

        cases = (
            ("codex", Path("scripts/install.sh"), "CODEX_HOME"),
            ("claude", Path("scripts/install-claude.sh"), "CLAUDE_CONFIG_DIR"),
        )
        for adapter_name, relative_script, config_variable in cases:
            with self.subTest(adapter=adapter_name), tempfile.TemporaryDirectory(
                prefix=f"atlas sticky python {adapter_name} ",
                dir=tmp_root,
            ) as temp_dir:
                root = Path(temp_dir)
                python_bin = root / "host-tools" / "bin"
                self.write_fake_command(
                    python_bin,
                    "python3",
                    'exec "$ATLAS_TEST_MATRIX_PYTHON" "$@"',
                )
                config = root / f"{adapter_name} config"
                result = run_command(
                    [relative_script],
                    env={
                        "HOME": str(root / "home"),
                        config_variable: str(config),
                        "PATH": (
                            f"{python_bin}{os.pathsep}/usr/bin"
                            f"{os.pathsep}/bin"
                        ),
                    },
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(
                    (config / "skills" / "map-project" / "SKILL.md").is_file()
                )

    def test_installers_run_embedded_python_in_isolated_mode(self) -> None:
        cases = (
            ("codex", Path("scripts/install.sh"), "CODEX_HOME"),
            ("claude", Path("scripts/install-claude.sh"), "CLAUDE_CONFIG_DIR"),
        )
        attacks = (
            (
                "cwd-hashlib",
                "hashlib.py",
                (
                    "import os\n"
                    "with open(os.environ['ATLAS_IMPORT_CANARY'], 'w') as canary:\n"
                    "    canary.write('cwd-hashlib\\n')\n"
                ),
                "cwd",
            ),
            (
                "pythonpath-sitecustomize",
                "sitecustomize.py",
                (
                    "import os\n"
                    "with open(os.environ['ATLAS_IMPORT_CANARY'], 'w') as canary:\n"
                    "    canary.write('pythonpath-sitecustomize\\n')\n"
                ),
                "pythonpath",
            ),
        )
        for adapter_name, relative_script, config_variable in cases:
            for attack_name, filename, payload, vector in attacks:
                with self.subTest(
                    adapter=adapter_name,
                    attack=attack_name,
                ), tempfile.TemporaryDirectory(
                    prefix=f"atlas isolated python {adapter_name} {attack_name} "
                ) as temp_dir:
                    root = Path(temp_dir)
                    attacker = root / "attacker"
                    attacker.mkdir()
                    (attacker / filename).write_text(payload, encoding="utf-8")
                    canary = root / "import-canary"
                    config = root / f"{adapter_name} config"
                    environment = {
                        "HOME": str(root / "home"),
                        config_variable: str(config),
                        "ATLAS_IMPORT_CANARY": str(canary),
                    }
                    cwd = REPO_ROOT
                    if vector == "cwd":
                        cwd = attacker
                    else:
                        environment["PYTHONPATH"] = str(attacker)
                    result = run_command(
                        [REPO_ROOT / relative_script],
                        cwd=cwd,
                        env=environment,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertFalse(
                        canary.exists(),
                        f"{adapter_name} embedded Python imported {attack_name}",
                    )
                    self.assertTrue(
                        (config / "skills" / "map-project" / "SKILL.md").is_file()
                    )

    def test_installers_reject_group_writable_diff_outside_repository(self) -> None:
        cases = (
            ("codex", Path("scripts/install.sh"), "CODEX_HOME"),
            ("claude", Path("scripts/install-claude.sh"), "CLAUDE_CONFIG_DIR"),
        )
        real_diff = shutil.which("diff")
        self.assertIsNotNone(real_diff, "group-writable diff regression requires system diff")
        for adapter_name, relative_script, config_variable in cases:
            with self.subTest(adapter=adapter_name), tempfile.TemporaryDirectory(
                prefix=f"atlas group writable diff {adapter_name} "
            ) as temp_dir:
                root = Path(temp_dir)
                fake_bin = root / "host-tools" / "bin"
                canary = root / f"{adapter_name}-diff-executed"
                fake_diff = self.write_fake_diff(
                    fake_bin,
                    f": > {str(canary)!r}\nexec \"$ATLAS_TEST_REAL_DIFF\" \"$@\"",
                )
                fake_diff.chmod(0o775)
                config = root / f"{adapter_name} config"
                result = run_command(
                    [relative_script],
                    env={
                        "HOME": str(root / "home"),
                        config_variable: str(config),
                        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                        "ATLAS_TEST_REAL_DIFF": str(real_diff),
                    },
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("unsafe", result.stderr.lower())
                self.assertFalse(
                    canary.exists(),
                    f"{adapter_name} installer launched group-writable diff",
                )

    def test_installers_reject_diff_under_group_writable_ancestor(self) -> None:
        cases = (
            ("codex", Path("scripts/install.sh"), "CODEX_HOME"),
            ("claude", Path("scripts/install-claude.sh"), "CLAUDE_CONFIG_DIR"),
        )
        for adapter_name, relative_script, config_variable in cases:
            with self.subTest(adapter=adapter_name), tempfile.TemporaryDirectory(
                prefix=f"atlas group writable diff parent {adapter_name} "
            ) as temp_dir:
                root = Path(temp_dir)
                unsafe_parent = root / "group-writable-parent"
                fake_bin = unsafe_parent / "bin"
                canary = root / f"{adapter_name}-diff-executed"
                fake_diff = self.write_fake_diff(
                    fake_bin,
                    f": > {str(canary)!r}\nexit 97",
                )
                fake_diff.chmod(0o755)
                unsafe_parent.chmod(0o775)
                self.assertEqual(stat.S_IMODE(unsafe_parent.stat().st_mode), 0o775)
                config = root / f"{adapter_name} config"
                result = run_command(
                    [relative_script],
                    env={
                        "HOME": str(root / "home"),
                        config_variable: str(config),
                        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                    },
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("unsafe", result.stderr.lower())
                self.assertFalse(
                    canary.exists(),
                    f"{adapter_name} installer launched diff below a 0775 ancestor",
                )

    def test_installers_reject_python_under_group_writable_ancestor(self) -> None:
        cases = (
            ("codex", Path("scripts/install.sh"), "CODEX_HOME"),
            ("claude", Path("scripts/install-claude.sh"), "CLAUDE_CONFIG_DIR"),
        )
        for adapter_name, relative_script, config_variable in cases:
            with self.subTest(adapter=adapter_name), tempfile.TemporaryDirectory(
                prefix=f"atlas group writable python parent {adapter_name} "
            ) as temp_dir:
                root = Path(temp_dir)
                unsafe_parent = root / "group-writable-parent"
                fake_bin = unsafe_parent / "bin"
                canary = root / f"{adapter_name}-python-executed"
                fake_python = self.write_fake_command(
                    fake_bin,
                    "python3",
                    f": > {str(canary)!r}\nexit 97",
                )
                fake_python.chmod(0o755)
                unsafe_parent.chmod(0o775)
                self.assertEqual(stat.S_IMODE(unsafe_parent.stat().st_mode), 0o775)
                config = root / f"{adapter_name} config"
                result = run_command(
                    [relative_script],
                    env={
                        "HOME": str(root / "home"),
                        config_variable: str(config),
                        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                    },
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("unsafe", result.stderr.lower())
                self.assertFalse(
                    canary.exists(),
                    f"{adapter_name} installer launched python below a 0775 ancestor",
                )

    def test_installers_reject_diff_under_nonsticky_world_writable_ancestor(
        self,
    ) -> None:
        cases = (
            ("codex", Path("scripts/install.sh"), "CODEX_HOME"),
            ("claude", Path("scripts/install-claude.sh"), "CLAUDE_CONFIG_DIR"),
        )
        for adapter_name, relative_script, config_variable in cases:
            with self.subTest(adapter=adapter_name), tempfile.TemporaryDirectory(
                prefix=f"atlas world writable diff parent {adapter_name} "
            ) as temp_dir:
                root = Path(temp_dir)
                unsafe_parent = root / "world-writable-parent"
                fake_bin = unsafe_parent / "bin"
                canary = root / f"{adapter_name}-diff-executed"
                fake_diff = self.write_fake_diff(
                    fake_bin,
                    f": > {str(canary)!r}\nexit 97",
                )
                fake_diff.chmod(0o755)
                unsafe_parent.chmod(0o777)
                self.assertEqual(stat.S_IMODE(unsafe_parent.stat().st_mode), 0o777)
                config = root / f"{adapter_name} config"
                result = run_command(
                    [relative_script],
                    env={
                        "HOME": str(root / "home"),
                        config_variable: str(config),
                        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                    },
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("unsafe", result.stderr.lower())
                self.assertFalse(
                    canary.exists(),
                    f"{adapter_name} installer launched diff below a non-sticky 0777 ancestor",
                )

    def test_installer_source_contract_rejects_untrusted_executable_owners(self) -> None:
        source = read_text(self, REPO_ROOT / "scripts" / "install.sh")
        required_fragments = (
            "'%u %g %p %l'",
            "'%u %g %a %h'",
            '[[ "$stat_owner" != "0" && "$stat_owner" != "$EUID" ]]',
            "((python_mode_value & 0022))",
            "is_trusted_executable_link_count",
            "/opt/homebrew/Cellar",
            "/usr/local/Cellar",
            "/home/linuxbrew/.linuxbrew/Cellar",
            '[[ "$owner" == "0" || "$owner" == "$EUID" ]]',
            'is_process_group "$group"',
            "! ((mode_value & 0002))",
            "trusted_owners = {0}",
            "trusted_owners.add(effective_uid_getter())",
            'getattr(metadata, "st_uid", None) not in trusted_owners',
            "metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)",
            "is_trusted_sticky_directory",
            "is_trusted_executable_ancestor",
            '"$python_executable" -I -',
        )
        for fragment in required_fragments:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, source)

    def test_bootstrap_homebrew_cellar_exception_is_narrow(self) -> None:
        source = read_text(self, REPO_ROOT / "scripts" / "install.sh")

        def extract_function(name: str) -> str:
            marker = f"{name}() {{"
            self.assertEqual(source.count(marker), 1)
            start = source.index(marker)
            return source[start : source.index("\n}", start) + len("\n}")]

        harness = (
            "set -euo pipefail\n"
            f"{extract_function('is_process_group')}\n"
            f"{extract_function('is_trusted_homebrew_cellar_directory')}\n"
            'mode_value=$((8#$4))\n'
            'if is_trusted_homebrew_cellar_directory "$1" "$2" "$3" "$mode_value"; then\n'
            "  builtin printf 'safe\\n'\n"
            "else\n"
            "  builtin printf 'unsafe\\n' >&2\n"
            "  exit 1\n"
            "fi\n"
        )
        effective_uid = os.geteuid()
        process_group = os.getegid()
        foreign_uid = next(
            uid for uid in range(1, 10_000) if uid not in {0, effective_uid}
        )
        current_groups = {*os.getgroups(), process_group}
        foreign_group = next(
            gid for gid in range(1, 10_000) if gid not in current_groups
        )
        cases = (
            ("/opt/homebrew/Cellar", effective_uid, process_group, "775", True),
            ("/usr/local/Cellar", 0, process_group, "775", True),
            (
                "/home/linuxbrew/.linuxbrew/Cellar",
                effective_uid,
                process_group,
                "775",
                True,
            ),
            ("/tmp/homebrew/Cellar", effective_uid, process_group, "775", False),
            ("/opt/homebrew/Cellar", foreign_uid, process_group, "775", False),
            ("/opt/homebrew/Cellar", effective_uid, foreign_group, "775", False),
            ("/opt/homebrew/Cellar", effective_uid, process_group, "777", False),
        )
        for path, owner, group, mode, accepted in cases:
            with self.subTest(
                path=path,
                owner=owner,
                group=group,
                mode=mode,
            ):
                result = run_command(
                    [
                        "/bin/bash",
                        "-p",
                        "-c",
                        harness,
                        "homebrew-cellar-harness",
                        path,
                        str(owner),
                        str(group),
                        mode,
                    ]
                )
                if accepted:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, "safe\n")
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("unsafe", result.stderr.lower())

    def test_bootstrap_sticky_directory_exception_requires_a_trusted_owner(
        self,
    ) -> None:
        source = read_text(self, REPO_ROOT / "scripts" / "install.sh")

        def extract_function(name: str) -> str:
            marker = f"{name}() {{"
            self.assertEqual(source.count(marker), 1)
            start = source.index(marker)
            return source[start : source.index("\n}", start) + len("\n}")]

        harness = (
            "set -euo pipefail\n"
            f"{extract_function('is_process_group')}\n"
            f"{extract_function('is_trusted_homebrew_cellar_directory')}\n"
            f"{extract_function('is_trusted_sticky_directory')}\n"
            f"{extract_function('is_trusted_executable_ancestor')}\n"
            "mode_value=$((8#$4))\n"
            'if is_trusted_executable_ancestor '
            '"$1" "$2" "$3" "$mode_value"; then\n'
            "  builtin printf 'safe\\n'\n"
            "else\n"
            "  builtin printf 'unsafe\\n' >&2\n"
            "  exit 1\n"
            "fi\n"
        )
        effective_uid = os.geteuid()
        foreign_uid = next(
            uid for uid in range(1, 10_000) if uid not in {0, effective_uid}
        )
        process_group = os.getegid()
        cases = (
            ("root-readonly", "/usr", 0, process_group, "755", True),
            (
                "user-readonly",
                "/safe/tools",
                effective_uid,
                process_group,
                "755",
                True,
            ),
            (
                "foreign-readonly",
                "/foreign/tools",
                foreign_uid,
                process_group,
                "755",
                False,
            ),
            (
                "root-sticky-world",
                "/tmp",
                0,
                process_group,
                "1777",
                True,
            ),
            (
                "user-sticky-world",
                "/safe/tmp",
                effective_uid,
                process_group,
                "1777",
                True,
            ),
            (
                "user-sticky-group-only",
                "/safe/group",
                effective_uid,
                process_group,
                "1770",
                False,
            ),
            (
                "foreign-sticky-world",
                "/foreign/tmp",
                foreign_uid,
                process_group,
                "1777",
                False,
            ),
            (
                "user-world-no-sticky",
                "/safe/world",
                effective_uid,
                process_group,
                "777",
                False,
            ),
            (
                "homebrew-group",
                "/opt/homebrew/Cellar",
                effective_uid,
                process_group,
                "775",
                True,
            ),
            (
                "generic-group",
                "/safe/group-tools",
                effective_uid,
                process_group,
                "775",
                False,
            ),
        )
        for label, path, owner, group, mode, accepted in cases:
            with self.subTest(case=label):
                result = run_command(
                    [
                        "/bin/bash",
                        "-p",
                        "-c",
                        harness,
                        "executable-ancestor-harness",
                        path,
                        str(owner),
                        str(group),
                        mode,
                    ]
                )
                if accepted:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, "safe\n")
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("unsafe", result.stderr.lower())

    def test_bootstrap_python_owner_guard_accepts_root_and_euid_only(self) -> None:
        source = read_text(self, REPO_ROOT / "scripts" / "install.sh")
        guard_marker = 'if [[ "$stat_owner" != "0" && "$stat_owner" != "$EUID" ]]'
        self.assertEqual(source.count(guard_marker), 1)
        guard_start = source.index(guard_marker)
        guard_end = source.index("\nfi", guard_start) + len("\nfi")
        exact_owner_guard = source[guard_start:guard_end]
        harness = (
            "set -euo pipefail\n"
            'command_name="owner-guard-harness"\n'
            'stat_owner="$1"\n'
            'stat_mode="$2"\n'
            "python_mode_value=$((8#$stat_mode))\n"
            f"{exact_owner_guard}\n"
            "builtin printf 'safe\\n'\n"
        )
        effective_uid_getter = getattr(os, "geteuid", None)
        self.assertTrue(callable(effective_uid_getter))
        effective_uid = effective_uid_getter()
        foreign_uid = next(
            uid for uid in range(1, 4) if uid not in {0, effective_uid}
        )
        cases = (
            ("root", 0, True),
            ("effective-user", effective_uid, True),
            ("foreign-user", foreign_uid, False),
        )
        for owner_kind, owner_uid, accepted in cases:
            with self.subTest(owner=owner_kind, uid=owner_uid):
                result = run_command(
                    ["/bin/bash", "-p", "-c", harness, "owner-harness", str(owner_uid), "755"]
                )
                if accepted:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, "safe\n")
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("unsafe", result.stderr.lower())
                    self.assertNotIn("safe", result.stdout)

    def test_bootstrap_executable_link_count_policy_is_root_only(self) -> None:
        source = read_text(self, REPO_ROOT / "scripts" / "install.sh")
        function_marker = "is_trusted_executable_link_count() {"
        self.assertEqual(source.count(function_marker), 1)
        function_start = source.index(function_marker)
        function_end = source.index("\n}", function_start) + len("\n}")
        exact_function = source[function_start:function_end]
        harness = (
            "set -euo pipefail\n"
            f"{exact_function}\n"
            'if is_trusted_executable_link_count "$1" "$2"; then\n'
            "  builtin printf 'safe\\n'\n"
            "else\n"
            "  builtin printf 'unsafe\\n' >&2\n"
            "  exit 1\n"
            "fi\n"
        )
        cases = (
            ("root-multilink", "0", "78", True),
            ("root-single-link", "0", "1", True),
            ("user-single-link", "501", "1", True),
            ("user-multilink", "501", "2", False),
            ("zero-links", "0", "0", False),
            ("non-numeric-links", "0", "many", False),
        )
        for label, owner, link_count, accepted in cases:
            with self.subTest(case=label):
                result = run_command(
                    [
                        "/bin/bash",
                        "-p",
                        "-c",
                        harness,
                        "link-count-harness",
                        owner,
                        link_count,
                    ]
                )
                if accepted:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, "safe\n")
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("unsafe", result.stderr.lower())

    def test_embedded_diff_owner_guard_accepts_root_and_euid_only(self) -> None:
        source = read_text(self, REPO_ROOT / "scripts" / "install.sh")
        heredoc_marker = "<<'PY'\n"
        self.assertEqual(source.count(heredoc_marker), 1)
        embedded_start = source.index(heredoc_marker) + len(heredoc_marker)
        embedded_end = source.index("\nPY\n", embedded_start)
        embedded_source = source[embedded_start:embedded_end]
        parsed = ast.parse(embedded_source, filename="install.sh embedded Python")
        required_names = {
            "InstallFailure",
            "stat_identity",
            "has_ancestor_identity",
            "is_trusted_homebrew_cellar_directory",
            "is_trusted_sticky_directory",
            "is_trusted_executable_ancestor",
            "is_trusted_executable_link_count",
            "host_executable_name_matches",
            "executable_path_is_link",
            "executable_symlink_chain",
            "trusted_external_executable",
        }
        owning_nodes = [
            node
            for node in parsed.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef))
            and node.name in required_names
        ]
        self.assertEqual({node.name for node in owning_nodes}, required_names)
        owning_module = ast.Module(body=owning_nodes, type_ignores=[])
        namespace = {
            "os": os,
            "Path": Path,
            "shutil": shutil,
            "stat": stat,
        }
        exec(
            compile(
                owning_module,
                filename="install.sh embedded trusted executable",
                mode="exec",
            ),
            namespace,
        )
        trusted_external_executable = namespace["trusted_external_executable"]
        trusted_homebrew_cellar = namespace[
            "is_trusted_homebrew_cellar_directory"
        ]
        trusted_sticky_directory = namespace["is_trusted_sticky_directory"]
        trusted_executable_ancestor = namespace[
            "is_trusted_executable_ancestor"
        ]
        trusted_executable_link_count = namespace[
            "is_trusted_executable_link_count"
        ]
        install_failure = namespace["InstallFailure"]
        namespace["source_skill"] = os.fspath(
            REPO_ROOT / "adapters" / "codex" / "skills" / "map-project"
        )

        mocked_effective_uid = 42_424
        foreign_uid = mocked_effective_uid + 1
        process_group = 8_080
        safe_cellar_metadata = mock.Mock(
            st_uid=mocked_effective_uid,
            st_gid=process_group,
            st_mode=stat.S_IFDIR | 0o775,
        )
        for cellar in (
            Path("/opt/homebrew/Cellar"),
            Path("/usr/local/Cellar"),
            Path("/home/linuxbrew/.linuxbrew/Cellar"),
        ):
            with self.subTest(cellar=cellar):
                self.assertTrue(
                    trusted_homebrew_cellar(
                        cellar,
                        safe_cellar_metadata,
                        {0, mocked_effective_uid},
                        {process_group},
                    )
                )
        self.assertFalse(
            trusted_homebrew_cellar(
                Path("/tmp/homebrew/Cellar"),
                safe_cellar_metadata,
                {0, mocked_effective_uid},
                {process_group},
            )
        )
        self.assertTrue(
            trusted_sticky_directory(
                mock.Mock(
                    st_uid=mocked_effective_uid,
                    st_mode=stat.S_IFDIR | 0o1777,
                ),
                {0, mocked_effective_uid},
            )
        )
        self.assertFalse(
            trusted_sticky_directory(
                mock.Mock(
                    st_uid=foreign_uid,
                    st_mode=stat.S_IFDIR | 0o1777,
                ),
                {0, mocked_effective_uid},
            )
        )
        safe_readonly_metadata = mock.Mock(
            st_uid=mocked_effective_uid,
            st_gid=process_group,
            st_mode=stat.S_IFDIR | 0o755,
        )
        foreign_readonly_metadata = mock.Mock(
            st_uid=foreign_uid,
            st_gid=process_group,
            st_mode=stat.S_IFDIR | 0o755,
        )
        self.assertTrue(
            trusted_executable_ancestor(
                Path("/safe/tools"),
                safe_readonly_metadata,
                {0, mocked_effective_uid},
                {process_group},
            )
        )
        self.assertFalse(
            trusted_executable_ancestor(
                Path("/foreign/tools"),
                foreign_readonly_metadata,
                {0, mocked_effective_uid},
                {process_group},
            )
        )
        self.assertFalse(
            trusted_sticky_directory(
                mock.Mock(
                    st_uid=mocked_effective_uid,
                    st_mode=stat.S_IFDIR | 0o777,
                ),
                {0, mocked_effective_uid},
            )
        )
        for unsafe_metadata in (
            mock.Mock(
                st_uid=foreign_uid,
                st_gid=process_group,
                st_mode=stat.S_IFDIR | 0o775,
            ),
            mock.Mock(
                st_uid=mocked_effective_uid,
                st_gid=process_group + 1,
                st_mode=stat.S_IFDIR | 0o775,
            ),
            mock.Mock(
                st_uid=mocked_effective_uid,
                st_gid=process_group,
                st_mode=stat.S_IFDIR | 0o777,
            ),
        ):
            with self.subTest(
                uid=unsafe_metadata.st_uid,
                gid=unsafe_metadata.st_gid,
                mode=stat.S_IMODE(unsafe_metadata.st_mode),
            ):
                self.assertFalse(
                    trusted_homebrew_cellar(
                        Path("/opt/homebrew/Cellar"),
                        unsafe_metadata,
                        {0, mocked_effective_uid},
                        {process_group},
                    )
                )
        self.assertTrue(
            trusted_executable_link_count(
                mock.Mock(st_uid=0, st_nlink=78)
            )
        )
        self.assertTrue(
            trusted_executable_link_count(
                mock.Mock(st_uid=mocked_effective_uid, st_nlink=1)
            )
        )
        for invalid_metadata in (
            mock.Mock(st_uid=mocked_effective_uid, st_nlink=2),
            mock.Mock(st_uid=0, st_nlink=0),
            mock.Mock(st_uid=0, st_nlink=True),
            mock.Mock(st_uid=0, st_nlink=1.0),
            mock.Mock(st_uid=0, spec=["st_uid"]),
        ):
            with self.subTest(link_count=repr(getattr(invalid_metadata, "st_nlink", None))):
                self.assertFalse(trusted_executable_link_count(invalid_metadata))
        cases = (
            ("root-multilink", 0, 78, True),
            ("effective-user", mocked_effective_uid, 1, True),
            ("effective-user-multilink", mocked_effective_uid, 2, False),
            ("foreign-user", foreign_uid, 1, False),
        )
        with tempfile.TemporaryDirectory(prefix="atlas mocked diff owner ") as temp_dir:
            executable = self.write_fake_diff(Path(temp_dir) / "bin", "exit 0")
            resolved_executable = executable.resolve(strict=True)
            real_path_stat = Path.stat
            for owner_kind, owner_uid, link_count, accepted in cases:
                with self.subTest(owner=owner_kind, uid=owner_uid):

                    def stat_with_mocked_owner(
                        path: Path,
                        *args: object,
                        **kwargs: object,
                    ) -> os.stat_result:
                        metadata = real_path_stat(path, *args, **kwargs)
                        values = list(metadata)
                        if path == resolved_executable:
                            values[3] = link_count
                            values[4] = owner_uid
                            return os.stat_result(values)
                        if path in resolved_executable.parents:
                            values[4] = 0
                            return os.stat_result(values)
                        return metadata

                    with (
                        mock.patch.object(
                            Path,
                            "stat",
                            new=stat_with_mocked_owner,
                        ),
                        mock.patch.object(
                            os,
                            "geteuid",
                            return_value=mocked_effective_uid,
                        ),
                        mock.patch.object(
                            shutil,
                            "which",
                            return_value=os.fspath(executable),
                        ),
                    ):
                        if accepted:
                            self.assertEqual(
                                trusted_external_executable("diff"),
                                os.fspath(resolved_executable),
                            )
                        else:
                            with self.assertRaises(install_failure):
                                trusted_external_executable("diff")

            foreign_parent = resolved_executable.parent.parent

            def stat_with_foreign_parent(
                path: Path,
                *args: object,
                **kwargs: object,
            ) -> os.stat_result:
                metadata = real_path_stat(path, *args, **kwargs)
                values = list(metadata)
                if path == resolved_executable:
                    values[4] = mocked_effective_uid
                elif path in resolved_executable.parents:
                    values[4] = 0
                if path == foreign_parent:
                    values[4] = foreign_uid
                return os.stat_result(values)

            with (
                mock.patch.object(
                    Path,
                    "stat",
                    new=stat_with_foreign_parent,
                ),
                mock.patch.object(
                    os,
                    "geteuid",
                    return_value=mocked_effective_uid,
                ),
                mock.patch.object(
                    shutil,
                    "which",
                    return_value=os.fspath(executable),
                ),
            ):
                with self.assertRaises(install_failure):
                    trusted_external_executable("diff")

        if os.name == "posix":
            tmp_root = Path("/tmp")
            try:
                tmp_mode = tmp_root.stat().st_mode
            except OSError:
                tmp_mode = 0
            if (
                tmp_mode & stat.S_ISVTX
                and tmp_mode & stat.S_IWGRP
                and tmp_mode & stat.S_IWOTH
            ):
                with tempfile.TemporaryDirectory(
                    prefix="atlas embedded sticky diff ",
                    dir=tmp_root,
                ) as temp_dir:
                    executable = self.write_fake_diff(
                        Path(temp_dir) / "bin",
                        "exit 0",
                    )
                    with mock.patch.object(
                        shutil,
                        "which",
                        return_value=os.fspath(executable),
                    ):
                        self.assertEqual(
                            trusted_external_executable("diff"),
                            os.fspath(executable.resolve(strict=True)),
                        )

        real_diff_text = shutil.which("diff")
        real_shell_text = shutil.which("sh")
        self.assertIsNotNone(
            real_diff_text,
            "embedded PATH provenance regression requires diff",
        )
        self.assertIsNotNone(
            real_shell_text,
            "embedded semantic-name regression requires a shell",
        )
        real_diff = Path(real_diff_text).resolve(strict=True)
        real_shell = Path(real_shell_text).resolve(strict=True)
        original_source_skill = namespace["source_skill"]
        try:
            with tempfile.TemporaryDirectory(
                prefix="atlas embedded path directory alias "
            ) as temp_dir:
                root = Path(temp_dir)
                repository = root / "repository"
                fake_source_skill = (
                    repository
                    / "adapters"
                    / "codex"
                    / "skills"
                    / "map-project"
                )
                (repository / ".git").mkdir(parents=True)
                fake_source_skill.mkdir(parents=True)
                repository_bin = repository / "bin"
                repository_bin.mkdir()
                (repository_bin / "diff").symlink_to(real_diff)
                external_alias = root / "external-path-alias"
                external_alias.symlink_to(
                    repository_bin,
                    target_is_directory=True,
                )
                namespace["source_skill"] = os.fspath(fake_source_skill)
                with mock.patch.object(
                    shutil,
                    "which",
                    return_value=os.fspath(external_alias / "diff"),
                ):
                    with self.assertRaises(install_failure):
                        trusted_external_executable("diff")

            with tempfile.TemporaryDirectory(
                prefix="atlas embedded repository symlink relay "
            ) as temp_dir:
                root = Path(temp_dir)
                repository = root / "repository"
                fake_source_skill = (
                    repository
                    / "adapters"
                    / "codex"
                    / "skills"
                    / "map-project"
                )
                (repository / ".git").mkdir(parents=True)
                fake_source_skill.mkdir(parents=True)
                repository_bin = repository / "bin"
                repository_bin.mkdir()
                relay = repository_bin / "diff-relay"
                relay.symlink_to(real_diff)
                candidate = root / "host-tools" / "diff"
                candidate.parent.mkdir()
                candidate.symlink_to(relay)
                namespace["source_skill"] = os.fspath(fake_source_skill)
                with mock.patch.object(
                    shutil,
                    "which",
                    return_value=os.fspath(candidate),
                ):
                    with self.assertRaises(install_failure):
                        trusted_external_executable("diff")

            with tempfile.TemporaryDirectory(
                prefix="atlas embedded directory symlink relay "
            ) as temp_dir:
                root = Path(temp_dir)
                repository = root / "repository"
                fake_source_skill = (
                    repository
                    / "adapters"
                    / "codex"
                    / "skills"
                    / "map-project"
                )
                (repository / ".git").mkdir(parents=True)
                fake_source_skill.mkdir(parents=True)
                directory_relay = repository / "directory-relay"
                directory_relay.symlink_to(
                    os.path.relpath(real_diff.parent, directory_relay.parent),
                    target_is_directory=True,
                )
                external_alias = root / "external-directory-alias"
                external_alias.symlink_to(
                    os.path.relpath(directory_relay, external_alias.parent),
                    target_is_directory=True,
                )
                namespace["source_skill"] = os.fspath(fake_source_skill)
                with mock.patch.object(
                    shutil,
                    "which",
                    return_value=os.fspath(external_alias / real_diff.name),
                ):
                    with self.assertRaises(install_failure):
                        trusted_external_executable("diff")

            with tempfile.TemporaryDirectory(
                prefix="atlas embedded host tool symlink cycle "
            ) as temp_dir:
                root = Path(temp_dir)
                fake_source_skill = (
                    root
                    / "repository"
                    / "adapters"
                    / "codex"
                    / "skills"
                    / "map-project"
                )
                fake_source_skill.mkdir(parents=True)
                candidate = root / "host-tools" / "diff"
                relay = candidate.parent / "relay"
                candidate.parent.mkdir()
                candidate.symlink_to(relay)
                relay.symlink_to(candidate)
                namespace["source_skill"] = os.fspath(fake_source_skill)
                with mock.patch.object(
                    shutil,
                    "which",
                    return_value=os.fspath(candidate),
                ):
                    previous_handler = signal.getsignal(signal.SIGALRM)

                    def cycle_timeout(
                        _signum: int,
                        _frame: object,
                    ) -> None:
                        raise AssertionError(
                            "embedded executable symlink cycle did not terminate"
                        )

                    signal.signal(signal.SIGALRM, cycle_timeout)
                    signal.setitimer(signal.ITIMER_REAL, 5)
                    try:
                        with self.assertRaises(install_failure):
                            trusted_external_executable("diff")
                    finally:
                        signal.setitimer(signal.ITIMER_REAL, 0)
                        signal.signal(signal.SIGALRM, previous_handler)

            with tempfile.TemporaryDirectory(
                prefix="atlas embedded mismatched host tool "
            ) as temp_dir:
                root = Path(temp_dir)
                fake_source_skill = (
                    root
                    / "repository"
                    / "adapters"
                    / "codex"
                    / "skills"
                    / "map-project"
                )
                fake_source_skill.mkdir(parents=True)
                candidate = root / "host-tools" / "diff"
                candidate.parent.mkdir()
                candidate.symlink_to(real_shell)
                namespace["source_skill"] = os.fspath(fake_source_skill)
                with mock.patch.object(
                    shutil,
                    "which",
                    return_value=os.fspath(candidate),
                ):
                    with self.assertRaises(install_failure):
                        trusted_external_executable("diff")
        finally:
            namespace["source_skill"] = original_source_skill

    def test_bootstrap_executable_symlink_cycle_is_bounded_and_rejected(
        self,
    ) -> None:
        source = read_text(self, REPO_ROOT / "scripts" / "install.sh")

        def extract_function(name: str) -> str:
            marker = f"{name}() {{"
            self.assertEqual(source.count(marker), 1)
            start = source.index(marker)
            return source[start : source.index("\n}", start) + len("\n}")]

        trusted_readlink = shutil.which("readlink")
        self.assertIsNotNone(
            trusted_readlink,
            "bootstrap symlink-cycle regression requires readlink",
        )
        harness = (
            "set -euo pipefail\n"
            'trusted_readlink="$1"\n'
            "physical_directory() {\n"
            "  builtin cd -P -- \"$1\"\n"
            "  builtin pwd -P\n"
            "}\n"
            f"{extract_function('normalize_absolute_path')}\n"
            "path_has_source_repository_ancestor() { return 1; }\n"
            f"{extract_function('rewrite_first_symlink')}\n"
            f"{extract_function('resolve_executable_path')}\n"
            'if resolve_executable_path "$2"; then\n'
            "  builtin printf 'unexpected-success\\n'\n"
            "  exit 91\n"
            "fi\n"
            "builtin printf 'unsafe\\n' >&2\n"
        )
        with tempfile.TemporaryDirectory(
            prefix="atlas bootstrap executable cycle "
        ) as temp_dir:
            root = Path(temp_dir)
            candidate = root / "python3"
            relay = root / "python-relay"
            candidate.symlink_to(relay)
            relay.symlink_to(candidate)
            result = run_command(
                [
                    "/bin/bash",
                    "-p",
                    "-c",
                    harness,
                    "bootstrap-cycle-harness",
                    trusted_readlink,
                    candidate,
                ],
                timeout=5,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("unsafe", result.stderr.lower())

    def test_stat_owner_and_mode_parses_gnu_fallback_output(self) -> None:
        source = read_text(self, REPO_ROOT / "scripts" / "install.sh")
        function_marker = "stat_owner_and_mode() {"
        self.assertEqual(source.count(function_marker), 1)
        function_start = source.index(function_marker)
        function_end = source.index("\n}", function_start) + len("\n}")
        exact_function = source[function_start:function_end]
        with tempfile.TemporaryDirectory(
            prefix="atlas gnu owner group mode stat "
        ) as temp_dir:
            root = Path(temp_dir)
            call_log = root / "stat-calls"
            fake_stat = self.write_fake_command(
                root / "bin",
                "stat",
                (
                    'printf "%s\\n" "$*" >> "$ATLAS_TEST_STAT_LOG"\n'
                    'if [ "$1" = "-f" ]; then exit 64; fi\n'
                    'if [ "$1" = "-c" ] && [ "$2" = "%u %g %a %h" ] && '
                    '[ "$3" = "--" ] && [ "$4" = "$ATLAS_TEST_STAT_TARGET" ]; then\n'
                    '  printf "123 456 755 2\\n"\n'
                    "  exit 0\n"
                    "fi\n"
                    "exit 65"
                ),
            )
            harness = (
                "set -euo pipefail\n"
                'trusted_stat="$1"\n'
                'target="$2"\n'
                f"{exact_function}\n"
                'stat_owner_and_mode "$target"\n'
                'builtin printf "%s:%s:%s:%s\\n" '
                '"$stat_owner" "$stat_group" "$stat_mode" "$stat_link_count"\n'
            )
            result = run_command(
                [
                    "/bin/bash",
                    "-p",
                    "-c",
                    harness,
                    "stat-harness",
                    os.fspath(fake_stat),
                    os.fspath(REPO_ROOT),
                ],
                env={
                    "ATLAS_TEST_STAT_LOG": os.fspath(call_log),
                    "ATLAS_TEST_STAT_TARGET": os.fspath(REPO_ROOT),
                },
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "123:456:755:2\n")
            self.assertEqual(
                call_log.read_text(encoding="utf-8").splitlines(),
                [
                    f"-f %u %g %p %l -- {REPO_ROOT}",
                    f"-c %u %g %a %h -- {REPO_ROOT}",
                ],
            )

    def test_installers_reject_executables_owned_by_untrusted_user_when_chown_is_available(
        self,
    ) -> None:
        """Exercise owner checks dynamically when the runner can create a foreign owner."""

        installers = (
            ("codex", Path("scripts/install.sh"), "CODEX_HOME"),
            ("claude", Path("scripts/install-claude.sh"), "CLAUDE_CONFIG_DIR"),
        )
        effective_uid_getter = getattr(os, "geteuid", None)
        current_uid = effective_uid_getter() if callable(effective_uid_getter) else -1
        untrusted_uid = 1 if current_uid != 1 else 2
        chown = getattr(os, "chown", None)
        for tool_name in ("python3", "diff"):
            for adapter_name, relative_script, config_variable in installers:
                with self.subTest(
                    tool=tool_name,
                    adapter=adapter_name,
                ), tempfile.TemporaryDirectory(
                    prefix=f"atlas foreign owner {tool_name} {adapter_name} "
                ) as temp_dir:
                    root = Path(temp_dir)
                    fake_bin = root / "host-tools" / "bin"
                    canary = root / f"{adapter_name}-{tool_name}-executed"
                    command = self.write_fake_command(
                        fake_bin,
                        tool_name,
                        f": > {str(canary)!r}\nexit 97",
                    )
                    if not callable(chown):
                        self.skipTest(
                            "dynamic owner regression requires os.chown; "
                            "the source-contract test remains portable"
                        )
                    try:
                        chown(command, untrusted_uid, -1)
                    except (NotImplementedError, PermissionError):
                        self.skipTest(
                            "non-root runners cannot chown a fixture to an unrelated UID; "
                            "the source-contract test remains portable"
                        )
                    self.assertEqual(command.stat().st_uid, untrusted_uid)
                    config = root / f"{adapter_name} config"
                    result = run_command(
                        [relative_script],
                        env={
                            "HOME": str(root / "home"),
                            config_variable: str(config),
                            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                        },
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("unsafe", result.stderr.lower())
                    self.assertFalse(
                        canary.exists(),
                        f"{adapter_name} installer launched foreign-owned {tool_name}",
                    )

    def test_stat_identity_parses_gnu_fallback_output(self) -> None:
        source = read_text(self, REPO_ROOT / "scripts" / "install.sh")
        function_marker = "stat_identity() {"
        self.assertEqual(source.count(function_marker), 1)
        function_start = source.index(function_marker)
        function_end = source.index("\n}", function_start) + len("\n}")
        exact_stat_identity = source[function_start:function_end]
        with tempfile.TemporaryDirectory(prefix="atlas gnu stat fallback ") as temp_dir:
            root = Path(temp_dir)
            call_log = root / "stat-calls"
            fake_stat = self.write_fake_command(
                root / "bin",
                "stat",
                (
                    'printf "%s\\n" "$*" >> "$ATLAS_TEST_STAT_LOG"\n'
                    'if [ "$1" = "-f" ]; then exit 64; fi\n'
                    'if [ "$1" = "-c" ] && [ "$2" = "%d %i" ] && '
                    '[ "$3" = "--" ] && [ "$4" = "$ATLAS_TEST_STAT_TARGET" ]; then\n'
                    '  printf "12345 67890\\n"\n'
                    "  exit 0\n"
                    "fi\n"
                    "exit 65"
                ),
            )
            harness = (
                "set -euo pipefail\n"
                'trusted_stat="$1"\n'
                'target="$2"\n'
                f"{exact_stat_identity}\n"
                'stat_identity "$target"\n'
                'builtin printf "%s:%s\\n" "$stat_device" "$stat_inode"\n'
            )
            result = run_command(
                [
                    "/bin/bash",
                    "-p",
                    "-c",
                    harness,
                    "stat-harness",
                    os.fspath(fake_stat),
                    os.fspath(REPO_ROOT),
                ],
                env={
                    "ATLAS_TEST_STAT_LOG": os.fspath(call_log),
                    "ATLAS_TEST_STAT_TARGET": os.fspath(REPO_ROOT),
                },
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "12345:67890\n")
            self.assertEqual(
                call_log.read_text(encoding="utf-8").splitlines(),
                [
                    f"-f %d %i -- {REPO_ROOT}",
                    f"-c %d %i -- {REPO_ROOT}",
                ],
            )

    def test_installers_do_not_execute_repository_path_tools_before_diff_guard(self) -> None:
        installers = (
            ("codex", Path("scripts/install.sh"), "CODEX_HOME"),
            ("claude", Path("scripts/install-claude.sh"), "CLAUDE_CONFIG_DIR"),
        )
        tools = (
            ("bash", (), True),
            ("cat", ("--help",), True),
            ("dirname", (), True),
            ("python3", (), False),
        )
        for adapter_name, relative_script, config_variable in installers:
            for tool_name, arguments, expect_success in tools:
                with self.subTest(
                    adapter=adapter_name, tool=tool_name
                ), tempfile.TemporaryDirectory(
                    prefix=f"atlas early path tool {adapter_name} {tool_name} "
                ) as temp_dir:
                    root = Path(temp_dir)
                    repository = root / "untrusted repository"
                    (repository / ".git").mkdir(parents=True)
                    clone = repository / "packages" / "project-atlas"
                    shutil.copytree(
                        REPO_ROOT,
                        clone,
                        ignore=shutil.ignore_patterns(
                            ".git", "__pycache__", "*.pyc", ".scratch"
                        ),
                    )
                    fake_bin = repository / "bin"
                    canary = root / f"{adapter_name}-{tool_name}-executed"
                    self.write_fake_command(
                        fake_bin,
                        tool_name,
                        f": > {str(canary)!r}\nexit 97",
                    )
                    config = root / f"{adapter_name} config"
                    result = run_command(
                        [clone / relative_script, *arguments],
                        env={
                            "HOME": str(root / "home"),
                            config_variable: str(config),
                            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                        },
                    )
                    if expect_success:
                        self.assertEqual(result.returncode, 0, result.stderr)
                        if arguments:
                            self.assertIn("Usage:", result.stdout)
                        else:
                            self.assertTrue(
                                (config / "skills" / "map-project" / "SKILL.md").is_file()
                            )
                    else:
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn("unsafe", result.stderr.lower())
                    self.assertFalse(
                        canary.exists(),
                        f"{adapter_name} installer launched repository-controlled {tool_name}",
                    )

    def test_installers_reject_unsafe_bootstrap_python_outside_repository(self) -> None:
        installers = (
            ("codex", Path("scripts/install.sh"), "CODEX_HOME"),
            ("claude", Path("scripts/install-claude.sh"), "CLAUDE_CONFIG_DIR"),
        )
        for adapter_name, relative_script, config_variable in installers:
            for scenario in (
                "group-writable",
                "world-writable",
                "world-writable-parent",
                "symlink-into-repository",
                "directory-alias-into-repository",
                "directory-relay-through-repository",
                "leaf-relay-through-repository",
            ):
                with self.subTest(
                    adapter=adapter_name,
                    scenario=scenario,
                ), tempfile.TemporaryDirectory(
                    prefix=f"atlas unsafe python {adapter_name} {scenario} "
                ) as temp_dir:
                    root = Path(temp_dir)
                    repository = root / "untrusted repository"
                    (repository / ".git").mkdir(parents=True)
                    clone = repository / "packages" / "project-atlas"
                    shutil.copytree(
                        REPO_ROOT,
                        clone,
                        ignore=shutil.ignore_patterns(
                            ".git", "__pycache__", "*.pyc", ".scratch"
                        ),
                    )
                    canary = root / f"{adapter_name}-{scenario}-python-executed"
                    unsafe_bin = root / "unsafe-bin"
                    if scenario == "group-writable":
                        self.write_fake_command(
                            unsafe_bin,
                            "python3",
                            f": > {str(canary)!r}\nexit 97",
                        )
                        (unsafe_bin / "python3").chmod(0o775)
                    elif scenario == "world-writable":
                        self.write_fake_command(
                            unsafe_bin,
                            "python3",
                            f": > {str(canary)!r}\nexit 97",
                        )
                        (unsafe_bin / "python3").chmod(0o777)
                    elif scenario == "world-writable-parent":
                        unsafe_bin = root / "world-writable-parent" / "bin"
                        self.write_fake_command(
                            unsafe_bin,
                            "python3",
                            f": > {str(canary)!r}\nexit 97",
                        )
                        unsafe_bin.parent.chmod(0o777)
                    elif scenario == "symlink-into-repository":
                        repository_bin = repository / "bin"
                        self.write_fake_command(
                            repository_bin,
                            "python3",
                            f": > {str(canary)!r}\nexit 97",
                        )
                        unsafe_bin.mkdir()
                        (unsafe_bin / "python3").symlink_to(repository_bin / "python3")
                    elif scenario == "directory-alias-into-repository":
                        repository_bin = repository / "bin"
                        repository_bin.mkdir()
                        (repository_bin / "python3").symlink_to(
                            Path(sys.executable).resolve(strict=True)
                        )
                        unsafe_bin.symlink_to(
                            repository_bin,
                            target_is_directory=True,
                        )
                    elif scenario == "directory-relay-through-repository":
                        trusted_bin = root / "trusted-host" / "bin"
                        trusted_bin.mkdir(parents=True)
                        (trusted_bin / "python3").symlink_to(
                            Path(sys.executable).resolve(strict=True)
                        )
                        repository_relay = repository / "directory-relay"
                        repository_relay.symlink_to(
                            os.path.relpath(
                                trusted_bin,
                                repository_relay.parent,
                            ),
                            target_is_directory=True,
                        )
                        unsafe_bin.symlink_to(
                            os.path.relpath(
                                repository_relay,
                                unsafe_bin.parent,
                            ),
                            target_is_directory=True,
                        )
                    else:
                        repository_bin = repository / "bin"
                        repository_bin.mkdir()
                        relay = repository_bin / "python-relay"
                        relay.symlink_to(Path(sys.executable).resolve(strict=True))
                        unsafe_bin.mkdir()
                        (unsafe_bin / "python3").symlink_to(relay)
                    config = root / f"{adapter_name} config"
                    result = run_command(
                        [clone / relative_script],
                        env={
                            "HOME": str(root / "home"),
                            config_variable: str(config),
                            "PATH": f"{unsafe_bin}{os.pathsep}{os.environ['PATH']}",
                        },
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("unsafe", result.stderr.lower())
                    self.assertFalse(
                        canary.exists(),
                        f"{adapter_name} installer launched unsafe bootstrap python",
                    )

    def test_installers_do_not_depend_on_external_find_for_package_preflight(self) -> None:
        cases = (
            ("codex", REPO_ROOT / "scripts" / "install.sh", "CODEX_HOME"),
            ("claude", REPO_ROOT / "scripts" / "install-claude.sh", "CLAUDE_CONFIG_DIR"),
        )
        for adapter_name, script, config_variable in cases:
            with self.subTest(adapter=adapter_name):
                with tempfile.TemporaryDirectory(
                    prefix=f"atlas no external find {adapter_name} "
                ) as temp_dir:
                    root = Path(temp_dir)
                    fake_bin = root / "fake-bin"
                    self.write_failing_find(fake_bin)
                    config = root / f"{adapter_name} config"
                    env = {
                        "HOME": str(root / "home"),
                        config_variable: str(config),
                        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                    }
                    result = run_command(["bash", script], env=env)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertTrue((config / "skills" / "map-project" / "SKILL.md").is_file())

    def test_codex_installs_into_isolated_codex_home(self) -> None:
        script = REPO_ROOT / "scripts" / "install.sh"
        assert_file(self, script)
        with tempfile.TemporaryDirectory(prefix="atlas codex home ") as temp_dir:
            root = Path(temp_dir)
            result = run_command(
                ["bash", script],
                env={"HOME": str(root / "home"), "CODEX_HOME": str(root / "codex home")},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assert_codex_standalone_installed(root / "codex home" / "skills" / "map-project")

    def test_codex_nested_cleanup_does_not_require_shutil_rmtree_dir_fd(
        self,
    ) -> None:
        script = REPO_ROOT / "scripts" / "install.sh"
        with tempfile.TemporaryDirectory(
            prefix="atlas python310 nested cleanup "
        ) as temp_dir:
            root = Path(temp_dir)
            fake_bin = root / "fake-bin"
            guard = root / "python-rmtree-guard.py"
            guard.write_text(
                "import shutil\n"
                "import sys\n"
                "\n"
                "_original_rmtree = shutil.rmtree\n"
                "\n"
                "def reject_dir_fd(*args, **kwargs):\n"
                "    if 'dir_fd' in kwargs:\n"
                "        raise TypeError(\"rmtree() got an unexpected keyword argument 'dir_fd'\")\n"
                "    return _original_rmtree(*args, **kwargs)\n"
                "\n"
                "shutil.rmtree = reject_dir_fd\n"
                "source = sys.stdin.read()\n"
                "scope = {'__name__': '__main__', '__file__': '<stdin>'}\n"
                "exec(compile(source, '<stdin>', 'exec'), scope)\n",
                encoding="utf-8",
            )
            self.write_fake_command(
                fake_bin,
                "python3",
                'shift 2\n'
                'exec "$ATLAS_REAL_PYTHON" -I "$ATLAS_TEST_PYTHON_GUARD" "$@"',
            )
            config = root / "codex"
            env = {
                "HOME": str(root / "home"),
                "CODEX_HOME": str(config),
                "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                "ATLAS_REAL_PYTHON": sys.executable,
                "ATLAS_TEST_PYTHON_GUARD": str(guard),
            }

            result = run_command(["bash", script], env=env)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assert_codex_standalone_installed(
                config / "skills" / "map-project"
            )
            skills = config / "skills"
            self.assertEqual(list(skills.glob(".map-project.install-*")), [])
            self.assertEqual(list(skills.glob(".map-project.cleanup-*")), [])
            self.assertFalse((skills / ".map-project.install.lock").exists())
            self.assertNotIn(
                "shutil.rmtree(",
                script.read_text(encoding="utf-8"),
            )

    def test_post_promotion_verification_uses_descriptor_anchored_paths(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas descriptor verification ") as temp_dir:
            root = Path(temp_dir)
            fake_bin = root / "fake-bin"
            real_diff = shutil.which("diff")
            self.assertIsNotNone(real_diff)
            log = root / "diff-arguments"
            self.write_fake_diff(
                fake_bin,
                'printf "%s\\t%s\\n" "$2" "$3" >> "$ATLAS_TEST_DIFF_ARGUMENTS"\n'
                'exec "$ATLAS_REAL_DIFF" "$@"',
            )
            result = run_command(
                ["bash", REPO_ROOT / "scripts" / "install.sh"],
                env={
                    "HOME": str(root / "home"),
                    "CODEX_HOME": str(root / "codex"),
                    "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                    "ATLAS_REAL_DIFF": str(real_diff),
                    "ATLAS_TEST_DIFF_ARGUMENTS": str(log),
                },
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(calls), 3)
            post_promotion = calls[-1].split("\t")
            self.assertEqual(len(post_promotion), 2)
            self.assertTrue(
                all(path.startswith("/dev/fd/") for path in post_promotion),
                f"public target path was reopened during verification: {post_promotion}",
            )

    def test_stalled_preflight_diff_times_out_without_mutating_installation(self) -> None:
        cases = (
            ("codex", REPO_ROOT / "scripts" / "install.sh", "CODEX_HOME"),
            ("claude", REPO_ROOT / "scripts" / "install-claude.sh", "CLAUDE_CONFIG_DIR"),
        )
        real_diff = shutil.which("diff")
        self.assertIsNotNone(real_diff, "timeout regression requires the system diff command")
        for stall_call in (1, 2):
            for adapter_name, script, config_variable in cases:
                with self.subTest(
                    adapter=adapter_name, preflight_call=stall_call
                ), tempfile.TemporaryDirectory(
                    prefix=f"atlas stalled preflight {stall_call} {adapter_name} "
                ) as temp_dir:
                    root = Path(temp_dir)
                    config = root / f"{adapter_name} config"
                    target = config / "skills" / "map-project"
                    target.mkdir(parents=True)
                    marker = target / "old-marker.txt"
                    marker.write_text("preserve-original\n", encoding="utf-8")
                    fake_bin = root / "fake-bin"
                    self.write_stalling_diff(fake_bin)
                    stalled_pid_path = root / "stalled-diff-pid"
                    env = {
                        "HOME": str(root / "home"),
                        config_variable: str(config),
                        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                        "ATLAS_REAL_DIFF": str(real_diff),
                        "ATLAS_TEST_DIFF_COUNT": str(root / "diff-count"),
                        "ATLAS_TEST_STALL_CALL": str(stall_call),
                        "ATLAS_TEST_STALLED_DIFF_PID": str(stalled_pid_path),
                        "ATLAS_TEST_DIFF_TIMEOUT_SECONDS": "0.5",
                    }

                    result, elapsed = self.run_installer_until_stalled_diff_timeout(
                        script=script,
                        env=env,
                        stalled_pid_path=stalled_pid_path,
                        phase="preflight",
                    )

                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    self.assertIn("external diff verification timed out", result.stderr)
                    self.assertLess(elapsed, 3.0, "stalled preflight verifier was not bounded")
                    self.assertEqual(marker.read_text(encoding="utf-8"), "preserve-original\n")
                    self.assertFalse((target / "SKILL.md").exists())
                    self.assertFalse((config / "skills" / ".map-project.install.lock").exists())
                    self.assertEqual(list((config / "skills").glob(".map-project.install-*")), [])
                    backup_root = config / ".skill-backups" / "project-atlas"
                    self.assertEqual(list(backup_root.glob("map-project-*")), [])
                    stalled_pid = int(stalled_pid_path.read_text(encoding="utf-8").strip())
                    self.assert_process_group_exited(stalled_pid)

    def test_stalled_post_promotion_diff_times_out_and_restores_previous_version(self) -> None:
        cases = (
            ("codex", REPO_ROOT / "scripts" / "install.sh", "CODEX_HOME"),
            ("claude", REPO_ROOT / "scripts" / "install-claude.sh", "CLAUDE_CONFIG_DIR"),
        )
        real_diff = shutil.which("diff")
        self.assertIsNotNone(real_diff, "timeout regression requires the system diff command")
        for adapter_name, script, config_variable in cases:
            with self.subTest(adapter=adapter_name), tempfile.TemporaryDirectory(
                prefix=f"atlas stalled post-promotion {adapter_name} "
            ) as temp_dir:
                root = Path(temp_dir)
                config = root / f"{adapter_name} config"
                target = config / "skills" / "map-project"
                target.mkdir(parents=True)
                marker = target / "old-marker.txt"
                marker.write_text("preserve-original\n", encoding="utf-8")
                fake_bin = root / "fake-bin"
                self.write_stalling_diff(fake_bin)
                stalled_pid_path = root / "stalled-diff-pid"
                env = {
                    "HOME": str(root / "home"),
                    config_variable: str(config),
                    "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                    "ATLAS_REAL_DIFF": str(real_diff),
                    "ATLAS_TEST_DIFF_COUNT": str(root / "diff-count"),
                    "ATLAS_TEST_STALL_CALL": "3",
                    "ATLAS_TEST_STALLED_DIFF_PID": str(stalled_pid_path),
                    "ATLAS_TEST_DIFF_TIMEOUT_SECONDS": "0.5",
                }

                result, elapsed = self.run_installer_until_stalled_diff_timeout(
                    script=script,
                    env=env,
                    stalled_pid_path=stalled_pid_path,
                    phase="post-promotion",
                )

                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("external diff verification timed out", result.stderr)
                self.assertLess(elapsed, 3.0, "stalled post-promotion verifier was not bounded")
                self.assertEqual(marker.read_text(encoding="utf-8"), "preserve-original\n")
                self.assertFalse((target / "SKILL.md").exists())
                self.assertFalse((config / "skills" / ".map-project.install.lock").exists())
                self.assertEqual(list((config / "skills").glob(".map-project.install-*")), [])
                backup_root = config / ".skill-backups" / "project-atlas"
                self.assertEqual(list(backup_root.glob("map-project-*")), [])
                stalled_pid = int(stalled_pid_path.read_text(encoding="utf-8").strip())
                self.assert_process_group_exited(stalled_pid)

    def test_signal_during_timeout_cleanup_kills_verifier_and_restores_previous_version(
        self,
    ) -> None:
        real_diff = shutil.which("diff")
        self.assertIsNotNone(real_diff, "timeout regression requires the system diff command")
        with tempfile.TemporaryDirectory(prefix="atlas verifier timeout signal ") as temp_dir:
            root = Path(temp_dir)
            config = root / "codex config"
            target = config / "skills" / "map-project"
            target.mkdir(parents=True)
            marker = target / "old-marker.txt"
            marker.write_text("preserve-original\n", encoding="utf-8")
            fake_bin = root / "fake-bin"
            self.write_stalling_diff(fake_bin)
            stalled_pid_path = root / "stalled-diff-pid"
            barrier = root / "timeout-cleanup-barrier"
            release = root / "timeout-cleanup-release"
            env = os.environ.copy()
            env.update(
                {
                    "HOME": str(root / "home"),
                    "CODEX_HOME": str(config),
                    "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                    "ATLAS_REAL_DIFF": str(real_diff),
                    "ATLAS_TEST_DIFF_COUNT": str(root / "diff-count"),
                    "ATLAS_TEST_STALL_CALL": "3",
                    "ATLAS_TEST_STALLED_DIFF_PID": str(stalled_pid_path),
                    "ATLAS_TEST_DIFF_TIMEOUT_SECONDS": "0.5",
                    "ATLAS_TEST_DIFF_TIMEOUT_CLEANUP_BARRIER": str(barrier),
                    "ATLAS_TEST_DIFF_TIMEOUT_CLEANUP_RELEASE": str(release),
                }
            )
            process = subprocess.Popen(
                ["bash", str(REPO_ROOT / "scripts" / "install.sh"), "--force"],
                cwd=REPO_ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.wait_for_process_barrier(
                process,
                barrier,
                "installer never reached the external-diff timeout cleanup gate",
            )
            stalled_process_group = int(stalled_pid_path.read_text(encoding="utf-8").strip())
            installer_python_pid = int(barrier.read_text(encoding="utf-8").strip())
            os.kill(installer_python_pid, signal.SIGTERM)
            release.touch()
            stdout, stderr = process.communicate(timeout=5)

            self.assertEqual(process.returncode, 128 + signal.SIGTERM, stderr or stdout)
            self.assertIn(f"interrupted by signal {signal.SIGTERM}", stderr)
            self.assert_process_group_exited(stalled_process_group)
            self.assertEqual(marker.read_text(encoding="utf-8"), "preserve-original\n")
            self.assertFalse((target / "SKILL.md").exists())
            self.assertFalse((config / "skills" / ".map-project.install.lock").exists())
            self.assertEqual(list((config / "skills").glob(".map-project.install-*")), [])
            backup_root = config / ".skill-backups" / "project-atlas"
            self.assertEqual(list(backup_root.glob("map-project-*")), [])

    def test_installer_rejects_oversized_or_overdeep_packaged_trees(self) -> None:
        for case in ("oversized", "overdeep"):
            with self.subTest(case=case), tempfile.TemporaryDirectory(
                prefix=f"atlas bounded package {case} "
            ) as temp_dir:
                root = Path(temp_dir)
                clone = root / "project-atlas"
                shutil.copytree(REPO_ROOT / "adapters", clone / "adapters")
                shutil.copytree(REPO_ROOT / "scripts", clone / "scripts")
                source = clone / "adapters" / "codex" / "skills" / "map-project"
                if case == "oversized":
                    with (source / "oversized.bin").open("wb") as stream:
                        stream.truncate(4 * 1024 * 1024 + 1)
                    expected = "per-file byte limit"
                else:
                    nested = source
                    for index in range(34):
                        nested = nested / f"d{index:02d}"
                        nested.mkdir()
                    (nested / "leaf.txt").write_text("bounded\n", encoding="utf-8")
                    expected = "directory-depth limit"
                config = root / "codex-home"
                result = run_command(
                    ["bash", clone / "scripts" / "install.sh"],
                    env={"HOME": str(root / "home"), "CODEX_HOME": str(config)},
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)
                self.assertFalse((config / "skills" / "map-project").exists())

    def test_codex_user_scope_installs_only_under_agents_skills(self) -> None:
        script = REPO_ROOT / "scripts" / "install.sh"
        assert_file(self, script)
        with tempfile.TemporaryDirectory(prefix="atlas user home with spaces ") as temp_dir:
            root = Path(temp_dir)
            home = root / "isolated home"
            home.mkdir()
            sentinel = home / "unrelated.txt"
            sentinel.write_text("keep-me\n", encoding="utf-8")
            codex_home = root / "legacy codex home"
            result = run_command(
                ["bash", script, "--user-scope"],
                env={"HOME": str(home), "CODEX_HOME": str(codex_home)},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assert_codex_standalone_installed(home / ".agents" / "skills" / "map-project")
            self.assertFalse((codex_home / "skills" / "map-project").exists())
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep-me\n")

    def test_codex_refuses_overwrite_without_force_and_force_is_explicit(self) -> None:
        script = REPO_ROOT / "scripts" / "install.sh"
        assert_file(self, script)
        with tempfile.TemporaryDirectory(prefix="atlas overwrite codex ") as temp_dir:
            root = Path(temp_dir)
            target = root / "codex home" / "skills" / "map-project"
            target.mkdir(parents=True)
            marker = target / "existing-user-file.txt"
            marker.write_text("preserve\n", encoding="utf-8")
            env = {"HOME": str(root / "home"), "CODEX_HOME": str(root / "codex home")}

            refused = run_command(["bash", script], env=env)
            self.assertNotEqual(refused.returncode, 0, "installer silently overwrote an existing skill")
            self.assertEqual(marker.read_text(encoding="utf-8"), "preserve\n")

            forced = run_command(["bash", script, "--force"], env=env)
            self.assertEqual(forced.returncode, 0, forced.stderr)
            self.assertFalse(marker.exists())
            self.assert_codex_standalone_installed(target)
            backup_root = root / "codex home" / ".skill-backups" / "project-atlas"
            backups = list(backup_root.glob("map-project-*"))
            self.assertEqual(len(backups), 1, "forced install must preserve exactly one old version")
            self.assertEqual(
                (backups[0] / "existing-user-file.txt").read_text(encoding="utf-8"),
                "preserve\n",
            )
            self.assertEqual(
                sorted(path.name for path in target.parent.iterdir()),
                ["map-project"],
                "backup or staging content remained inside the auto-discovered skills directory",
            )

    def test_claude_installer_supports_force(self) -> None:
        script = REPO_ROOT / "scripts" / "install-claude.sh"
        assert_file(self, script)
        result = run_command(["bash", script, "--help"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--force", result.stdout)

    def test_claude_installs_into_isolated_config_directory(self) -> None:
        script = REPO_ROOT / "scripts" / "install-claude.sh"
        assert_file(self, script)
        with tempfile.TemporaryDirectory(prefix="atlas claude config ") as temp_dir:
            root = Path(temp_dir)
            config = root / "claude config with spaces"
            result = run_command(
                ["bash", script],
                env={"HOME": str(root / "home"), "CLAUDE_CONFIG_DIR": str(config)},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assert_bundle_installed(
                CLAUDE_ADAPTER / "skills" / "map-project",
                config / "skills" / "map-project",
            )

    def test_claude_refuses_overwrite_without_force(self) -> None:
        script = REPO_ROOT / "scripts" / "install-claude.sh"
        assert_file(self, script)
        with tempfile.TemporaryDirectory(prefix="atlas overwrite claude ") as temp_dir:
            root = Path(temp_dir)
            config = root / "claude config"
            target = config / "skills" / "map-project"
            target.mkdir(parents=True)
            marker = target / "existing-user-file.txt"
            marker.write_text("preserve\n", encoding="utf-8")
            env = {"HOME": str(root / "home"), "CLAUDE_CONFIG_DIR": str(config)}

            refused = run_command(["bash", script], env=env)
            self.assertNotEqual(refused.returncode, 0, "installer silently overwrote an existing skill")
            self.assertEqual(marker.read_text(encoding="utf-8"), "preserve\n")

            forced = run_command(["bash", script, "--force"], env=env)
            self.assertEqual(forced.returncode, 0, forced.stderr)
            self.assertFalse(marker.exists())
            self.assert_bundle_installed(CLAUDE_ADAPTER / "skills" / "map-project", target)
            backup_root = config / ".skill-backups" / "project-atlas"
            backups = list(backup_root.glob("map-project-*"))
            self.assertEqual(len(backups), 1, "forced install must preserve exactly one old version")
            self.assertEqual(
                (backups[0] / "existing-user-file.txt").read_text(encoding="utf-8"),
                "preserve\n",
            )
            self.assertEqual(
                sorted(path.name for path in target.parent.iterdir()),
                ["map-project"],
                "backup or staging content remained inside the auto-discovered skills directory",
            )

    def test_codex_standalone_metadata_uses_direct_invocation_without_mutating_plugin(self) -> None:
        plugin_metadata = CODEX_ADAPTER / "skills" / "map-project" / "agents" / "openai.yaml"
        original = plugin_metadata.read_bytes()
        self.assertIn(b"$project-atlas:map-project", original)
        with tempfile.TemporaryDirectory(prefix="atlas standalone metadata ") as temp_dir:
            root = Path(temp_dir)
            result = run_command(
                ["bash", REPO_ROOT / "scripts" / "install.sh"],
                env={"HOME": str(root / "home"), "CODEX_HOME": str(root / "codex")},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            target = root / "codex" / "skills" / "map-project"
            self.assert_codex_standalone_installed(target)
            installed = (target / "agents" / "openai.yaml").read_bytes()
            self.assertIn(b"$map-project", installed)
            self.assertNotIn(b"$project-atlas:map-project", installed)
        self.assertEqual(plugin_metadata.read_bytes(), original)

    def test_post_install_verification_failure_restores_previous_version(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas installer mismatch ") as temp_dir:
            root = Path(temp_dir)
            fake_bin = root / "fake-bin"
            self.write_counted_diff(fake_bin)
            real_diff = shutil.which("diff")
            self.assertIsNotNone(real_diff)
            cases = (
                (
                    "codex",
                    REPO_ROOT / "scripts" / "install.sh",
                    root / "codex" / "skills" / "map-project",
                    {"HOME": str(root / "home"), "CODEX_HOME": str(root / "codex")},
                ),
                (
                    "claude",
                    REPO_ROOT / "scripts" / "install-claude.sh",
                    root / "claude" / "skills" / "map-project",
                    {"HOME": str(root / "home"), "CLAUDE_CONFIG_DIR": str(root / "claude")},
                ),
            )
            for name, script, target, env in cases:
                with self.subTest(adapter=name):
                    target.mkdir(parents=True)
                    marker = target / "old-marker.txt"
                    marker.write_text("preserve\n", encoding="utf-8")
                    env["PATH"] = f"{fake_bin}{os.pathsep}{os.environ['PATH']}"
                    env["ATLAS_REAL_DIFF"] = str(real_diff)
                    env["ATLAS_TEST_DIFF_COUNT"] = str(root / f"{name}-mismatch-count")
                    env["ATLAS_TEST_FAIL_CALL"] = "3"
                    result = run_command(["bash", script, "--force"], env=env)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(marker.read_text(encoding="utf-8"), "preserve\n")
                    self.assertFalse((target / "SKILL.md").exists())

    def test_term_during_verification_restores_previous_version(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas installer term ") as temp_dir:
            root = Path(temp_dir)
            fake_bin = root / "fake-bin"
            self.write_counted_diff(fake_bin)
            real_diff = shutil.which("diff")
            self.assertIsNotNone(real_diff)
            cases = (
                (
                    "codex",
                    REPO_ROOT / "scripts" / "install.sh",
                    root / "codex" / "skills" / "map-project",
                    {"HOME": str(root / "home"), "CODEX_HOME": str(root / "codex")},
                ),
                (
                    "claude",
                    REPO_ROOT / "scripts" / "install-claude.sh",
                    root / "claude" / "skills" / "map-project",
                    {"HOME": str(root / "home"), "CLAUDE_CONFIG_DIR": str(root / "claude")},
                ),
            )
            for name, script, target, env in cases:
                with self.subTest(adapter=name):
                    target.mkdir(parents=True)
                    marker = target / "old-marker.txt"
                    marker.write_text("preserve\n", encoding="utf-8")
                    env["PATH"] = f"{fake_bin}{os.pathsep}{os.environ['PATH']}"
                    env["ATLAS_REAL_DIFF"] = str(real_diff)
                    env["ATLAS_TEST_DIFF_COUNT"] = str(root / f"{name}-term-count")
                    env["ATLAS_TEST_SIGNAL_CALL"] = "3"
                    result = run_command(["bash", script, "--force"], env=env, timeout=10)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(marker.read_text(encoding="utf-8"), "preserve\n")
                    self.assertFalse((target / "SKILL.md").exists())

    def test_installers_reject_nested_symlinks_and_special_nodes_in_packaged_skill(self) -> None:
        cases = (
            ("codex", "adapters/codex/skills/map-project", "scripts/install.sh", "CODEX_HOME"),
            (
                "claude",
                "adapters/claude-code/skills/map-project",
                "scripts/install-claude.sh",
                "CLAUDE_CONFIG_DIR",
            ),
        )
        for name, source_relative, script_relative, config_variable in cases:
            with self.subTest(adapter=name):
                with tempfile.TemporaryDirectory(prefix=f"atlas unsafe {name} source ") as temp_dir:
                    root = Path(temp_dir)
                    clone = root / "project atlas"
                    clone.mkdir()
                    shutil.copytree(REPO_ROOT / "adapters", clone / "adapters")
                    shutil.copytree(REPO_ROOT / "scripts", clone / "scripts")
                    source = clone / source_relative
                    config = root / f"{name} config"
                    env = {"HOME": str(root / "home"), config_variable: str(config)}

                    external = root / "external secret.txt"
                    external.write_text("must-not-be-installed\n", encoding="utf-8")
                    unsafe = source / "references" / "external-link"
                    unsafe.symlink_to(external)
                    symlink_result = run_command(
                        ["bash", clone / script_relative], env=env, cwd=clone, timeout=5
                    )
                    self.assertNotEqual(
                        symlink_result.returncode,
                        0,
                        "installer accepted a nested symlink in its packaged source",
                    )
                    self.assertFalse((config / "skills" / "map-project").exists())
                    unsafe.unlink()

                    fifo = source / "references" / "unexpected.pipe"
                    os.mkfifo(fifo)
                    fifo_result = run_command(
                        ["bash", clone / script_relative], env=env, cwd=clone, timeout=5
                    )
                    self.assertNotEqual(
                        fifo_result.returncode,
                        0,
                        "installer accepted a special filesystem node in its packaged source",
                    )
                    self.assertFalse((config / "skills" / "map-project").exists())

    def test_installers_fail_closed_when_packaged_source_root_is_swapped_after_validation(
        self,
    ) -> None:
        cases = (
            ("codex", "adapters/codex/skills/map-project", "scripts/install.sh", "CODEX_HOME"),
            (
                "claude",
                "adapters/claude-code/skills/map-project",
                "scripts/install-claude.sh",
                "CLAUDE_CONFIG_DIR",
            ),
        )
        for name, source_relative, script_relative, config_variable in cases:
            with self.subTest(adapter=name):
                with tempfile.TemporaryDirectory(prefix=f"atlas swapped {name} source ") as temp_dir:
                    root = Path(temp_dir)
                    clone = root / "project atlas"
                    clone.mkdir()
                    shutil.copytree(REPO_ROOT / "adapters", clone / "adapters")
                    shutil.copytree(REPO_ROOT / "scripts", clone / "scripts")
                    source = clone / source_relative
                    config = root / f"{name} config"
                    target = config / "skills" / "map-project"
                    target.mkdir(parents=True)
                    preserved = target / "existing-user-file.txt"
                    preserved.write_text("preserve-original\n", encoding="utf-8")

                    barrier = root / "source-validated"
                    release = root / "release-source-copy"
                    env = os.environ.copy()
                    env.update(
                        {
                            "HOME": str(root / "home"),
                            config_variable: str(config),
                            "ATLAS_TEST_SOURCE_ANCHORED_BARRIER": str(barrier),
                            "ATLAS_TEST_SOURCE_ANCHORED_RELEASE": str(release),
                        }
                    )
                    process = subprocess.Popen(
                        ["bash", str(clone / script_relative), "--force"],
                        cwd=clone,
                        env=env,
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )
                    self.wait_for_process_barrier(
                        process,
                        barrier,
                        "installer never reached the packaged-source validation gate",
                    )

                    validated_source = source.with_name("validated-map-project")
                    source.rename(validated_source)
                    shutil.copytree(validated_source, source)
                    (source / "swapped-source-marker.txt").write_text(
                        "must-not-be-installed\n",
                        encoding="utf-8",
                    )
                    release.touch()
                    stdout, stderr = process.communicate(timeout=10)

                    self.assertNotEqual(process.returncode, 0, stderr or stdout)
                    self.assertEqual(
                        preserved.read_text(encoding="utf-8"),
                        "preserve-original\n",
                    )
                    self.assertFalse(
                        (target / "swapped-source-marker.txt").exists(),
                        "installer promoted files from a source root that was not the validated object",
                    )

    def test_installers_fail_closed_if_skill_manifest_disappears_after_path_check(self) -> None:
        cases = (
            ("codex", "adapters/codex/skills/map-project", "scripts/install.sh", "CODEX_HOME"),
            (
                "claude",
                "adapters/claude-code/skills/map-project",
                "scripts/install-claude.sh",
                "CLAUDE_CONFIG_DIR",
            ),
        )
        real_python = shutil.which("python3")
        self.assertIsNotNone(real_python, "manifest race regression requires the system python3")
        for name, source_relative, script_relative, config_variable in cases:
            with self.subTest(adapter=name):
                with tempfile.TemporaryDirectory(prefix=f"atlas missing {name} manifest ") as temp_dir:
                    root = Path(temp_dir)
                    clone = root / "project atlas"
                    clone.mkdir()
                    shutil.copytree(REPO_ROOT / "adapters", clone / "adapters")
                    shutil.copytree(REPO_ROOT / "scripts", clone / "scripts")
                    source = clone / source_relative
                    config = root / f"{name} config"
                    target = config / "skills" / "map-project"
                    target.mkdir(parents=True)
                    preserved = target / "existing-user-file.txt"
                    preserved.write_text("preserve-original\n", encoding="utf-8")

                    fake_bin = root / "fake-bin"
                    self.write_blocking_python(fake_bin)
                    barrier = root / "source-path-check-complete"
                    release = root / "release-python"
                    env = os.environ.copy()
                    env.update(
                        {
                            "HOME": str(root / "home"),
                            config_variable: str(config),
                            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                            "ATLAS_REAL_PYTHON": str(real_python),
                            "ATLAS_TEST_BARRIER": str(barrier),
                            "ATLAS_TEST_RELEASE": str(release),
                        }
                    )
                    process = subprocess.Popen(
                        ["bash", str(clone / script_relative), "--force"],
                        cwd=clone,
                        env=env,
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )
                    self.wait_for_process_barrier(
                        process,
                        barrier,
                        "installer never completed the path-based SKILL.md check",
                    )

                    (source / "SKILL.md").unlink()
                    release.touch()
                    stdout, stderr = process.communicate(timeout=10)

                    self.assertNotEqual(process.returncode, 0, stderr or stdout)
                    self.assertEqual(
                        preserved.read_text(encoding="utf-8"),
                        "preserve-original\n",
                    )

    @unittest.skipUnless(os.name == "posix", "descriptor cleanup requires POSIX nodes")
    def test_cleanup_preserves_nested_unsafe_nodes(self) -> None:
        script = REPO_ROOT / "scripts" / "install.sh"
        for node_kind in ("symlink", "hardlink", "fifo"):
            with self.subTest(node=node_kind), tempfile.TemporaryDirectory(
                prefix=f"atlas nested cleanup {node_kind} "
            ) as temp_dir:
                root = Path(temp_dir)
                config = root / "codex"
                barrier = root / "cleanup-quarantined"
                release = root / "release-cleanup"
                env = os.environ.copy()
                env.update(
                    {
                        "HOME": str(root / "home"),
                        "CODEX_HOME": str(config),
                        "ATLAS_TEST_CLEANUP_REMOVE_BARRIER": str(barrier),
                        "ATLAS_TEST_CLEANUP_REMOVE_RELEASE": str(release),
                    }
                )
                process = subprocess.Popen(
                    ["bash", str(script)],
                    cwd=REPO_ROOT,
                    env=env,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                self.wait_for_process_barrier(
                    process,
                    barrier,
                    "installer never quarantined its nested staging tree",
                )

                skills = config / "skills"
                cleanup_entries = list(skills.glob(".map-project.cleanup-*"))
                if len(cleanup_entries) != 1:
                    release.touch()
                    stdout, stderr = process.communicate(timeout=10)
                    self.fail(
                        "installer exposed an unexpected cleanup state: "
                        f"{[entry.name for entry in cleanup_entries]}\n"
                        f"stdout:\n{stdout}\nstderr:\n{stderr}"
                    )
                nested = (
                    cleanup_entries[0]
                    / "expected-map-project"
                    / "references"
                )
                self.assertTrue(nested.is_dir())
                unsafe = nested / f"unsafe-{node_kind}"
                external = root / f"external-{node_kind}"
                external.write_text("preserve\n", encoding="utf-8")
                if node_kind == "symlink":
                    unsafe.symlink_to(external)
                elif node_kind == "hardlink":
                    try:
                        os.link(external, unsafe)
                    except OSError as exc:
                        release.touch()
                        process.communicate(timeout=10)
                        self.skipTest(
                            f"nested cleanup hardlink regression is unsupported: {exc}"
                        )
                    self.assertTrue(unsafe.samefile(external))
                    self.assertGreater(external.stat().st_nlink, 1)
                else:
                    os.mkfifo(unsafe)

                release.touch()
                stdout, stderr = process.communicate(timeout=10)

                self.assertNotEqual(process.returncode, 0, stderr or stdout)
                self.assertIn(
                    "unable to remove owned staging directory",
                    stderr,
                )
                preserved = list(
                    skills.glob(
                        f".map-project.cleanup-*/expected-map-project/references/{unsafe.name}"
                    )
                )
                self.assertEqual(len(preserved), 1, stderr or stdout)
                if node_kind == "symlink":
                    self.assertTrue(preserved[0].is_symlink())
                    self.assertTrue(
                        preserved[0].resolve(strict=True).samefile(external)
                    )
                elif node_kind == "hardlink":
                    self.assertTrue(preserved[0].samefile(external))
                    self.assertGreater(external.stat().st_nlink, 1)
                else:
                    self.assertTrue(stat.S_ISFIFO(preserved[0].lstat().st_mode))
                self.assertEqual(
                    external.read_text(encoding="utf-8"),
                    "preserve\n",
                )

    def test_cleanup_never_deletes_foreign_replacement_after_identity_check(self) -> None:
        cases = (
            ("codex", REPO_ROOT / "scripts" / "install.sh", "CODEX_HOME"),
            ("claude", REPO_ROOT / "scripts" / "install-claude.sh", "CLAUDE_CONFIG_DIR"),
        )
        for name, script, config_variable in cases:
            with self.subTest(adapter=name):
                with tempfile.TemporaryDirectory(prefix=f"atlas cleanup identity {name} ") as temp_dir:
                    root = Path(temp_dir)
                    config = root / f"{name} config"
                    barrier = root / "cleanup-identity-checked"
                    release = root / "release-cleanup"
                    env = os.environ.copy()
                    env.update(
                        {
                            "HOME": str(root / "home"),
                            config_variable: str(config),
                            "ATLAS_TEST_CLEANUP_REMOVE_BARRIER": str(barrier),
                            "ATLAS_TEST_CLEANUP_REMOVE_RELEASE": str(release),
                        }
                    )
                    process = subprocess.Popen(
                        ["bash", str(script)],
                        cwd=REPO_ROOT,
                        env=env,
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )
                    self.wait_for_process_barrier(
                        process,
                        barrier,
                        "installer never reached cleanup after its final identity check",
                    )

                    skills = config / "skills"
                    cleanup_entries = list(skills.glob(".map-project.cleanup-*"))
                    if len(cleanup_entries) != 1:
                        release.touch()
                        stdout, stderr = process.communicate(timeout=10)
                        self.fail(
                            "installer exposed an unexpected cleanup state: "
                            f"{[entry.name for entry in cleanup_entries]}\n"
                            f"stdout:\n{stdout}\nstderr:\n{stderr}"
                        )
                    cleanup = cleanup_entries[0]
                    cleanup.rename(root / "installer-owned-cleanup")
                    cleanup.mkdir()
                    foreign_marker = cleanup / "foreign-marker.txt"
                    foreign_marker.write_text("do-not-delete\n", encoding="utf-8")
                    release.touch()
                    stdout, stderr = process.communicate(timeout=10)

                    self.assertNotEqual(process.returncode, 0, stderr or stdout)
                    self.assertTrue(
                        foreign_marker.exists(),
                        "cleanup removed a directory that replaced the installer-owned quarantine",
                    )
                    self.assertEqual(foreign_marker.read_text(encoding="utf-8"), "do-not-delete\n")

    def test_cleanup_preserves_same_identity_tree_when_run_marker_drifts(self) -> None:
        marker_name = ".project-atlas-install-owner.json"
        cases = (
            ("codex", REPO_ROOT / "scripts" / "install.sh", "CODEX_HOME"),
            ("claude", REPO_ROOT / "scripts" / "install-claude.sh", "CLAUDE_CONFIG_DIR"),
        )
        for name, script, config_variable in cases:
            with self.subTest(adapter=name):
                with tempfile.TemporaryDirectory(
                    prefix=f"atlas cleanup marker drift {name} "
                ) as temp_dir:
                    root = Path(temp_dir)
                    config = root / f"{name} config"
                    barrier = root / "cleanup-marker-verified"
                    release = root / "release-cleanup-marker"
                    env = os.environ.copy()
                    env.update(
                        {
                            "HOME": str(root / "home"),
                            config_variable: str(config),
                            "ATLAS_TEST_CLEANUP_REMOVE_BARRIER": str(barrier),
                            "ATLAS_TEST_CLEANUP_REMOVE_RELEASE": str(release),
                        }
                    )
                    process = subprocess.Popen(
                        ["bash", str(script)],
                        cwd=REPO_ROOT,
                        env=env,
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )
                    self.wait_for_process_barrier(
                        process,
                        barrier,
                        "installer never reached cleanup after quarantining its staging tree",
                    )

                    skills = config / "skills"
                    cleanup_entries = list(skills.glob(".map-project.cleanup-*"))
                    if len(cleanup_entries) != 1:
                        release.touch()
                        stdout, stderr = process.communicate(timeout=10)
                        self.fail(
                            "installer exposed an unexpected cleanup state: "
                            f"{[entry.name for entry in cleanup_entries]}\n"
                            f"stdout:\n{stdout}\nstderr:\n{stderr}"
                        )
                    cleanup = cleanup_entries[0]
                    cleanup_identity = (cleanup.stat().st_dev, cleanup.stat().st_ino)
                    marker = cleanup / marker_name
                    marker.write_text("foreign ownership proof\n", encoding="utf-8")
                    canary = cleanup / "foreign-canary.txt"
                    canary.write_text("preserve\n", encoding="utf-8")
                    self.assertEqual(
                        (cleanup.stat().st_dev, cleanup.stat().st_ino),
                        cleanup_identity,
                        "test changed the directory identity instead of only its ownership proof",
                    )
                    release.touch()
                    stdout, stderr = process.communicate(timeout=10)

                    self.assertNotEqual(process.returncode, 0, stderr or stdout)
                    preserved_canaries = list(
                        skills.glob(".map-project.install-*/foreign-canary.txt")
                    ) + list(skills.glob(".map-project.cleanup-*/foreign-canary.txt"))
                    self.assertEqual(len(preserved_canaries), 1, stderr or stdout)
                    self.assertEqual(
                        preserved_canaries[0].read_text(encoding="utf-8"),
                        "preserve\n",
                    )

    def test_cleanup_restores_a_replacement_moved_during_source_quarantine(self) -> None:
        cases = (
            ("codex", REPO_ROOT / "scripts" / "install.sh", "CODEX_HOME"),
            ("claude", REPO_ROOT / "scripts" / "install-claude.sh", "CLAUDE_CONFIG_DIR"),
        )
        for name, script, config_variable in cases:
            with self.subTest(adapter=name):
                with tempfile.TemporaryDirectory(prefix=f"atlas cleanup source race {name} ") as temp_dir:
                    root = Path(temp_dir)
                    config = root / f"{name} config"
                    barrier = root / "cleanup-source-identity-checked"
                    release = root / "release-cleanup-source"
                    env = os.environ.copy()
                    env.update(
                        {
                            "HOME": str(root / "home"),
                            config_variable: str(config),
                            "ATLAS_TEST_CLEANUP_BEFORE_QUARANTINE_BARRIER": str(barrier),
                            "ATLAS_TEST_CLEANUP_BEFORE_QUARANTINE_RELEASE": str(release),
                        }
                    )
                    process = subprocess.Popen(
                        ["bash", str(script)],
                        cwd=REPO_ROOT,
                        env=env,
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )
                    self.wait_for_process_barrier(
                        process,
                        barrier,
                        "installer never paused between cleanup identity check and quarantine",
                    )

                    skills = config / "skills"
                    stage_entries = list(skills.glob(".map-project.install-*"))
                    if len(stage_entries) != 1:
                        release.touch()
                        stdout, stderr = process.communicate(timeout=10)
                        self.fail(
                            "installer exposed an unexpected staging state: "
                            f"{[entry.name for entry in stage_entries]}\n"
                            f"stdout:\n{stdout}\nstderr:\n{stderr}"
                        )
                    stage = stage_entries[0]
                    stage_name = stage.name
                    stage.rename(root / "installer-owned-stage")
                    replacement = skills / stage_name
                    replacement.mkdir()
                    marker = replacement / "foreign-marker.txt"
                    marker.write_text("keep-visible\n", encoding="utf-8")
                    release.touch()
                    stdout, stderr = process.communicate(timeout=10)

                    self.assertNotEqual(process.returncode, 0, stderr or stdout)
                    self.assertTrue(
                        marker.exists(),
                        "cleanup hid a foreign replacement under an unreported quarantine name",
                    )
                    self.assertEqual(marker.read_text(encoding="utf-8"), "keep-visible\n")

    def test_rollback_restores_a_backup_path_replaced_during_atomic_move(self) -> None:
        cases = (
            ("codex", REPO_ROOT / "scripts" / "install.sh", "CODEX_HOME"),
            ("claude", REPO_ROOT / "scripts" / "install-claude.sh", "CLAUDE_CONFIG_DIR"),
        )
        real_diff = shutil.which("diff")
        self.assertIsNotNone(real_diff)
        for name, script, config_variable in cases:
            with self.subTest(adapter=name):
                with tempfile.TemporaryDirectory(prefix=f"atlas backup restore race {name} ") as temp_dir:
                    root = Path(temp_dir)
                    config = root / f"{name} config"
                    target = config / "skills" / "map-project"
                    target.mkdir(parents=True)
                    (target / "old-marker.txt").write_text("original\n", encoding="utf-8")
                    fake_bin = root / "fake-bin"
                    self.write_counted_diff(fake_bin)
                    barrier = root / "backup-restore-identity-checked"
                    release = root / "release-backup-restore"
                    env = os.environ.copy()
                    env.update(
                        {
                            "HOME": str(root / "home"),
                            config_variable: str(config),
                            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                            "ATLAS_REAL_DIFF": str(real_diff),
                            "ATLAS_TEST_DIFF_COUNT": str(root / "diff-count"),
                            "ATLAS_TEST_FAIL_CALL": "3",
                            "ATLAS_TEST_BACKUP_RESTORE_BARRIER": str(barrier),
                            "ATLAS_TEST_BACKUP_RESTORE_RELEASE": str(release),
                        }
                    )
                    process = subprocess.Popen(
                        ["bash", str(script), "--force"],
                        cwd=REPO_ROOT,
                        env=env,
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )
                    self.wait_for_process_barrier(
                        process,
                        barrier,
                        "installer never paused between backup identity check and restore move",
                    )

                    backup_root = config / ".skill-backups" / "project-atlas"
                    backups = list(backup_root.glob("map-project-*"))
                    if len(backups) != 1:
                        release.touch()
                        stdout, stderr = process.communicate(timeout=10)
                        self.fail(
                            f"unexpected backup state: {backups}\nstdout:\n{stdout}\nstderr:\n{stderr}"
                        )
                    backup = backups[0]
                    backup.rename(root / "installer-owned-backup")
                    backup.mkdir()
                    foreign = backup / "foreign-marker.txt"
                    foreign.write_text("keep-at-backup-path\n", encoding="utf-8")
                    release.touch()
                    stdout, stderr = process.communicate(timeout=10)

                    self.assertNotEqual(process.returncode, 0, stderr or stdout)
                    self.assertTrue(
                        foreign.exists(),
                        "rollback moved a foreign backup replacement into the public target",
                    )
                    self.assertEqual(
                        foreign.read_text(encoding="utf-8"),
                        "keep-at-backup-path\n",
                    )
                    self.assertFalse((target / "foreign-marker.txt").exists())

    def test_backup_restore_preserves_same_identity_payload_when_digest_drifts(self) -> None:
        cases = (
            ("codex", REPO_ROOT / "scripts" / "install.sh", "CODEX_HOME"),
            ("claude", REPO_ROOT / "scripts" / "install-claude.sh", "CLAUDE_CONFIG_DIR"),
        )
        real_diff = shutil.which("diff")
        self.assertIsNotNone(real_diff)
        for name, script, config_variable in cases:
            with self.subTest(adapter=name):
                with tempfile.TemporaryDirectory(
                    prefix=f"atlas backup payload drift {name} "
                ) as temp_dir:
                    root = Path(temp_dir)
                    config = root / f"{name} config"
                    target = config / "skills" / "map-project"
                    target.mkdir(parents=True)
                    original = target / "old-marker.txt"
                    original.write_text("original\n", encoding="utf-8")
                    fake_bin = root / "fake-bin"
                    self.write_counted_diff(fake_bin)
                    barrier = root / "backup-payload-verified"
                    release = root / "release-backup-payload"
                    env = os.environ.copy()
                    env.update(
                        {
                            "HOME": str(root / "home"),
                            config_variable: str(config),
                            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                            "ATLAS_REAL_DIFF": str(real_diff),
                            "ATLAS_TEST_DIFF_COUNT": str(root / "diff-count"),
                            "ATLAS_TEST_FAIL_CALL": "3",
                            "ATLAS_TEST_BACKUP_RESTORE_BARRIER": str(barrier),
                            "ATLAS_TEST_BACKUP_RESTORE_RELEASE": str(release),
                        }
                    )
                    process = subprocess.Popen(
                        ["bash", str(script), "--force"],
                        cwd=REPO_ROOT,
                        env=env,
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )
                    self.wait_for_process_barrier(
                        process,
                        barrier,
                        "installer never paused after checking the backup payload",
                    )

                    backup_root = config / ".skill-backups" / "project-atlas"
                    backups = list(backup_root.glob("map-project-*"))
                    if len(backups) != 1:
                        release.touch()
                        stdout, stderr = process.communicate(timeout=10)
                        self.fail(
                            f"unexpected backup state: {backups}\n"
                            f"stdout:\n{stdout}\nstderr:\n{stderr}"
                        )
                    backup = backups[0]
                    backup_identity = (backup.stat().st_dev, backup.stat().st_ino)
                    (backup / "old-marker.txt").write_text("altered\n", encoding="utf-8")
                    canary = backup / "foreign-canary.txt"
                    canary.write_text("preserve\n", encoding="utf-8")
                    self.assertEqual(
                        (backup.stat().st_dev, backup.stat().st_ino),
                        backup_identity,
                        "test changed the backup directory identity",
                    )
                    release.touch()
                    stdout, stderr = process.communicate(timeout=10)

                    self.assertNotEqual(process.returncode, 0, stderr or stdout)
                    remaining_backups = list(backup_root.glob("map-project-*"))
                    self.assertEqual(len(remaining_backups), 1, stderr or stdout)
                    self.assertEqual(
                        (remaining_backups[0].stat().st_dev, remaining_backups[0].stat().st_ino),
                        backup_identity,
                    )
                    self.assertEqual(
                        (remaining_backups[0] / "old-marker.txt").read_text(encoding="utf-8"),
                        "altered\n",
                    )
                    self.assertEqual(
                        (remaining_backups[0] / "foreign-canary.txt").read_text(
                            encoding="utf-8"
                        ),
                        "preserve\n",
                    )
                    self.assertFalse(target.exists(), "rollback published the altered backup")

    def test_installers_reject_hardlinked_packaged_source_files(self) -> None:
        cases = (
            (
                "codex",
                Path("adapters/codex/skills/map-project"),
                Path("scripts/install.sh"),
                "CODEX_HOME",
            ),
            (
                "claude",
                Path("adapters/claude-code/skills/map-project"),
                Path("scripts/install-claude.sh"),
                "CLAUDE_CONFIG_DIR",
            ),
        )
        for name, source_relative, script_relative, config_variable in cases:
            with self.subTest(adapter=name):
                with tempfile.TemporaryDirectory(prefix=f"atlas hardlink source {name} ") as temp_dir:
                    root = Path(temp_dir)
                    clone = root / "project atlas"
                    clone.mkdir()
                    shutil.copytree(REPO_ROOT / "adapters", clone / "adapters")
                    shutil.copytree(REPO_ROOT / "scripts", clone / "scripts")
                    source_file = clone / source_relative / "references" / "evidence-model.md"
                    external = root / "outside-source.txt"
                    external.write_text("outside-source-must-not-be-packaged\n", encoding="utf-8")
                    source_file.unlink()
                    os.link(external, source_file)
                    config = root / f"{name} config"
                    result = run_command(
                        ["bash", clone / script_relative],
                        cwd=clone,
                        env={
                            "HOME": str(root / "home"),
                            config_variable: str(config),
                        },
                    )

                    self.assertNotEqual(result.returncode, 0, result.stderr or result.stdout)
                    self.assertFalse((config / "skills" / "map-project").exists())
                    self.assertEqual(
                        external.read_text(encoding="utf-8"),
                        "outside-source-must-not-be-packaged\n",
                    )
                    self.assertNotIn("outside-source-must-not-be-packaged", result.stderr)

    def test_codex_metadata_rewrite_does_not_follow_replacement_symlink(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas metadata rewrite identity ") as temp_dir:
            root = Path(temp_dir)
            config = root / "codex config"
            barrier = root / "metadata-opened"
            release = root / "release-metadata-rewrite"
            env = os.environ.copy()
            env.update(
                {
                    "HOME": str(root / "home"),
                    "CODEX_HOME": str(config),
                    "ATLAS_TEST_CODEX_METADATA_REWRITE_BARRIER": str(barrier),
                    "ATLAS_TEST_CODEX_METADATA_REWRITE_RELEASE": str(release),
                }
            )
            process = subprocess.Popen(
                ["bash", str(REPO_ROOT / "scripts" / "install.sh")],
                cwd=REPO_ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.wait_for_process_barrier(
                process,
                barrier,
                "installer never reached the metadata rewrite gate",
            )

            staged_metadata = list(
                (config / "skills").glob(
                    ".map-project.install-*/map-project/agents/openai.yaml"
                )
            )
            if len(staged_metadata) != 1:
                release.touch()
                stdout, stderr = process.communicate(timeout=10)
                self.fail(
                    "installer exposed an unexpected metadata staging state: "
                    f"{staged_metadata}\nstdout:\n{stdout}\nstderr:\n{stderr}"
                )
            metadata = staged_metadata[0]
            metadata.rename(metadata.with_name("anchored-openai.yaml"))
            external = root / "external-metadata.yaml"
            external.write_text("do-not-overwrite\n", encoding="utf-8")
            metadata.symlink_to(external)
            release.touch()
            stdout, stderr = process.communicate(timeout=10)

            self.assertNotEqual(process.returncode, 0, stderr or stdout)
            self.assertEqual(external.read_text(encoding="utf-8"), "do-not-overwrite\n")
            self.assertFalse((config / "skills" / "map-project").exists())

    def test_installers_reject_symlinked_targets_before_writing_outside_them(self) -> None:
        cases = (
            ("codex", REPO_ROOT / "scripts" / "install.sh", "CODEX_HOME"),
            ("claude", REPO_ROOT / "scripts" / "install-claude.sh", "CLAUDE_CONFIG_DIR"),
        )
        for name, script, config_variable in cases:
            with self.subTest(adapter=name, symlink="root"):
                with tempfile.TemporaryDirectory(prefix=f"atlas {name} root symlink ") as temp_dir:
                    root = Path(temp_dir)
                    external = root / "external config"
                    external.mkdir()
                    linked_config = root / f"{name} config"
                    linked_config.symlink_to(external, target_is_directory=True)
                    result = run_command(
                        ["bash", script],
                        env={"HOME": str(root / "home"), config_variable: str(linked_config)},
                        timeout=5,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse(
                        (external / "skills").exists(),
                        "installer wrote through a symlinked installation root before rejecting it",
                    )

            with self.subTest(adapter=name, symlink="skills"):
                with tempfile.TemporaryDirectory(prefix=f"atlas {name} skills symlink ") as temp_dir:
                    root = Path(temp_dir)
                    config = root / f"{name} config"
                    config.mkdir()
                    external_skills = root / "external skills"
                    external_skills.mkdir()
                    (config / "skills").symlink_to(external_skills, target_is_directory=True)
                    result = run_command(
                        ["bash", script],
                        env={"HOME": str(root / "home"), config_variable: str(config)},
                        timeout=5,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse(
                        (external_skills / "map-project").exists(),
                        "installer wrote through a symlinked skills directory before rejecting it",
                    )

            with self.subTest(adapter=name, symlink="intermediate"):
                with tempfile.TemporaryDirectory(prefix=f"atlas {name} intermediate symlink ") as temp_dir:
                    root = Path(temp_dir)
                    external = root / "external parent"
                    external.mkdir()
                    linked_parent = root / "linked parent"
                    linked_parent.symlink_to(external, target_is_directory=True)
                    config = linked_parent / "nested config"
                    result = run_command(
                        ["bash", script],
                        env={"HOME": str(root / "home"), config_variable: str(config)},
                        timeout=5,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse(
                        (external / "nested config").exists(),
                        "installer followed an intermediate symlink while creating its destination",
                    )

    def test_installers_anchor_transaction_when_skills_path_is_swapped(self) -> None:
        cases = (
            ("codex", REPO_ROOT / "scripts" / "install.sh", "CODEX_HOME"),
            ("claude", REPO_ROOT / "scripts" / "install-claude.sh", "CLAUDE_CONFIG_DIR"),
        )
        real_diff = shutil.which("diff")
        self.assertIsNotNone(real_diff, "race regression requires the system diff command")
        for name, script, config_variable in cases:
            with self.subTest(adapter=name):
                with tempfile.TemporaryDirectory(prefix=f"atlas anchored {name} transaction ") as temp_dir:
                    root = Path(temp_dir)
                    config = root / f"{name} config"
                    target = config / "skills" / "map-project"
                    target.mkdir(parents=True)
                    marker = target / "old-marker.txt"
                    marker.write_text("preserve-original\n", encoding="utf-8")

                    fake_bin = root / "fake-bin"
                    self.write_counted_diff(fake_bin)
                    barrier = root / "pre-promotion-verification"
                    release = root / "release-verification"
                    env = os.environ.copy()
                    env.update(
                        {
                            "HOME": str(root / "home"),
                            config_variable: str(config),
                            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                            "ATLAS_REAL_DIFF": str(real_diff),
                            "ATLAS_TEST_DIFF_COUNT": str(root / "diff-count"),
                            "ATLAS_TEST_BLOCK_CALL": "1",
                            "ATLAS_TEST_BARRIER": str(barrier),
                            "ATLAS_TEST_RELEASE": str(release),
                        }
                    )
                    process = subprocess.Popen(
                        ["bash", str(script), "--force"],
                        cwd=REPO_ROOT,
                        env=env,
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )
                    self.wait_for_process_barrier(
                        process,
                        barrier,
                        "installer never reached the anchored transaction gate",
                    )

                    skills = config / "skills"
                    anchored_skills = config / "anchored-skills"
                    skills.rename(anchored_skills)
                    external_skills = root / "external skills"
                    external_skills.mkdir()
                    skills.symlink_to(external_skills, target_is_directory=True)
                    release.touch()
                    stdout, stderr = process.communicate(timeout=10)

                    self.assertNotEqual(process.returncode, 0, stderr or stdout)
                    self.assertEqual(
                        (anchored_skills / "map-project" / "old-marker.txt").read_text(
                            encoding="utf-8"
                        ),
                        "preserve-original\n",
                    )
                    self.assertEqual(list(external_skills.iterdir()), [])

    def test_rollback_never_deletes_target_created_by_another_process(self) -> None:
        cases = (
            ("codex", REPO_ROOT / "scripts" / "install.sh", "CODEX_HOME"),
            ("claude", REPO_ROOT / "scripts" / "install-claude.sh", "CLAUDE_CONFIG_DIR"),
        )
        real_diff = shutil.which("diff")
        self.assertIsNotNone(real_diff, "race regression requires the system diff command")
        for name, script, config_variable in cases:
            with self.subTest(adapter=name):
                with tempfile.TemporaryDirectory(prefix=f"atlas foreign {name} target ") as temp_dir:
                    root = Path(temp_dir)
                    config = root / f"{name} config"
                    target = config / "skills" / "map-project"
                    target.mkdir(parents=True)
                    (target / "old-marker.txt").write_text("original\n", encoding="utf-8")

                    fake_bin = root / "fake-bin"
                    self.write_counted_diff(fake_bin)
                    barrier = root / "post-promotion-verification"
                    release = root / "release-verification"
                    env = os.environ.copy()
                    env.update(
                        {
                            "HOME": str(root / "home"),
                            config_variable: str(config),
                            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                            "ATLAS_REAL_DIFF": str(real_diff),
                            "ATLAS_TEST_DIFF_COUNT": str(root / "diff-count"),
                            "ATLAS_TEST_BLOCK_CALL": "3",
                            "ATLAS_TEST_FAIL_CALL": "3",
                            "ATLAS_TEST_BARRIER": str(barrier),
                            "ATLAS_TEST_RELEASE": str(release),
                        }
                    )
                    process = subprocess.Popen(
                        ["bash", str(script), "--force"],
                        cwd=REPO_ROOT,
                        env=env,
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )
                    self.wait_for_process_barrier(
                        process,
                        barrier,
                        "installer never reached post-promotion verification",
                    )

                    run_owned_target = target.parent / "run-owned-map-project"
                    target.rename(run_owned_target)
                    target.mkdir()
                    foreign = target / "foreign-marker.txt"
                    foreign.write_text("do-not-delete\n", encoding="utf-8")
                    release.touch()
                    stdout, stderr = process.communicate(timeout=10)

                    self.assertNotEqual(process.returncode, 0, stderr or stdout)
                    self.assertEqual(foreign.read_text(encoding="utf-8"), "do-not-delete\n")
                    backups = list(
                        (config / ".skill-backups" / "project-atlas").glob("map-project-*")
                    )
                    self.assertEqual(len(backups), 1)
                    self.assertEqual(
                        (backups[0] / "old-marker.txt").read_text(encoding="utf-8"),
                        "original\n",
                    )

    def test_rollback_preserves_same_identity_target_when_run_marker_drifts(self) -> None:
        marker_name = ".project-atlas-install-owner.json"
        cases = (
            ("codex", REPO_ROOT / "scripts" / "install.sh", "CODEX_HOME"),
            ("claude", REPO_ROOT / "scripts" / "install-claude.sh", "CLAUDE_CONFIG_DIR"),
        )
        real_diff = shutil.which("diff")
        self.assertIsNotNone(real_diff, "race regression requires the system diff command")
        for name, script, config_variable in cases:
            with self.subTest(adapter=name):
                with tempfile.TemporaryDirectory(
                    prefix=f"atlas promoted marker drift {name} "
                ) as temp_dir:
                    root = Path(temp_dir)
                    config = root / f"{name} config"
                    target = config / "skills" / "map-project"
                    target.mkdir(parents=True)
                    (target / "old-marker.txt").write_text("original\n", encoding="utf-8")
                    fake_bin = root / "fake-bin"
                    self.write_counted_diff(fake_bin)
                    barrier = root / "promoted-marker-verified"
                    release = root / "release-promoted-marker"
                    env = os.environ.copy()
                    env.update(
                        {
                            "HOME": str(root / "home"),
                            config_variable: str(config),
                            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                            "ATLAS_REAL_DIFF": str(real_diff),
                            "ATLAS_TEST_DIFF_COUNT": str(root / "diff-count"),
                            "ATLAS_TEST_BLOCK_CALL": "3",
                            "ATLAS_TEST_FAIL_CALL": "3",
                            "ATLAS_TEST_BARRIER": str(barrier),
                            "ATLAS_TEST_RELEASE": str(release),
                        }
                    )
                    process = subprocess.Popen(
                        ["bash", str(script), "--force"],
                        cwd=REPO_ROOT,
                        env=env,
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )
                    self.wait_for_process_barrier(
                        process,
                        barrier,
                        "installer never reached post-promotion verification",
                    )

                    target_identity = (target.stat().st_dev, target.stat().st_ino)
                    marker = target / marker_name
                    marker.write_text("foreign ownership proof\n", encoding="utf-8")
                    canary = target / "foreign-canary.txt"
                    canary.write_text("preserve\n", encoding="utf-8")
                    self.assertEqual(
                        (target.stat().st_dev, target.stat().st_ino),
                        target_identity,
                        "test changed the promoted directory identity",
                    )
                    release.touch()
                    stdout, stderr = process.communicate(timeout=10)

                    self.assertNotEqual(process.returncode, 0, stderr or stdout)
                    self.assertEqual(canary.read_text(encoding="utf-8"), "preserve\n")
                    self.assertEqual(marker.read_text(encoding="utf-8"), "foreign ownership proof\n")
                    backups = list(
                        (config / ".skill-backups" / "project-atlas").glob("map-project-*")
                    )
                    self.assertEqual(len(backups), 1, stderr or stdout)
                    self.assertEqual(
                        (backups[0] / "old-marker.txt").read_text(encoding="utf-8"),
                        "original\n",
                    )

    def test_atomic_promotion_never_replaces_target_that_appears_after_backup(self) -> None:
        cases = (
            ("codex", REPO_ROOT / "scripts" / "install.sh", "CODEX_HOME"),
            ("claude", REPO_ROOT / "scripts" / "install-claude.sh", "CLAUDE_CONFIG_DIR"),
        )
        real_diff = shutil.which("diff")
        self.assertIsNotNone(real_diff, "race regression requires the system diff command")
        for name, script, config_variable in cases:
            with self.subTest(adapter=name):
                with tempfile.TemporaryDirectory(prefix=f"atlas no-replace {name} ") as temp_dir:
                    root = Path(temp_dir)
                    config = root / f"{name} config"
                    target = config / "skills" / "map-project"
                    target.mkdir(parents=True)
                    (target / "old-marker.txt").write_text("original\n", encoding="utf-8")

                    fake_bin = root / "fake-bin"
                    self.write_counted_diff(fake_bin)
                    barrier = root / "backup-complete"
                    release = root / "release-promotion"
                    env = os.environ.copy()
                    env.update(
                        {
                            "HOME": str(root / "home"),
                            config_variable: str(config),
                            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                            "ATLAS_REAL_DIFF": str(real_diff),
                            "ATLAS_TEST_DIFF_COUNT": str(root / "diff-count"),
                            "ATLAS_TEST_BLOCK_CALL": "2",
                            "ATLAS_TEST_BARRIER": str(barrier),
                            "ATLAS_TEST_RELEASE": str(release),
                        }
                    )
                    process = subprocess.Popen(
                        ["bash", str(script), "--force"],
                        cwd=REPO_ROOT,
                        env=env,
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )
                    self.wait_for_process_barrier(
                        process,
                        barrier,
                        "installer never reached the no-replace promotion gate",
                    )
                    target_was_reserved_for_no_replace = not target.exists()
                    if not target_was_reserved_for_no_replace:
                        release.touch()
                        stdout, stderr = process.communicate(timeout=10)
                        self.fail(
                            "installer reached its second verification after publishing the target; "
                            f"stdout:\n{stdout}\nstderr:\n{stderr}"
                        )

                    target.mkdir()
                    foreign = target / "foreign-marker.txt"
                    foreign.write_text("do-not-replace\n", encoding="utf-8")
                    release.touch()
                    stdout, stderr = process.communicate(timeout=10)

                    self.assertNotEqual(process.returncode, 0, stderr or stdout)
                    self.assertEqual(foreign.read_text(encoding="utf-8"), "do-not-replace\n")
                    backups = list(
                        (config / ".skill-backups" / "project-atlas").glob("map-project-*")
                    )
                    self.assertEqual(len(backups), 1)
                    self.assertEqual(
                        (backups[0] / "old-marker.txt").read_text(encoding="utf-8"),
                        "original\n",
                    )

    def test_concurrent_force_install_keeps_original_backup_and_rejects_second_writer(self) -> None:
        cases = (
            (
                "codex",
                REPO_ROOT / "scripts" / "install.sh",
                "CODEX_HOME",
                self.assert_codex_standalone_installed,
            ),
            (
                "claude",
                REPO_ROOT / "scripts" / "install-claude.sh",
                "CLAUDE_CONFIG_DIR",
                lambda target: self.assert_bundle_installed(
                    CLAUDE_ADAPTER / "skills" / "map-project", target
                ),
            ),
        )
        for name, script, config_variable, assert_installed in cases:
            with self.subTest(adapter=name):
                with tempfile.TemporaryDirectory(prefix=f"atlas concurrent {name} ") as temp_dir:
                    root = Path(temp_dir)
                    config = root / f"{name} config"
                    target = config / "skills" / "map-project"
                    target.mkdir(parents=True)
                    marker = target / "original-marker.txt"
                    marker.write_text("preserve-original\n", encoding="utf-8")

                    fake_bin = root / "fake-bin"
                    barrier = root / "verification-started"
                    release = root / "release-verification"
                    self.write_fake_diff(
                        fake_bin,
                        ': > "$ATLAS_TEST_BARRIER"\n'
                        'while [ ! -e "$ATLAS_TEST_RELEASE" ]; do sleep 0.05; done\n'
                        "exit 0",
                    )
                    env = os.environ.copy()
                    env.update(
                        {
                            "HOME": str(root / "home"),
                            config_variable: str(config),
                            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                            "ATLAS_TEST_BARRIER": str(barrier),
                            "ATLAS_TEST_RELEASE": str(release),
                        }
                    )
                    command = ["bash", str(script), "--force"]
                    first = subprocess.Popen(
                        command,
                        cwd=REPO_ROOT,
                        env=env,
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )
                    second: subprocess.Popen[str] | None = None
                    try:
                        deadline = time.monotonic() + 5
                        while not barrier.exists() and time.monotonic() < deadline:
                            if first.poll() is not None:
                                break
                            time.sleep(0.02)
                        self.assertTrue(
                            barrier.exists(),
                            "first installer never reached verification",
                        )
                        lock = config / "skills" / ".map-project.install.lock"
                        self.assertTrue(
                            lock.is_dir(),
                            "first installer reached verification without holding its lock",
                        )

                        second = subprocess.Popen(
                            command,
                            cwd=REPO_ROOT,
                            env=env,
                            text=True,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                        )
                        try:
                            second_stdout, second_stderr = second.communicate(timeout=5)
                        except subprocess.TimeoutExpired as error:
                            self.fail(
                                "second installer did not reject the active lock before "
                                "the first installer was released\n"
                                f"second stdout before timeout:\n{error.stdout or ''}\n"
                                f"second stderr before timeout:\n{error.stderr or ''}"
                            )
                        release.touch()
                        first_stdout, first_stderr = first.communicate(timeout=10)
                        self.assertEqual(
                            first.returncode,
                            0,
                            first_stderr or first_stdout,
                        )
                        self.assertNotEqual(
                            second.returncode,
                            0,
                            second_stderr
                            or second_stdout
                            or "second concurrent installer unexpectedly succeeded",
                        )
                        self.assertIn(
                            "another installation is active or a stale lock exists",
                            second_stderr,
                        )

                        assert_installed(target)
                        backup_root = config / ".skill-backups" / "project-atlas"
                        backups = list(backup_root.glob("map-project-*"))
                        self.assertEqual(len(backups), 1)
                        self.assertEqual(
                            (backups[0] / "original-marker.txt").read_text(
                                encoding="utf-8"
                            ),
                            "preserve-original\n",
                        )
                        self.assertEqual(
                            sorted(path.name for path in target.parent.iterdir()),
                            ["map-project"],
                        )
                    finally:
                        release.touch()
                        for process in (second, first):
                            if process is None:
                                continue
                            try:
                                process.communicate(timeout=1)
                            except subprocess.TimeoutExpired:
                                process.terminate()
                                try:
                                    process.communicate(timeout=1)
                                except subprocess.TimeoutExpired:
                                    process.kill()
                                    process.communicate(timeout=5)

    def test_installers_do_not_require_sudo_or_edit_shell_profiles(self) -> None:
        for relative_path in ("scripts/install.sh", "scripts/install-claude.sh"):
            text = read_text(self, REPO_ROOT / relative_path).lower()
            with self.subTest(path=relative_path):
                self.assertNotIn("sudo", text)
                self.assertNotRegex(text, r"\.(?:zshrc|bashrc|bash_profile|profile)\b")


if __name__ == "__main__":
    unittest.main()
