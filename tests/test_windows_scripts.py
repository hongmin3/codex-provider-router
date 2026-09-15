import pathlib
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

    def test_cmd_shims_do_not_contain_secrets(self):
        for name in ("codex.cmd", "codex-router.cmd"):
            content = (SCRIPTS / name).read_text(encoding="utf-8")
            self.assertIn("ExecutionPolicy Bypass", content)
            self.assertNotIn("DEEPSEEK_API_KEY", content)


if __name__ == "__main__":
    unittest.main()
