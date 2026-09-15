import os
import pathlib
import subprocess
import tempfile
import unittest


PROJECT = pathlib.Path(__file__).parents[1]
INSTALLER = PROJECT / "scripts" / "install.sh"


class InstallerTests(unittest.TestCase):
    def test_key_file_is_imported_but_not_copied_to_installed_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            home = root / "home"
            bin_dir = root / "bin"
            home.mkdir()
            bin_dir.mkdir()
            key = root / "deepseek.txt"
            secret = "dummy-deepseek-key-for-installer-test"
            key.write_text(secret + "\n", encoding="utf-8")

            curl = bin_dir / "curl"
            curl.write_text(
                "#!/bin/sh\n"
                "while [ \"$1\" != \"-o\" ]; do shift; done\n"
                "output=$2\n"
                "printf '%s\\n' \"cat <<'CODEX_MODELS_JSON'\" "
                "'{\"models\":[{\"slug\":\"deepseek-flash\"}]}' "
                "'CODEX_MODELS_JSON' > \"$output\"\n",
                encoding="utf-8",
            )
            security = bin_dir / "security"
            security.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' \"$@\" > \"$SECURITY_CAPTURE\"\n",
                encoding="utf-8",
            )
            curl.chmod(0o700)
            security.chmod(0o700)
            capture = root / "security-arguments.txt"
            env = os.environ.copy()
            env.update(
                HOME=str(home),
                PATH=f"{bin_dir}:/usr/bin:/bin:/usr/sbin",
                SECURITY_CAPTURE=str(capture),
                USER="installer-test",
            )

            result = subprocess.run(
                ["/bin/zsh", str(INSTALLER), "--key-file", str(key)],
                env=env,
                text=True,
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn(secret, capture.read_text(encoding="utf-8"))
            self.assertEqual(key.stat().st_mode & 0o777, 0o600)
            installed_roots = (home / ".codex", home / ".config", home / ".local")
            for installed_root in installed_roots:
                for path in installed_root.rglob("*"):
                    if path.is_file() and not path.is_symlink():
                        self.assertNotIn(secret, path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
