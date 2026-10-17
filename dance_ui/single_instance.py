"""Session-scoped Windows instance guard, released even after a process crash."""
import ctypes

from .steamvr_startup import MUTEX_NAME


class Instance:
    def __init__(self, kernel, handle):
        self.kernel, self.handle = kernel, handle

    def close(self):
        if self.handle is not None:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def claim_instance():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.restype = ctypes.c_bool
    handle = kernel.CreateMutexW(None, False, MUTEX_NAME)
    error = ctypes.get_last_error()
    if not handle:
        raise ctypes.WinError(error)
    if error == 183:  # ERROR_ALREADY_EXISTS
        kernel.CloseHandle(handle)
        return None
    return Instance(kernel, handle)
