"""Process lifetime directory lock for POSIX and Windows."""
from contextlib import contextmanager


@contextmanager
def directory_lock(path):
    handle = path.open('a+b')
    try:
        if handle.seek(0,2) == 0:
            handle.write(b'0');handle.flush()
        handle.seek(0)
        try:
            import os
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            raise ValueError('该 data-dir 已被另一个服务使用') from None
        yield
    finally:
        handle.close()
