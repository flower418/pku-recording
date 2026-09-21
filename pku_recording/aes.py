"""AES-128-CBC 解密（自动选择可用后端：cryptography / pycryptodome / openssl）。"""

import shutil
import subprocess

_BACKEND = None
_ERROR = None

try:  # 首选：cryptography（跨平台、性能好）
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes  # noqa: F401

    _BACKEND = "cryptography"
except ImportError:
    try:  # 次选：pycryptodome
        from Crypto.Cipher import AES as _PyCryptoAES  # noqa: F401

        _BACKEND = "pycryptodome"
    except ImportError:
        if shutil.which("openssl"):  # 兜底：openssl 命令行
            _BACKEND = "openssl"
        else:
            _ERROR = (
                "没有可用的 AES 解密后端。请任选其一：\n"
                "  - pip install cryptography\n"
                "  - pip install pycryptodome\n"
                "  - 安装 openssl 命令行"
            )


def backend_name():
    return _BACKEND


def _unpad_pkcs7(data):
    if not data:
        return data
    pad = data[-1]
    if 1 <= pad <= 16 and len(data) >= pad:
        return data[:-pad]
    return data


def _decrypt_cryptography(data, key, iv):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    return dec.update(data) + dec.finalize()


def _decrypt_pycryptodome(data, key, iv):
    from Crypto.Cipher import AES

    return AES.new(key, AES.MODE_CBC, iv).decrypt(data)


def _decrypt_openssl(data, key, iv):
    p = subprocess.run(
        ["openssl", "enc", "-aes-128-cbc", "-d", "-nopad", "-K", key.hex(), "-iv", iv.hex()],
        input=data,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if p.returncode != 0:
        raise RuntimeError(f"openssl 解密失败: {p.stderr.decode('utf-8', 'ignore')[:200]}")
    return p.stdout


def decrypt_aes128_cbc(data, key, iv):
    """解密一段 AES-128-CBC 数据并去掉 PKCS#7 填充。"""
    if _BACKEND is None:
        raise RuntimeError(_ERROR)
    if _BACKEND == "cryptography":
        raw = _decrypt_cryptography(data, key, iv)
    elif _BACKEND == "pycryptodome":
        raw = _decrypt_pycryptodome(data, key, iv)
    else:
        raw = _decrypt_openssl(data, key, iv)
    return _unpad_pkcs7(raw)
