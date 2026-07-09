import ctypes
import re
import sys
from ctypes import wintypes

from Crypto.Cipher import AES


PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
TH32CS_SNAPPROCESS = 0x00000002
MEM_COMMIT = 0x1000
READABLE_PROTECT = {0x02, 0x04, 0x20, 0x40}


kernel32 = ctypes.windll.kernel32

OpenProcess = kernel32.OpenProcess
OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
OpenProcess.restype = wintypes.HANDLE

CloseHandle = kernel32.CloseHandle

ReadProcessMemory = kernel32.ReadProcessMemory
ReadProcessMemory.argtypes = [
    wintypes.HANDLE,
    wintypes.LPCVOID,
    wintypes.LPVOID,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]


VirtualQueryEx = kernel32.VirtualQueryEx
VirtualQueryEx.argtypes = [
    wintypes.HANDLE,
    wintypes.LPCVOID,
    ctypes.POINTER(MEMORY_BASIC_INFORMATION),
    ctypes.c_size_t,
]


def get_pid_by_name(name):
    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(wintypes.ULONG)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_char * 260),
        ]

    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    pe = PROCESSENTRY32()
    pe.dwSize = ctypes.sizeof(pe)

    if not kernel32.Process32First(snapshot, ctypes.byref(pe)):
        CloseHandle(snapshot)
        return None

    while True:
        exe = pe.szExeFile.decode(errors="ignore").lower()
        if name.lower() in exe:
            CloseHandle(snapshot)
            return pe.th32ProcessID
        if not kernel32.Process32Next(snapshot, ctypes.byref(pe)):
            break

    CloseHandle(snapshot)
    return None


class MemoryFinder:
    CHUNK_SIZE = 1024 * 1024

    def __init__(self, pid: int, token_regex, token_max_length: int):
        self.pid = pid
        self.token_regex = token_regex
        self.token_max_length = token_max_length
        self.max_address = 0x7FFFFFFFFFFF if sys.maxsize > 2**32 else 0x7FFFFFFF

    def __iter__(self):
        handle = OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, self.pid)
        if not handle:
            raise OSError("OpenProcess failed (try admin)")

        seen = set()
        address = 0
        mbi = MEMORY_BASIC_INFORMATION()
        overlap = max(self.token_max_length - 1, 0)

        try:
            while address < self.max_address:
                if not VirtualQueryEx(
                    handle,
                    ctypes.c_void_p(address),
                    ctypes.byref(mbi),
                    ctypes.sizeof(mbi),
                ):
                    address += 0x1000
                    continue

                base = int(mbi.BaseAddress or 0)
                size = int(mbi.RegionSize or 0)

                if size == 0:
                    address += 0x1000
                    continue

                if mbi.State == MEM_COMMIT and mbi.Protect in READABLE_PROTECT:
                    yield from self._iter_region(handle, base, size, overlap, seen)

                address = base + size
        finally:
            CloseHandle(handle)

    def _iter_region(self, handle, base: int, size: int, overlap: int, seen):
        cursor = base
        end = base + size

        while cursor < end:
            read_size = min(self.CHUNK_SIZE, end - cursor)
            buffer = ctypes.create_string_buffer(read_size)
            read = ctypes.c_size_t()

            if ReadProcessMemory(handle, ctypes.c_void_p(cursor), buffer, read_size, ctypes.byref(read)):
                data = buffer.raw[: read.value]
                for match in self.token_regex.finditer(data):
                    token = match.group().decode("utf-8", errors="ignore")
                    if token not in seen:
                        seen.add(token)
                        yield token

            if read_size == end - cursor:
                break

            step = max(read_size - overlap, 1)
            cursor += step


def _read_db_header(db_path: str) -> bytes:
    with open(db_path, "rb") as file:
        return file.read(16)


def _is_valid_key(key_bytes: bytes, encrypted_header: bytes) -> bool:
    if len(encrypted_header) != 16 or len(key_bytes) != 16:
        return False

    try:
        plain = AES.new(key_bytes, AES.MODE_ECB).decrypt(encrypted_header)
    except Exception:
        return False

    return plain == b"SQLite format 3\x00"

def retrieve_db_key(db_path: str, process_name: str = "AliWorkbench.exe") -> bytes:
    pid = get_pid_by_name(process_name)
    if not pid:
        raise ValueError("Cannot find process")

    encrypted_header = _read_db_header(db_path)
    if len(encrypted_header) != 16:
        raise EOFError("Database header is less than 16 bytes")

    finder = MemoryFinder(
        pid=pid,
        token_regex=re.compile(b"[0-9a-f]{16}"),
        token_max_length=16,
    )

    for key in finder:
        key_bytes = key.encode(encoding="utf-8")
        if _is_valid_key(key_bytes, encrypted_header):
            return key_bytes

    raise ValueError("No valid key found")


if __name__ == "__main__":
    print(retrieve_db_key("im.sqlite"))
