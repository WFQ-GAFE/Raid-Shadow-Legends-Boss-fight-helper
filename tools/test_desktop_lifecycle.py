"""Offline regressions: shutdown races, helper ownership, persistent resources."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch
import subprocess
import threading
import ast

import desktop_lifecycle as lifecycle
import controller_manager as managers
import chimera_web as web
import chimera_icons as icons
import strategy_storage as storage


def test_agent_survives_extraction_cleanup_and_is_never_replaced():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / 'extraction' / 'agent.dll'
        source.parent.mkdir()
        source.write_bytes(b'offline-agent-build-one')
        target = lifecycle.persistent_agent(source, root / 'user')
        assert target.read_bytes() == source.read_bytes()
        with patch.object(lifecycle, 'atomic_write_bytes') as write:
            assert lifecycle.persistent_agent(source, root / 'user') == target
            write.assert_not_called()
        source.write_bytes(b'offline-agent-build-two')
        second = lifecycle.persistent_agent(source, root / 'user')
        source.unlink()
        assert second != target and target.read_bytes() == b'offline-agent-build-one'
        assert second.read_bytes() == b'offline-agent-build-two'


def test_corrupt_agent_cache_is_not_loaded_or_overwritten():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / 'source.dll'
        source.write_bytes(b'agent')
        target = lifecycle.persistent_agent(source, root)
        target.write_bytes(b'corrupt')
        try:
            lifecycle.persistent_agent(source, root)
        except RuntimeError:
            pass
        else:
            raise AssertionError('corrupt DLL accepted')
        assert target.read_bytes() == b'corrupt'


def fake_window(loaded=True):
    window = Mock()
    event = threading.Event()
    if loaded:
        event.set()
    window.events = SimpleNamespace(loaded=event)
    return window


def test_close_before_load_has_no_popup_or_window_operations():
    startup = lifecycle.WindowStartup()
    window = fake_window(False)
    notify = Mock()
    startup.close()
    startup.reveal(window, 'offline', notify, timeout=0, mount_delay=0)
    assert not window.mock_calls and not notify.called


def test_close_during_mount_cancels_navigation():
    startup = lifecycle.WindowStartup()
    window = fake_window()
    window.show.side_effect = startup.close
    notify = Mock()
    startup.reveal(window, 'offline', notify, mount_delay=0)
    window.evaluate_js.assert_not_called()
    window.load_url.assert_not_called()
    notify.assert_not_called()


def test_close_while_waiting_cancels_timeout():
    startup = lifecycle.WindowStartup()
    window = fake_window(False)
    window.events.loaded = Mock()
    window.events.loaded.wait.side_effect = lambda _: (startup.close() or False)
    notify = Mock()
    startup.reveal(window, 'offline', notify, timeout=0)
    notify.assert_not_called()
    window.show.assert_not_called()


def test_real_startup_failure_remains_visible_and_mount_retries_once():
    notify = Mock()
    lifecycle.WindowStartup().reveal(fake_window(False), 'offline', notify, timeout=0)
    notify.assert_called_once()
    window = fake_window()
    window.evaluate_js.return_value = False
    lifecycle.WindowStartup().reveal(window, 'offline', Mock(), mount_delay=0)
    window.load_url.assert_called_once_with('offline')


def test_cleanup_failure_does_not_break_manager_reset():
    with TemporaryDirectory() as directory, patch.object(managers, 'PROJECT_ROOT', Path(directory)):
        manager = managers.ControllerManager()
        manager.preparing = True
        manager.closing = True
        manager.config_path = Path(directory) / 'session.json'
        with patch.object(managers, 'remove_session_file', return_value=False):
            manager._prepare_and_run(1, 'offline', 1, 'chimera')
        assert not manager.preparing and manager.process is None and manager.config_path is None
        assert manager.error is None and manager.status == '已关闭'
        assert 'temporary_config_cleanup_deferred' in manager.critical_journal.path.read_text(encoding='utf-8')


def test_cancelled_initialization_error_does_not_report_failure():
    with TemporaryDirectory() as directory, patch.object(managers, 'PROJECT_ROOT', Path(directory)):
        manager = managers.ControllerManager()
        def fail(*args):
            manager.request_shutdown()
            raise OSError('offline initialization interrupted')
        with patch.object(managers, 'require_expected_account', side_effect=fail):
            manager._prepare_and_run(1, 'offline', 1, 'chimera')
        assert manager.error is None and manager.status == '已关闭'
        assert not any('启动失败' in line for line in manager.snapshot()['logs'])


def test_cancelled_helpers_are_reaped_without_killing_dispatched_load():
    for readonly in (True, False):
        with TemporaryDirectory() as directory, patch.object(managers, 'PROJECT_ROOT', Path(directory)):
            manager = managers.ControllerManager()
            child = Mock(args=['offline'], returncode=0)
            child.poll.return_value = 0
            calls = []
            def communicate(timeout):
                calls.append(timeout)
                if len(calls) == 1:
                    manager.request_shutdown()
                    raise subprocess.TimeoutExpired(child.args, timeout)
                return b'{}', b''
            child.communicate.side_effect = communicate
            with patch.object(managers.subprocess, 'Popen', return_value=child):
                try:
                    manager._run_injector(['1', '--check-only'] if readonly else ['1'], 35)
                except managers.LaunchCancelled:
                    pass
                else:
                    raise AssertionError('cancel was ignored')
            assert child.terminate.call_count == int(readonly)
            child.kill.assert_not_called()
            assert manager.preparation_process is None and len(calls) == 2


def test_cancel_just_before_helper_spawn_prevents_launch():
    with TemporaryDirectory() as directory, patch.object(managers, 'PROJECT_ROOT', Path(directory)):
        manager = managers.ControllerManager()
        def command(*args):
            manager.request_shutdown()
            return ['offline']
        with patch.object(managers, 'worker_command', side_effect=command), patch.object(managers.subprocess, 'Popen') as launch:
            try:
                manager._run_injector(['1'], 35)
            except managers.LaunchCancelled:
                pass
            else:
                raise AssertionError('cancel was ignored')
            launch.assert_not_called()


def test_temporary_cleanup_preserves_save_success_and_original_error():
    with TemporaryDirectory() as directory:
        path = Path(directory) / 'saved.json'
        with patch.object(Path, 'unlink', side_effect=PermissionError('cleanup locked')):
            storage.atomic_write_bytes(path, b'old')
            assert path.read_bytes() == b'old'
            with patch.object(storage.os, 'replace', side_effect=OSError('commit failed')):
                try:
                    storage.atomic_write_bytes(path, b'new')
                except OSError as error:
                    assert str(error) == 'commit failed'
                else:
                    raise AssertionError('actual save failure hidden')
            assert path.read_bytes() == b'old'
            assert lifecycle.remove_session_file(path) is False


def test_closed_client_is_not_sent_another_error_response():
    handler = object.__new__(web.ChimeraHandler)
    handler.path = '/'
    with patch.object(handler, '_static', side_effect=ConnectionResetError), patch.object(handler, '_error') as error:
        handler.do_GET()
        error.assert_not_called()
    with patch.object(handler, '_json', side_effect=BrokenPipeError):
        handler._error(ValueError('real error'))


def test_cancelled_asset_preload_does_no_work():
    with patch.object(icons, 'ensure_icon_cache') as effects, patch.object(icons, 'ensure_game_avatar_cache') as avatars, patch.object(icons, 'ensure_game_reward_cache') as rewards, patch.object(icons, 'ensure_game_skill_cache') as skills:
        status = icons.preload_game_visuals({}, [1], cancelled=lambda: True)
        assert not any(status.values())
        for operation in (effects, avatars, rewards, skills):
            operation.assert_not_called()


def test_failed_server_initialization_releases_service_and_singleton():
    service, mutex = Mock(), Mock()
    with patch.object(web.sys, 'argv', ['offline']), patch.object(web, 'NamedMutex', return_value=mutex), patch.object(web, 'ChimeraService', return_value=service), patch.object(web, 'ChimeraHttpServer', side_effect=OSError('bind failed')), patch.object(web, 'desktop_lifecycle_log'):
        try:
            web.main()
        except OSError as error:
            assert str(error) == 'bind failed'
        else:
            raise AssertionError('startup error hidden')
    service.shutdown.assert_called_once()
    mutex.close.assert_called_once()


def backend_methods():
    # Compile the exact vendored methods without importing CLR or starting a browser.
    source = Path(__file__).resolve().parents[1] / 'third_party/python/webview/platforms/edgechromium.py'
    tree = ast.parse(source.read_text(encoding='utf-8-sig'))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'EdgeChrome')
    methods = [node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in {'clear_user_data', 'on_webview_ready', 'evaluate_js'}]
    class Generic:
        def __getitem__(self, key):
            return lambda value: value
    namespace = {'_state': {'private_mode': True}, 'Convert': SimpleNamespace(ToInt32=int),
                 'Process': Mock(), 'logger': Mock(), 'shutil': Mock(), 'Semaphore': threading.Semaphore,
                 'Func': Generic(), 'Action': Generic(), 'Task': Generic(), 'String': str, 'Object': object,
                 'json': __import__('json')}
    exec(compile(ast.Module(body=methods, type_ignores=[]), str(source), 'exec'), namespace)
    return namespace


def backend_instance():
    return SimpleNamespace(_closing=False, _pending_js=set(), _pending_js_lock=threading.Lock(),
        webview=Mock(CoreWebView2=None), user_data_folder='offline-cache', syncContextTaskScheduler=None)


def test_uninitialized_webview_is_disposed_and_pending_js_released():
    ns = backend_methods()
    instance = backend_instance()
    waiting = threading.Semaphore(0)
    instance._pending_js.add(waiting)
    ns['shutil'].rmtree.side_effect = PermissionError('cache locked')
    ns['clear_user_data'](instance)
    assert instance._closing and waiting.acquire(blocking=False)
    instance.webview.Dispose.assert_called_once()
    ns['Process'].GetProcessById.assert_not_called()
    ns['logger'].warning.assert_called_once()


def test_late_webview_ready_never_uses_disposed_control():
    ns = backend_methods()
    instance = backend_instance()
    instance._closing = True
    sender, args = Mock(), Mock()
    ns['on_webview_ready'](instance, sender, args)
    assert not sender.mock_calls and not args.mock_calls


def test_failed_js_task_releases_waiter_without_unhandled_exception():
    ns = backend_methods()
    instance = backend_instance()
    class FailedTask:
        @property
        def Result(self):
            raise RuntimeError('renderer closed')
    instance.webview.Invoke.side_effect = lambda fn: fn()
    instance.webview.ExecuteScriptAsync.return_value.ContinueWith.side_effect = lambda fn, scheduler: fn(FailedTask())
    assert ns['evaluate_js'](instance, 'offline', True) is None
    assert not instance._pending_js
    ns['logger'].exception.assert_called_once()


def test_close_during_js_invocation_releases_local_waiter():
    ns = backend_methods()
    instance = backend_instance()
    instance.webview.Invoke.side_effect = lambda fn: ns['clear_user_data'](instance)
    assert ns['evaluate_js'](instance, 'offline', True) is None
    assert not instance._pending_js and instance._closing
    ns['logger'].exception.assert_not_called()


def test_failed_worker_retains_marker_for_noninteractive_error_handler():
    import inject_probe
    argv = ['offline', '--internal-worker', 'injector']
    with patch.object(web.sys, 'argv', argv), patch.object(web, 'restore_internal_worker_streams'), patch.object(inject_probe, 'main', side_effect=RuntimeError('offline failure')):
        try:
            web.run_internal_worker('injector', ['--help'])
        except RuntimeError:
            pass
        else:
            raise AssertionError('real worker failure hidden')
        assert web.sys.argv is argv


def test_frontend_error_endpoint_records_bounded_redacted_diagnostics_only():
    handler = object.__new__(web.ChimeraHandler)
    handler.path = '/api/ui-error'
    with patch.object(handler, '_write_authorized', return_value=True), patch.object(handler, '_body', return_value={
        'kind': 'react_render', 'message': 'conditions.map failed', 'stack': 'http://localhost/?token=private-token\n' + 'x'*8000,
        'unrelatedState': {'account': 'not-for-logging'}}), patch.object(handler, '_json') as reply, patch.object(web, 'desktop_lifecycle_log') as record:
        handler.do_POST()
    message = record.call_args.args[0]
    assert 'conditions.map failed' in message and 'private-token' not in message
    assert 'not-for-logging' not in message and len(message) < 6500
    reply.assert_called_once_with({'recorded': True})
