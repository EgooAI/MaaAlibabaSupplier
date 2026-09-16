"""Keep collection and tests away from the installed application's state."""

import os
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest


def _set_test_paths(patch, root):
    from backend.app.shared.utils import settings

    backend_root = root / "backend"
    backend_root.mkdir()
    patch.setattr(settings, "REPO_ROOT", root)
    patch.setattr(settings, "BACKEND_ROOT", backend_root)
    patch.setenv("MAA_CRM_DB_PATH", str(backend_root / "data" / "crm.sqlite"))
    patch.setenv("MAA_POOLS_DB_PATH", str(backend_root / "data" / "pools.db"))
    patch.chdir(root)


def _unexpected_external_access(*args, **kwargs):
    pytest.fail("External access must be mocked by the test", pytrace=False)


def pytest_configure(config):
    # Fixtures run after collection, but imports can already resolve data paths.
    temporary = TemporaryDirectory(prefix="maa-pytest-")
    config.add_cleanup(temporary.cleanup)
    patch = pytest.MonkeyPatch()
    config.add_cleanup(patch.undo)
    source_root = Path(__file__).resolve().parent
    patch.syspath_prepend(str(source_root.parent))
    patch.syspath_prepend(str(source_root / "app" / "crm_sdk"))
    for key in tuple(os.environ):
        if key.startswith(("MAA_", "MITM_", "EMAIL_", "ALIBABA_", "YAK_")):
            patch.delenv(key)
    _set_test_paths(patch, Path(temporary.name))

    import dotenv
    from maa.toolkit import Toolkit
    from backend.app.shared.utils import env, im_db_decryptor

    patch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
    patch.setattr(env, "load_dotenv", dotenv.load_dotenv)
    patch.setattr(env, "load_workdir_env", lambda env_filename=".env": Path.cwd() / env_filename)
    patch.setattr(im_db_decryptor, "retrieve_db_key", _unexpected_external_access)
    patch.setattr(Toolkit, "find_desktop_windows", _unexpected_external_access)
    patch.setattr(socket, "create_connection", _unexpected_external_access)
    patch.setattr(subprocess, "Popen", _unexpected_external_access)
    config.add_cleanup(_cleanup_runtime)


def _cleanup_runtime():
    tasks = sys.modules.get("backend.app.task_queue")
    if tasks is not None and tasks.TaskQueue._instance is not None:
        tasks.TaskQueue._instance.shutdown()

    sync = sys.modules.get("backend.app.shared.crm.sync")
    if sync is not None:
        sync._SYNC_EXECUTOR.shutdown(wait=True)
        sync._SYNC_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="crm-sync")

    middleware = sys.modules.get("backend.app.shared.backend.im_db_middleware")
    if middleware is not None:
        cls = middleware.IMDBMiddleware
        if cls._instance is not None:
            with cls._instance._lock:
                cls._instance._reset_runtime_state()
            cls._instance = None

    pools = sys.modules.get("backend.app.shared.mitm.pool")
    if pools is not None:
        for name in ("UserInfoPool", "ProductCardPool", "GenericCardPool", "InquiryCardPool", "InputPendingPool"):
            cls = getattr(pools, name)
            if cls._instance is not None:
                with cls._instance._lock:
                    cls._instance._conn.close()
                cls._instance = None

    common_modules = {
        sys.modules[name] for name in ("utils.common", "backend.app.crm_sdk.utils.common")
        if name in sys.modules
    }
    for common in common_modules:
        for engine in list(common._SHARED_ENGINES._items.values()):
            engine.dispose()
        common._DATABASE_LOCKS._items.clear()


@pytest.fixture(autouse=True)
def isolated_backend():
    _cleanup_runtime()
    with TemporaryDirectory(prefix="test-", dir=Path.cwd()) as temporary:
        with pytest.MonkeyPatch.context() as patch:
            _set_test_paths(patch, Path(temporary))
            try:
                yield
            finally:
                _cleanup_runtime()
