from __future__ import annotations

import os
import signal
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

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
    ) -> None:
        deadline = time.monotonic() + 5
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
            for scenario in ("world-writable", "symlink-into-repository"):
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
                    if scenario == "world-writable":
                        self.write_fake_command(
                            unsafe_bin,
                            "python3",
                            f": > {str(canary)!r}\nexit 97",
                        )
                        (unsafe_bin / "python3").chmod(0o777)
                    else:
                        repository_bin = repository / "bin"
                        self.write_fake_command(
                            repository_bin,
                            "python3",
                            f": > {str(canary)!r}\nexit 97",
                        )
                        unsafe_bin.mkdir()
                        (unsafe_bin / "python3").symlink_to(repository_bin / "python3")
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

                    started = time.monotonic()
                    result = run_command(["bash", script, "--force"], env=env, timeout=5)
                    elapsed = time.monotonic() - started

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

                started = time.monotonic()
                result = run_command(["bash", script, "--force"], env=env, timeout=5)
                elapsed = time.monotonic() - started

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
                    deadline = time.monotonic() + 5
                    while not barrier.exists() and time.monotonic() < deadline:
                        if first.poll() is not None:
                            break
                        time.sleep(0.02)
                    self.assertTrue(barrier.exists(), "first installer never reached verification")

                    second = subprocess.Popen(
                        command,
                        cwd=REPO_ROOT,
                        env=env,
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )
                    time.sleep(0.2)
                    release.touch()
                    first_stdout, first_stderr = first.communicate(timeout=10)
                    second_stdout, second_stderr = second.communicate(timeout=10)
                    self.assertEqual(first.returncode, 0, first_stderr or first_stdout)
                    self.assertNotEqual(
                        second.returncode,
                        0,
                        second_stderr or second_stdout or "second concurrent installer unexpectedly succeeded",
                    )

                    assert_installed(target)
                    backup_root = config / ".skill-backups" / "project-atlas"
                    backups = list(backup_root.glob("map-project-*"))
                    self.assertEqual(len(backups), 1)
                    self.assertEqual(
                        (backups[0] / "original-marker.txt").read_text(encoding="utf-8"),
                        "preserve-original\n",
                    )
                    self.assertEqual(sorted(path.name for path in target.parent.iterdir()), ["map-project"])

    def test_installers_do_not_require_sudo_or_edit_shell_profiles(self) -> None:
        for relative_path in ("scripts/install.sh", "scripts/install-claude.sh"):
            text = read_text(self, REPO_ROOT / relative_path).lower()
            with self.subTest(path=relative_path):
                self.assertNotIn("sudo", text)
                self.assertNotRegex(text, r"\.(?:zshrc|bashrc|bash_profile|profile)\b")


if __name__ == "__main__":
    unittest.main()
