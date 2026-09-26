"""Offline publication checks, including a real Windows file-sharing lock."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from publish_chimera_release import APPLICATION, ReleasePublishError, publish


def checksum(payload):
    return hashlib.sha256(payload).hexdigest()


def test_fixed_entry_and_previous_build_survive_same_version_update():
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        source, directory = root / 'staged.exe', root / 'release'
        first, second = b'MZ-first-offline-fixture', b'MZ-second-offline-fixture'
        source.write_bytes(first)
        result = publish(source, '1.0.5', checksum(first), directory)
        target = Path(result['target'])
        assert target.name == APPLICATION + '.exe' and target.read_bytes() == first
        source.write_bytes(second)
        result = publish(source, '1.0.5', checksum(second), directory)
        assert Path(result['target']) == target and target.read_bytes() == second
        assert Path(result['backup']).read_bytes() == first
        assert source.read_bytes() == second
        record = json.loads((directory / 'latest.json').read_text(encoding='utf-8'))
        assert record['sha256'] == checksum(second) and record['version'] == '1.0.5'
        assert (directory / (APPLICATION + '.sha256')).read_text().split()[0] == checksum(second)
        backups = list((directory / 'archive').iterdir())
        assert not publish(source, '1.0.5', checksum(second), directory)['changed']
        assert list((directory / 'archive').iterdir()) == backups
        # Future version numbers still publish to the same shortcut target.
        assert publish(source, '1.0.6', checksum(second), directory)['target'] == str(target)


def test_checksum_failure_cannot_replace_existing_release():
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        source, directory = root / 'staged.exe', root / 'release'
        source.write_bytes(b'MZ-original-offline-fixture')
        result = publish(source, '1.0.5', checksum(source.read_bytes()), directory)
        before = {file.name: file.read_bytes() for file in directory.iterdir()}
        source.write_bytes(b'MZ-corrupted-offline-fixture')
        try:
            publish(source, '1.0.5', result['sha256'], directory)
        except ReleasePublishError:
            pass
        else:
            raise AssertionError('A mismatched package was published')
        assert before == {file.name: file.read_bytes() for file in directory.iterdir()}


def test_windows_lock_preserves_release_and_retry_after_close_succeeds():
    if os.name != 'nt':
        return
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        source, directory = root / 'staged.exe', root / 'release'
        first, second = b'MZ-old-offline-fixture', b'MZ-new-offline-fixture'
        source.write_bytes(first)
        result = publish(source, '1.0.5', checksum(first), directory)
        target = Path(result['target'])
        manifest = (directory / 'latest.json').read_bytes()
        sidecar = (directory / (APPLICATION + '.sha256')).read_bytes()
        source.write_bytes(second)
        # GENERIC_READ, FILE_SHARE_READ only: deny writes and deletion, just as
        # a running executable prevents replacement. No executable is launched.
        handle = kernel.CreateFileW(str(target), 0x80000000, 1, None, 3, 0x80, None)
        assert handle != wintypes.HANDLE(-1).value, ctypes.get_last_error()
        try:
            try:
                publish(source, '1.0.5', checksum(second), directory)
            except ReleasePublishError as error:
                assert '关闭工具后重试' in str(error)
            else:
                raise AssertionError('A locked release was replaced')
            assert target.read_bytes() == first and source.read_bytes() == second
            assert (directory / 'latest.json').read_bytes() == manifest
            assert (directory / (APPLICATION + '.sha256')).read_bytes() == sidecar
            assert not list(directory.glob('*.tmp'))
        finally:
            kernel.CloseHandle(handle)
        result = publish(source, '1.0.5', checksum(second), directory)
        assert target.read_bytes() == second and Path(result['backup']).read_bytes() == first


def main():
    tests = [test_fixed_entry_and_previous_build_survive_same_version_update,
             test_checksum_failure_cannot_replace_existing_release,
             test_windows_lock_preserves_release_and_retry_after_close_succeeds]
    for test in tests:
        test()
        print('PASS', test.__name__)


if __name__ == '__main__':
    main()
