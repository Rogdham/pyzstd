
try:
    from .c.c_pyzstd import *
except ImportError:
    try:
        from .cffi.cffi_pyzstd import *
    except ImportError:
        raise ImportError('Neither C version nor cffi version can be imported.')

__version__ = '0.14.3'

__doc__ = '''\
Python bindings to Zstandard (zstd) compression library, the API is similar to
Python's bz2/lzma/zlib module.

Documentation: https://pyzstd.readthedocs.io
GitHub: https://github.com/animalize/pyzstd
PyPI: https://pypi.org/project/pyzstd'''