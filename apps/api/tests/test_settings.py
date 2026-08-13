from pathlib import Path

import pytest

from app import settings as settings_module
from app.settings import REPO_ROOT, Settings, find_repo_root

OVERRIDABLE_ENV_KEYS = (
    "ARTIFACTS_DIR",
    "DATABASE_URL",
    "MYSQL_DATABASE",
    "MYSQL_HOST",
    "MYSQL_PASSWORD",
    "MYSQL_PORT",
    "MYSQL_USER",
    "REDIS_URL",
)


@pytest.fixture
def isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ignore ambient configuration so assertions describe the checked-in defaults."""
    for key in OVERRIDABLE_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def build_settings() -> Settings:
    return Settings(_env_file=None)


def test_repo_root_points_at_the_worktree_that_owns_the_source_tree() -> None:
    package_dir = Path(settings_module.__file__).resolve().parent

    assert package_dir == REPO_ROOT / "apps" / "api" / "app"
    assert (REPO_ROOT / "compose.yaml").is_file()


def test_find_repo_root_returns_the_nearest_ancestor_holding_a_marker(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    package_dir = checkout / "apps" / "api" / "app"
    package_dir.mkdir(parents=True)
    (checkout / "compose.yaml").write_text("name: test\n", encoding="utf-8")

    assert find_repo_root(package_dir) == checkout


def test_find_repo_root_falls_back_to_the_start_directory_without_markers(tmp_path: Path) -> None:
    package_parent = tmp_path / "app"
    package_parent.mkdir()

    assert find_repo_root(package_parent, markers=("marker-that-never-exists",)) == package_parent


@pytest.mark.parametrize("relative_cwd", ["", "apps/api", "apps/web"])
def test_artifacts_dir_stays_inside_the_repo_for_every_process_cwd(
    isolated_env: None,
    monkeypatch: pytest.MonkeyPatch,
    relative_cwd: str,
) -> None:
    monkeypatch.chdir(REPO_ROOT / relative_cwd if relative_cwd else REPO_ROOT)

    artifacts_dir = build_settings().artifacts_dir

    assert artifacts_dir == REPO_ROOT / "data" / "artifacts"
    assert REPO_ROOT in artifacts_dir.parents


def test_artifacts_dir_ignores_an_unrelated_cwd(
    isolated_env: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)

    assert build_settings().artifacts_dir == REPO_ROOT / "data" / "artifacts"


def test_relative_artifacts_dir_override_is_anchored_to_the_repo_root(
    isolated_env: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("ARTIFACTS_DIR", "data/artifacts")
    monkeypatch.chdir(tmp_path)

    assert build_settings().artifacts_dir == REPO_ROOT / "data" / "artifacts"


def test_absolute_artifacts_dir_override_is_honoured(
    isolated_env: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    mounted_dir = tmp_path / "mounted" / "artifacts"
    monkeypatch.setenv("ARTIFACTS_DIR", str(mounted_dir))

    assert build_settings().artifacts_dir == mounted_dir


def test_env_file_is_the_repo_root_dotenv_so_api_and_worker_share_it() -> None:
    env_file = Path(str(Settings.model_config["env_file"]))

    assert env_file.is_absolute()
    assert env_file == REPO_ROOT / ".env"


def test_database_url_is_composed_from_mysql_components(
    isolated_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MYSQL_HOST", "mysql")
    monkeypatch.setenv("MYSQL_PORT", "3307")
    monkeypatch.setenv("MYSQL_DATABASE", "quickstudy")
    monkeypatch.setenv("MYSQL_USER", "quickstudy")
    monkeypatch.setenv("MYSQL_PASSWORD", "local-password")

    expected = "mysql://quickstudy:local-password@mysql:3307/quickstudy"

    assert build_settings().database_url == expected


def test_composed_database_url_percent_encodes_credentials(
    isolated_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MYSQL_USER", "svc@corp")
    monkeypatch.setenv("MYSQL_PASSWORD", "p@ss:w/rd?1")

    database_url = build_settings().database_url

    assert database_url is not None
    assert database_url.startswith("mysql://svc%40corp:p%40ss%3Aw%2Frd%3F1@")


def test_explicit_database_url_overrides_the_composed_value(
    isolated_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "mysql://api:secret@mysql:3306/other")
    monkeypatch.setenv("MYSQL_PASSWORD", "unused")

    assert build_settings().database_url == "mysql://api:secret@mysql:3306/other"


def test_checked_in_defaults_carry_no_credentials(isolated_env: None) -> None:
    defaults = build_settings()

    assert defaults.mysql_password == ""
    assert defaults.database_url == "mysql://quickstudy:@127.0.0.1:3306/quickstudy"
