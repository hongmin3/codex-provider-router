import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest


PROJECT = pathlib.Path(__file__).parents[1]
SCRIPTS = PROJECT / "scripts"


class WindowsScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.installer = (SCRIPTS / "install.ps1").read_text(encoding="utf-8")
        cls.router = (SCRIPTS / "codex-router.ps1").read_text(encoding="utf-8")

    def test_installer_preserves_original_codex_and_uses_dpapi(self):
        self.assertIn("Find-RealCodex", self.installer)
        self.assertIn("real-codex-path.txt", self.installer)
        self.assertIn("ConvertFrom-SecureString", self.installer)
        self.assertNotIn("SetEnvironmentVariable('DEEPSEEK_API_KEY'", self.installer)

    def test_installer_accepts_key_file_and_ignores_plaintext_storage(self):
        self.assertIn("[string]$KeyFile", self.installer)
        self.assertIn("deepseek-api-key.dpapi", self.installer)
        self.assertIn("deepseek-api-key.txt", self.installer)

    def test_force_flag_injects_deepseek_profile(self):
        self.assertIn("$env:FORCE_DEEPSEEK -eq '1'", self.router)
        self.assertIn("'--profile', 'deepseek'", self.router)
        self.assertIn("'--model', 'deepseek-flash'", self.router)
        self.assertIn("model_reasoning_effort=\"high\"", self.router)

    def test_normal_run_forwards_to_original_codex(self):
        self.assertIn("if (-not $ForceDeepSeek)", self.router)
        self.assertIn("& $RealCodex @Arguments", self.router)

    def test_installer_decodes_the_downloaded_setup_script(self):
        # Windows PowerShell 5.1 returns Invoke-WebRequest's .Content as a Byte[] when the
        # server sends a non-text Content-Type, and the DeepSeek CDN sends
        # application/octet-stream. Matching a regex against that Byte[] does not raise; it
        # stringifies to "35 33 47 ..." and misses, so the installer aborts claiming the
        # official catalog is unparseable. Keep the decode, not just the memory of it.
        self.assertIn("[byte[]]", self.installer)
        self.assertIn("[System.Text.Encoding]::UTF8.GetString(", self.installer)
        self.assertNotIn(
            "(Invoke-WebRequest -UseBasicParsing -Uri $SetupUrl).Content", self.installer
        )

    def test_deep_shortcut_forces_deepseek_without_an_environment_variable(self):
        # PowerShell has no `VAR=1 command` prefix syntax, so the documented
        # `FORCE_DEEPSEEK=1 codex --yolo` is a parse error there. `deep` is the shell-agnostic
        # entry point: a .cmd shim resolves from PowerShell and cmd.exe alike.
        deep = (SCRIPTS / "deep.cmd").read_text(encoding="utf-8")
        self.assertIn('"%~dp0..\\codex-router.ps1" deep %*', deep)
        self.assertIn("'deep' {", self.router)
        self.assertIn("Remove-LeadingCodexToken $Remaining", self.router)
        self.assertIn("-ForceDeepSeek $true", self.router)
        self.assertIn("codex-router {deep|deepseek", self.router)

    def test_deep_accepts_an_optional_leading_codex_token(self):
        # `deep codex --yolo` reads like `sudo`, so the leading `codex` must be dropped rather
        # than forwarded as a positional argument to the real Codex CLI.
        self.assertIn("function Remove-LeadingCodexToken", self.router)
        self.assertIn("$Arguments[0] -eq 'codex'", self.router)
        self.assertIn("Select-Object -Skip 1", self.router)

    def test_installer_deploys_the_deep_shim(self):
        self.assertIn("'deep.cmd') -Destination (Join-Path $BinDir 'deep.cmd')", self.installer)

    @unittest.skipUnless(os.name == "nt", "requires Windows PowerShell")
    def test_router_recovers_when_saved_codex_path_disappears(self):
        powershell = shutil.which("powershell.exe")
        self.assertIsNotNone(powershell)

        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            install_dir = root / "CodexProviderRouter"
            router_bin = install_dir / "bin"
            upstream_bin = root / "upstream"
            router_bin.mkdir(parents=True)
            upstream_bin.mkdir()

            shutil.copy2(SCRIPTS / "codex-router.ps1", install_dir / "codex-router.ps1")
            (router_bin / "codex.cmd").write_text(
                "@echo off\r\necho ROUTER_SHIM %*\r\nexit /b 0\r\n", encoding="ascii"
            )
            saved_path = install_dir / "real-codex-path.txt"
            saved_path.write_text(str(root / "removed-router" / "codex.exe"), encoding="ascii")

            upstream = upstream_bin / "codex.cmd"
            upstream.write_text(
                "@echo off\r\necho RECOVERED_CODEX %*\r\nexit /b 0\r\n",
                encoding="ascii",
            )
            env = os.environ.copy()
            env["PATH"] = os.pathsep.join((str(router_bin), str(upstream_bin), env["PATH"]))

            result = subprocess.run(
                [
                    powershell,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(install_dir / "codex-router.ps1"),
                    "run",
                    "--version",
                ],
                env=env,
                text=True,
                capture_output=True,
                timeout=10,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stdout.strip(), "RECOVERED_CODEX --version")
            self.assertIn("Recovered original Codex", result.stderr)
            self.assertEqual(saved_path.read_text(encoding="utf-8").strip(), str(upstream))

    @unittest.skipUnless(os.name == "nt", "requires Windows PowerShell")
    def test_router_rejects_its_own_saved_shim_and_recovers(self):
        powershell = shutil.which("powershell.exe")
        self.assertIsNotNone(powershell)

        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            install_dir = root / "CodexProviderRouter"
            router_bin = install_dir / "bin"
            upstream_bin = root / "upstream"
            router_bin.mkdir(parents=True)
            upstream_bin.mkdir()

            shutil.copy2(SCRIPTS / "codex-router.ps1", install_dir / "codex-router.ps1")
            (router_bin / "codex.cmd").write_text(
                "@echo off\r\necho ROUTER_SHIM %*\r\nexit /b 0\r\n", encoding="ascii"
            )
            saved_path = install_dir / "real-codex-path.txt"
            saved_path.write_text(str(router_bin / "codex.cmd"), encoding="ascii")
            upstream = upstream_bin / "codex.cmd"
            upstream.write_text("@echo off\r\necho UPSTREAM_CODEX %*\r\nexit /b 0\r\n", encoding="ascii")
            env = os.environ.copy()
            env["PATH"] = os.pathsep.join((str(router_bin), str(upstream_bin), env["PATH"]))

            result = subprocess.run(
                [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                 str(install_dir / "codex-router.ps1"), "run", "--version"],
                env=env,
                text=True,
                capture_output=True,
                timeout=10,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stdout.strip(), "UPSTREAM_CODEX --version")
            self.assertEqual(saved_path.read_text(encoding="utf-8").strip(), str(upstream))

    @unittest.skipUnless(os.name == "nt", "requires Windows PowerShell")
    def test_router_preserves_a_valid_saved_codex_path(self):
        powershell = shutil.which("powershell.exe")
        self.assertIsNotNone(powershell)

        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            install_dir = root / "CodexProviderRouter"
            router_bin = install_dir / "bin"
            saved_bin = root / "saved"
            other_bin = root / "other"
            router_bin.mkdir(parents=True)
            saved_bin.mkdir()
            other_bin.mkdir()
            shutil.copy2(SCRIPTS / "codex-router.ps1", install_dir / "codex-router.ps1")
            shutil.copy2(SCRIPTS / "codex.cmd", router_bin / "codex.cmd")
            saved = saved_bin / "codex.cmd"
            saved.write_text("@echo off\r\necho SAVED_CODEX %*\r\nexit /b 0\r\n", encoding="ascii")
            (other_bin / "codex.cmd").write_text(
                "@echo off\r\necho OTHER_CODEX %*\r\nexit /b 0\r\n", encoding="ascii"
            )
            path_file = install_dir / "real-codex-path.txt"
            path_file.write_text(str(saved), encoding="ascii")
            env = os.environ.copy()
            env["PATH"] = os.pathsep.join((str(router_bin), str(other_bin), env["PATH"]))

            result = subprocess.run(
                [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                 str(install_dir / "codex-router.ps1"), "run", "--version"],
                env=env,
                text=True,
                capture_output=True,
                timeout=10,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stdout.strip(), "SAVED_CODEX --version")
            self.assertEqual(path_file.read_text(encoding="ascii"), str(saved))

    @unittest.skipUnless(os.name == "nt", "requires Windows PowerShell")
    def test_router_keeps_the_missing_path_error_when_no_alternative_exists(self):
        powershell = shutil.which("powershell.exe")
        self.assertIsNotNone(powershell)

        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            install_dir = root / "CodexProviderRouter"
            router_bin = install_dir / "bin"
            router_bin.mkdir(parents=True)
            shutil.copy2(SCRIPTS / "codex-router.ps1", install_dir / "codex-router.ps1")
            shutil.copy2(SCRIPTS / "codex.cmd", router_bin / "codex.cmd")
            missing = root / "removed" / "codex.exe"
            (install_dir / "real-codex-path.txt").write_text(str(missing), encoding="ascii")
            env = os.environ.copy()
            env["PATH"] = str(router_bin)

            result = subprocess.run(
                [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                 str(install_dir / "codex-router.ps1"), "run", "--version"],
                env=env,
                text=True,
                capture_output=True,
                timeout=10,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn(f"Original Codex CLI not found at: {missing}", result.stderr)

    def test_cmd_shims_do_not_contain_secrets(self):
        for name in ("codex.cmd", "codex-router.cmd", "deep.cmd"):
            content = (SCRIPTS / name).read_text(encoding="utf-8")
            self.assertIn("ExecutionPolicy Bypass", content)
            self.assertNotIn("DEEPSEEK_API_KEY", content)


if __name__ == "__main__":
    unittest.main()
