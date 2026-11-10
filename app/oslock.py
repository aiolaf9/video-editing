"""Kunci berkas eksklusif antar-proses yang berjalan di Linux/macOS (flock) dan Windows (LockFileEx).

Di Windows yang dikunci adalah 1 byte jauh di luar ukuran berkas mana pun (offset 2^62), sehingga kunci tidak
pernah menghalangi baca/tulis isi berkas (kunci byte-range Windows bersifat wajib), tetapi tetap eksklusif antar
handle/proses — setara flock(LOCK_EX) pada berkas yang sama. Kunci dilepas otomatis saat handle ditutup."""
import os
import time

if os.name == "nt":
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class _Overlapped(ctypes.Structure):
        _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p),
                    ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD), ("hEvent", wintypes.HANDLE)]

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.LockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                ctypes.POINTER(_Overlapped)]
    _k32.UnlockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.POINTER(_Overlapped)]
    _EXCLUSIVE, _FAIL_NOW = 0x2, 0x1
    _HIGH = 0x40000000                       # offset 2^62: di luar isi berkas

    def _ov():
        ov = _Overlapped()
        ov.OffsetHigh = _HIGH
        return ov

    def try_lock(fh) -> bool:
        handle = msvcrt.get_osfhandle(fh.fileno())
        return bool(_k32.LockFileEx(handle, _EXCLUSIVE | _FAIL_NOW, 0, 1, 0, ctypes.byref(_ov())))

    def lock(fh) -> None:
        while not try_lock(fh):
            time.sleep(0.05)

    def unlock(fh) -> None:
        _k32.UnlockFileEx(msvcrt.get_osfhandle(fh.fileno()), 0, 1, 0, ctypes.byref(_ov()))
else:
    import fcntl

    def try_lock(fh) -> bool:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False

    def lock(fh) -> None:
        fcntl.flock(fh, fcntl.LOCK_EX)

    def unlock(fh) -> None:
        fcntl.flock(fh, fcntl.LOCK_UN)
