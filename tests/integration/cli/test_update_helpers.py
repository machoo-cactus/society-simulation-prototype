from tests.helpers.paths import REPOSITORY_ROOT


def test_update_helpers_make_fast_forward_pull_explicit() -> None:
    shell = (REPOSITORY_ROOT / "update.sh").read_text(encoding="utf-8")
    powershell = (REPOSITORY_ROOT / "update.ps1").read_text(encoding="utf-8")

    assert "pull=false" in shell
    assert '== "--pull"' in shell
    assert "git pull --ff-only" in shell
    assert "--skip-pull" not in shell

    assert "[switch]$Pull" in powershell
    assert "if ($Pull)" in powershell
    assert "git pull --ff-only" in powershell
    assert "SkipPull" not in powershell


def test_update_helpers_require_python_312_and_create_writable_catalogs() -> None:
    shell = (REPOSITORY_ROOT / "update.sh").read_text(encoding="utf-8")
    powershell = (REPOSITORY_ROOT / "update.ps1").read_text(encoding="utf-8")

    for content in (shell, powershell):
        assert "3.12" in content
        assert "data" in content
        assert "characters" in content
        assert "scenarios" in content
        assert "elements" in content
        assert "runs" in content
