import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("upstream_maintenance.py")
SPEC = importlib.util.spec_from_file_location("upstream_maintenance", MODULE_PATH)
maintenance = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = maintenance
SPEC.loader.exec_module(maintenance)


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=repo, check=check, text=True, capture_output=True
    )


def write_owner_files(repo: Path) -> None:
    (repo / ".github/workflows").mkdir(parents=True)
    (repo / "src/themes/classic/utils").mkdir(parents=True)
    (repo / ".github/workflows/run_data_sync.yml").write_text(
        "name: Run Data Sync\nenv:\n"
        + "".join(f"  {key}: owner_{key}\n" for key in maintenance.ENV_KEYS)
        + "jobs:\n  sync: {}\n",
        encoding="utf-8",
    )
    (repo / "config.yml").write_text(
        "theme: dark\ntheme_preset: classic\n", encoding="utf-8"
    )
    (repo / "src/themes/classic/utils/const.ts").write_text(
        "const PRIVACY_MODE = true;\n"
        "const CHINESE_LOCATION_INFO_MESSAGE_FIRST = 'Always Forward';\n"
        "const CHINESE_LOCATION_INFO_MESSAGE_SECOND = 'Just run';\n",
        encoding="utf-8",
    )


class RepoCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.repo = Path(self.temp.name)
        git(self.repo, "init", "-b", "master")
        git(self.repo, "config", "user.name", "Test")
        git(self.repo, "config", "user.email", "test@example.invalid")
        write_owner_files(self.repo)
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "base")
        git(self.repo, "branch", "upstream")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def commit(self, message: str) -> None:
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", message)


class MergeTests(RepoCase):
    def test_three_way_merge_keeps_owner_values(self) -> None:
        git(self.repo, "switch", "upstream")
        (self.repo / "config.yml").write_text(
            "theme: light\ntheme_preset: classic\n", encoding="utf-8"
        )
        (self.repo / "upstream.txt").write_text("new\n", encoding="utf-8")
        self.commit("upstream change")
        git(self.repo, "switch", "master")

        result = maintenance.merge_ref(self.repo, "upstream", has_workflow_token=False)

        self.assertTrue(result.updated)
        self.assertFalse(result.workflow_changes)
        self.assertEqual(
            "classic", maintenance.owner_snapshot(self.repo)["theme_preset"]
        )
        self.assertEqual("new\n", (self.repo / "upstream.txt").read_text())

    def test_conflict_reports_path_and_aborts_merge(self) -> None:
        shared = self.repo / "shared.txt"
        shared.write_text("base\n", encoding="utf-8")
        self.commit("shared base")
        git(self.repo, "branch", "-f", "upstream")
        shared.write_text("owner\n", encoding="utf-8")
        self.commit("owner change")
        git(self.repo, "switch", "upstream")
        shared.write_text("upstream\n", encoding="utf-8")
        self.commit("upstream conflict")
        git(self.repo, "switch", "master")

        with self.assertRaisesRegex(maintenance.MaintenanceError, "shared.txt"):
            maintenance.merge_ref(self.repo, "upstream", has_workflow_token=False)

        self.assertEqual("owner\n", shared.read_text(encoding="utf-8"))
        self.assertEqual("", git(self.repo, "status", "--porcelain").stdout)

    def test_changed_owner_value_rejects_and_aborts_merge(self) -> None:
        git(self.repo, "switch", "upstream")
        path = self.repo / "src/themes/classic/utils/const.ts"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "const PRIVACY_MODE = true;", "const PRIVACY_MODE = false;"
            ),
            encoding="utf-8",
        )
        self.commit("change protected value")
        git(self.repo, "switch", "master")

        with self.assertRaisesRegex(maintenance.MaintenanceError, "PRIVACY_MODE"):
            maintenance.merge_ref(self.repo, "upstream", has_workflow_token=False)

        self.assertIn("PRIVACY_MODE = true", path.read_text(encoding="utf-8"))
        self.assertEqual("", git(self.repo, "status", "--porcelain").stdout)


class PublishTests(unittest.TestCase):
    def test_rejected_non_fast_forward_push_never_dispatches(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp:
            root = Path(temp)
            remote = root / "remote.git"
            owner = root / "owner"
            other = root / "other"
            git(root, "init", "--bare", "--initial-branch=master", str(remote))
            git(root, "clone", str(remote), str(owner))
            git(owner, "config", "user.name", "Test")
            git(owner, "config", "user.email", "test@example.invalid")
            (owner / "file.txt").write_text("base\n")
            git(owner, "add", ".")
            git(owner, "commit", "-m", "base")
            git(owner, "push", "origin", "master")
            git(root, "clone", str(remote), str(other))
            git(other, "config", "user.name", "Other")
            git(other, "config", "user.email", "other@example.invalid")
            (other / "remote.txt").write_text("remote wins\n")
            git(other, "add", ".")
            git(other, "commit", "-m", "remote advance")
            git(other, "push", "origin", "master")
            (owner / "local.txt").write_text("local\n")
            git(owner, "add", ".")
            git(owner, "commit", "-m", "local change")
            dispatched = []

            with self.assertRaises(maintenance.MaintenanceError):
                maintenance.push_then_dispatch(owner, lambda: dispatched.append(True))

            self.assertEqual([], dispatched)
            remote_head = git(
                root, "--git-dir", str(remote), "show", "master:remote.txt"
            )
            self.assertEqual("remote wins\n", remote_head.stdout)


if __name__ == "__main__":
    unittest.main()
