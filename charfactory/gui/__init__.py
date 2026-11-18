import sys

if sys.implementation.name == "cpython" and sys.version_info < (3, 12):
    # No Linux, o PySide 6.12 perde uma referência do None a cada chamada que retorna vazio e o
    # Python 3.11 aborta quando a contagem zera. Do 3.12 em diante o None já é imortal.
    import ctypes

    ctypes.c_ssize_t.from_address(id(None)).value += sys.maxsize // 2
