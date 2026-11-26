"""kbapp.core.secrets：由职责边界组织的知识库能力。"""
from __future__ import annotations
import base64
import ctypes
import os


class SecretProtectionUnavailable(RuntimeError):
    """本平台没有密钥保护器（非 Windows）。

    这是能力缺失，不是密文损坏：它每次调用都会失败，永远不会因为时间或数据
    变化而恢复。读侧必须把它与「密文解不开」分开——ADR 0041 靠它抛错来判定
    「本平台不能保存密钥」。
    """


class SecretDecryptError(RuntimeError):
    """密文存在但解不开：另一个 Windows 用户/机器写的，或已被破坏。"""


def protect(text, decrypt=False):
    if not text:
        return ''
    if os.name != 'nt':
        raise SecretProtectionUnavailable('密钥保护仅支持当前 Windows 用户，其他平台需配置密钥存储适配器')

    class Blob(ctypes.Structure):
        _fields_ = [('size', ctypes.c_ulong), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    raw = base64.b64decode(text) if decrypt else text.encode('utf-8')
    buf = ctypes.create_string_buffer(raw)
    src = Blob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    dst = Blob()
    crypt = ctypes.windll.crypt32
    ok = crypt.CryptUnprotectData(ctypes.byref(src), None, None, None, None, 1, ctypes.byref(dst)) if decrypt else crypt.CryptProtectData(ctypes.byref(src), 'LiteratureWorkbench', None, None, None, 1, ctypes.byref(dst))
    if not ok:
        # 解密失败是「这块密文不属于当前用户/已损坏」，与「本平台没有保护器」
        # 是两回事：前者可恢复（重新填写），后者永远不可恢复。
        raise SecretDecryptError('Windows 密钥保护失败')
    try:
        result = ctypes.string_at(dst.data, dst.size)
        return result.decode('utf-8') if decrypt else base64.b64encode(result).decode('ascii')
    finally:
        ctypes.windll.kernel32.LocalFree(dst.data)
