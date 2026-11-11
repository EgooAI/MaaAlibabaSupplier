from backend.app.shared.utils import paths, settings


def test_default_paths_follow_current_settings():
    backend = settings.resolve_backend_root()
    repo = settings.resolve_repo_root()
    assert paths.data_dir() == backend / "data"
    assert paths.log_dir() == backend / "data" / "logs"
    assert paths.cache_dir() == backend / "data" / ".cache"
    assert paths.debug_dir() == backend / "debug"
    assert paths.assets_dir() == backend / "assets"
    assert paths.deps_bin_dir() == backend / "deps" / "bin"
    assert paths.frontend_out_dir() == repo / "frontend" / "out"
    assert paths.build_info_path() == repo / "build-info.json"


def test_backend_relative_paths_preserve_absolute_ones(tmp_path):
    assert paths.resolve_backend_relative("data/crm.sqlite", tmp_path) == tmp_path / "data" / "crm.sqlite"
    assert paths.resolve_backend_relative(tmp_path / "crm.sqlite", tmp_path) == tmp_path / "crm.sqlite"


def test_explicit_roots_override_settings(tmp_path):
    assert paths.data_dir(tmp_path) == tmp_path / "data"
    assert paths.log_dir(tmp_path) == tmp_path / "data" / "logs"
    assert paths.auth_db_path(tmp_path) == tmp_path / "data" / "auth.sqlite"
    assert paths.frontend_out_dir(tmp_path) == tmp_path / "frontend" / "out"
    assert paths.build_id_path(tmp_path) == tmp_path / "frontend" / "out" / "build-id.json"
