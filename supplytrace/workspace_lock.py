"""OS-released process lease: one service per SQLite workspace, including after crashes."""
import os


class WorkspaceLock:
    def __init__(self,path):
        self.path,self.file=path,None

    def acquire(self):
        self.file=self.path.open('a+b')
        self.file.seek(0,2)
        if not self.file.tell():
            self.file.write(b'0');self.file.flush()
        self.file.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.file.close();self.file=None
            raise RuntimeError('Workspace already served by another process') from None

    def release(self):
        if self.file:
            self.file.close();self.file=None
