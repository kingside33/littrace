"""kbapp.core.secrets：由职责边界组织的知识库能力。"""
from __future__ import annotations
import base64
import ctypes
import os

def protect(text, decrypt=False):
    if not text:
        return ''
    if os.name != 'nt':
        raise RuntimeError('密钥保护仅支持当前 Windows 用户，其他平台需配置密钥存储适配器')

    class Blob(ctypes.Structure):
        _fields_ = [('size', ctypes.c_ulong), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    raw = base64.b64decode(text) if decrypt else text.encode('utf-8')
    buf = ctypes.create_string_buffer(raw)
    src = Blob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    dst = Blob()
    crypt = ctypes.windll.crypt32
    ok = crypt.CryptUnprotectData(ctypes.byref(src), None, None, None, None, 1, ctypes.byref(dst)) if decrypt else crypt.CryptProtectData(ctypes.byref(src), 'LiteratureWorkbench', None, None, None, 1, ctypes.byref(dst))
    if not ok:
        raise RuntimeError('Windows 密钥保护失败')
    try:
        result = ctypes.string_at(dst.data, dst.size)
        return result.decode('utf-8') if decrypt else base64.b64encode(result).decode('ascii')
    finally:
        ctypes.windll.kernel32.LocalFree(dst.data)
