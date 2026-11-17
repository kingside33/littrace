"""kbapp.core.win_runtime：由职责边界组织的知识库能力。"""
import ctypes
from ctypes import wintypes as W
import socket

def kernel():
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    api.OpenProcess.restype = W.HANDLE
    api.CloseHandle.argtypes = [W.HANDLE]
    api.GetProcessTimes.argtypes = [W.HANDLE] + [ctypes.POINTER(W.FILETIME)] * 4
    api.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, ctypes.POINTER(W.DWORD)]
    api.TerminateProcess.argtypes = [W.HANDLE, W.UINT]
    api.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
    api.GetExitCodeProcess.argtypes = [W.HANDLE, ctypes.POINTER(W.DWORD)]
    return api

def created_ticks(api, handle):
    values = [W.FILETIME() for _ in range(4)]
    if not api.GetProcessTimes(handle, *[ctypes.byref(x) for x in values]):
        raise ctypes.WinError(ctypes.get_last_error())
    return values[0].dwHighDateTime << 32 | values[0].dwLowDateTime

def process_info(pid):
    api = kernel()
    handle = api.OpenProcess(4096, False, int(pid))
    if not handle:
        error = ctypes.get_last_error()
        if error == 87:
            return None
        raise ctypes.WinError(error)
    try:
        exit_code = W.DWORD()
        if not api.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            raise ctypes.WinError(ctypes.get_last_error())
        if exit_code.value != 259:
            return None
        buf = ctypes.create_unicode_buffer(32768)
        size = W.DWORD(len(buf))
        if not api.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        return {'pid': int(pid), 'executable': buf.value, 'created_ticks': created_ticks(api, handle)}
    finally:
        api.CloseHandle(handle)

def listener_pid(port):

    class Row(ctypes.Structure):
        _fields_ = [(name, W.DWORD) for name in ('state', 'local_addr', 'local_port', 'remote_addr', 'remote_port', 'pid')]
    call = ctypes.WinDLL('iphlpapi').GetExtendedTcpTable
    call.argtypes = [W.LPVOID, ctypes.POINTER(W.DWORD), W.BOOL, W.ULONG, ctypes.c_int, W.ULONG]
    call.restype = W.DWORD
    size = W.DWORD()
    code = call(None, ctypes.byref(size), False, 2, 3, 0)
    if code not in {0, 122}:
        raise ctypes.WinError(code)
    for _ in range(3):
        buf = ctypes.create_string_buffer(size.value)
        code = call(buf, ctypes.byref(size), False, 2, 3, 0)
        if code == 122:
            continue
        if code:
            raise ctypes.WinError(code)
        count = W.DWORD.from_buffer(buf).value
        for row in (Row * count).from_buffer(buf, ctypes.sizeof(W.DWORD)):
            if socket.ntohs(row.local_port & 65535) == port:
                return int(row.pid)
        return None
    raise ValueError('监听端口状态持续变化，请稍后重试')

def terminate_owned(pid, ticks):
    api = kernel()
    handle = api.OpenProcess(1052673, False, int(pid))
    if not handle:
        if ctypes.get_last_error() == 87:
            return
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if created_ticks(api, handle) != int(ticks):
            raise ValueError('进程创建时间已变化，未停止该进程')
        exit_code = W.DWORD()
        if not api.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            raise ctypes.WinError(ctypes.get_last_error())
        if exit_code.value != 259:
            return
        if not api.TerminateProcess(handle, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        if api.WaitForSingleObject(handle, 10000) != 0:
            raise ValueError('模型仍在退出，已保留进程登记，请稍后重试')
    finally:
        api.CloseHandle(handle)
