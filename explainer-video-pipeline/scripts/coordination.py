"""Cooperating-process locks for shared indices and browser submission phases.

The kernel owns the lock, so a dead process cannot leave a stale exclusive lease.
Never delete the persistent .lock file: replacing its inode defeats coordination.
This does not lock browsers/editors themselves or protect callers bypassing it.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
import uuid


class LockTimeoutError(TimeoutError):
    """The resource stayed owned by another cooperating process."""


def resource_key(resource):
    if isinstance(resource, os.PathLike):
        return os.path.normcase(str(Path(resource).resolve()))
    if not isinstance(resource, str) or not resource.strip():
        raise ValueError('Resource must be a nonempty stable key or a Path')
    return resource.strip()


def _try_acquire(stream):
    stream.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
            return False
        raise


def _release(stream):
    stream.seek(0)
    if os.name == 'nt':
        import msvcrt
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _owner_write(path, owner):
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(owner, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


@contextmanager
def resource_lock(resource, lock_root=None, timeout=1800, poll_seconds=0.05):
    """Hold a stable resource across processes; yielded metadata is diagnostic.

    All callers must use the same lock_root AND resource. For shared editor
    indices use a Path key and the index's adjacent .pipeline-locks directory.
    For browser phases explicitly pass the shared project lock root. The default
    is cwd/.video-coordination/locks, suitable only for a common working directory.
    """
    if not math.isfinite(timeout) or timeout < 0:
        raise ValueError('Lock timeout must be finite and nonnegative')
    if not math.isfinite(poll_seconds) or poll_seconds <= 0:
        raise ValueError('Lock poll interval must be finite and positive')
    key = resource_key(resource)
    root = Path(lock_root or Path.cwd() / '.video-coordination' / 'locks').resolve()
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / (hashlib.sha256(key.encode('utf-8')).hexdigest() + '.lock')
    owner_path = lock_path.with_suffix('.owner.json')
    started = time.monotonic()
    with lock_path.open('a+b') as stream:
        # msvcrt locks the same first byte in every process; no file truncation.
        if stream.seek(0, os.SEEK_END) == 0:
            stream.write(b'\0'); stream.flush()
        while not _try_acquire(stream):
            elapsed = time.monotonic() - started
            if elapsed >= timeout:
                raise LockTimeoutError(f'Timed out after {elapsed:.3f}s waiting for {key!r}; lock: {lock_path}')
            time.sleep(min(poll_seconds, timeout - elapsed))
        lease = {'resource': key, 'lock_path': str(lock_path), 'owner_file': str(owner_path),
                 'lease_id': uuid.uuid4().hex, 'pid': os.getpid(), 'acquired_at_unix': time.time(),
                 'wait_seconds': round(time.monotonic() - started, 6), 'held': True,
                 'cooperating_processes_only': True}
        try:
            _owner_write(owner_path, lease)
            yield lease
        finally:
            try:
                _owner_write(owner_path, {**lease, 'held': False, 'released_at_unix': time.time()})
            finally:
                _release(stream)


def hold_lease(resource, lock_root, timeout=0, ttl=600, owner_id=None):
    """Keep an OS lock alive across tool calls until matching release or TTL.

    Start this helper as a running exec session, then perform browser operations
    only after its acquired JSON was emitted. The browser itself is not locked:
    every cooperating chat must use the same resource/root and check lease expiry.
    """
    if not math.isfinite(ttl) or not 0 < ttl <= 3600:
        raise ValueError('Lease TTL must be finite, positive, and at most 3600 seconds')
    if not isinstance(owner_id, str) or not owner_id.strip():
        raise ValueError('owner_id must identify the episode/run')
    root = Path(lock_root).resolve()
    leases = root / 'leases'; leases.mkdir(parents=True, exist_ok=True)
    receipt = None
    status = None
    reason = 'helper_interrupted'
    try:
        with resource_lock(resource, root, timeout) as lease:
            lease.update(owner_id=owner_id.strip(), release_token=secrets.token_hex(16),
                         expires_at_unix=time.time() + ttl, ttl_seconds=ttl)
            receipt = leases / (lease['lease_id'] + '.json')
            signal = leases / (lease['lease_id'] + '.release.json')
            lease.update(lease_file=str(receipt), release_file=str(signal))
            status = {**lease, 'status': 'held', 'invalid_release_signals': 0}
            _owner_write(Path(lease['owner_file']), lease)
            _owner_write(receipt, status)
            print(json.dumps({'ok': True, 'event': 'acquired', **status}, ensure_ascii=False), flush=True)
            deadline = time.monotonic() + ttl
            rejected_bytes = None
            while time.monotonic() < deadline:
                if signal.is_file():
                    raw = signal.read_bytes()
                    try:
                        requested = json.loads(raw.decode('utf-8'))
                    except (UnicodeError, ValueError):
                        requested = {}
                    if not isinstance(requested, dict):
                        requested = {}
                    if (requested.get('lease_id') == lease['lease_id']
                            and secrets.compare_digest(str(requested.get('release_token', '')).encode('utf-8'), lease['release_token'].encode('utf-8'))):
                        reason = 'matching_release'
                        break
                    if raw != rejected_bytes:
                        status['invalid_release_signals'] += 1
                        _owner_write(receipt, status)
                        rejected_bytes = raw
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
            else:
                reason = 'ttl_expired'
    finally:
        # This receipt is written only after the OS-lock context has exited.
        if receipt is not None:
            status.update(held=False, status='released', release_reason=reason, released_at_unix=time.time())
            _owner_write(receipt, status)
            print(json.dumps({'ok': True, 'event': 'released', 'lease_id': status['lease_id'],
                              'release_reason': reason, 'lease_file': str(receipt)}, ensure_ascii=False), flush=True)
    return 0


def release_lease(lease_file, lease_id, token, wait_seconds=5):
    """Signal only this exact lease; no lock file deletion or stale-owner stealing."""
    if not re.fullmatch(r'[0-9a-f]{32}', lease_id or ''):
        raise ValueError('lease_id must be the exact acquired 32-character identifier')
    if not math.isfinite(wait_seconds) or wait_seconds < 0:
        raise ValueError('Release wait must be finite and nonnegative')
    receipt = Path(lease_file).resolve()
    if receipt.name != lease_id + '.json' or receipt.parent.name != 'leases':
        raise ValueError('Lease receipt path does not match the supplied lease_id')
    state = json.loads(receipt.read_text(encoding='utf-8'))
    if (not isinstance(state, dict) or state.get('lease_id') != lease_id
            or not secrets.compare_digest(str(token).encode('utf-8'), str(state.get('release_token', '')).encode('utf-8'))):
        raise ValueError('Release token/lease_id does not match this acquired lease')
    if state.get('status') != 'held' or not state.get('held'):
        raise ValueError('Lease is already released or expired; refuse stale release')
    signal = receipt.with_name(lease_id + '.release.json')
    _owner_write(signal, {'lease_id': lease_id, 'release_token': token, 'requested_at_unix': time.time()})
    deadline = time.monotonic() + wait_seconds
    while True:
        current = json.loads(receipt.read_text(encoding='utf-8'))
        if current.get('status') == 'released' and not current.get('held'):
            result = {'ok': True, 'event': 'release_acknowledged', 'lease_id': lease_id,
                      'release_reason': current.get('release_reason'), 'lease_file': str(receipt)}
            print(json.dumps(result, ensure_ascii=False), flush=True)
            return 0
        if time.monotonic() >= deadline:
            print(json.dumps({'ok': False, 'event': 'release_signal_written_without_ack',
                              'lease_id': lease_id, 'lease_file': str(receipt)}, ensure_ascii=False), flush=True)
            return 76
        time.sleep(0.05)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='mode', required=True)
    run = commands.add_parser('run', help='Run one command while owning a shared resource')
    run.add_argument('--resource', required=True)
    run.add_argument('--lock-root')
    run.add_argument('--timeout', type=float, default=1800)
    run.add_argument('command', nargs=argparse.REMAINDER)
    hold = commands.add_parser('hold', help='Hold a browser/resource lock across separate tool calls')
    hold.add_argument('--resource', required=True)
    hold.add_argument('--lock-root', required=True)
    hold.add_argument('--owner-id', required=True)
    hold.add_argument('--timeout', type=float, default=0)
    hold.add_argument('--ttl', type=float, default=600)
    release = commands.add_parser('release', help='Release the exact acquired helper lease')
    release.add_argument('--lease-file', required=True)
    release.add_argument('--lease-id', required=True)
    release.add_argument('--token', required=True)
    release.add_argument('--wait', type=float, default=5)
    args = parser.parse_args(argv)
    try:
        if args.mode == 'hold':
            return hold_lease(args.resource, args.lock_root, args.timeout, args.ttl, args.owner_id)
        if args.mode == 'release':
            return release_lease(args.lease_file, args.lease_id, args.token, args.wait)
        command = args.command[1:] if args.command[:1] == ['--'] else args.command
        if not command:
            parser.error('Supply a command after --')
        with resource_lock(args.resource, args.lock_root, args.timeout):
            process = subprocess.Popen(command)
            try:
                return process.wait()
            except BaseException:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill(); process.wait()
                raise
    except LockTimeoutError as exc:
        print(str(exc), file=sys.stderr)
        return 75
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
