<div align="center" size="15px">

# pyzstd

Python bindings to Zstandard (zstd) compression library

[![GitHub build status](https://img.shields.io/github/actions/workflow/status/rogdham/pyzstd/build.yml?branch=master)](https://github.com/rogdham/pyzstd/actions?query=branch:master)
[![Release on PyPI](https://img.shields.io/pypi/v/pyzstd)](https://pypi.org/project/pyzstd/)
[![BSD-3-Clause License](https://img.shields.io/pypi/l/pyzstd)](https://github.com/Rogdham/pyzstd/blob/master/LICENSE.txt)

---

[📖 Documentation](https://pyzstd.readthedocs.io/)&nbsp;&nbsp;&nbsp;|&nbsp;&nbsp;&nbsp;[📃 Changelog](./CHANGELOG.md)

</div>

---

Pyzstd module provides classes and functions for compressing and decompressing data, using Facebook's [Zstandard](http://www.zstd.net) (or zstd as short name) algorithm.

The API style is similar to Python's bz2/lzma/zlib modules.

- Includes zstd v1.5.6 source code
- Can also dynamically link to zstd library provided by system, see [this note](https://pyzstd.readthedocs.io/#build-pyzstd).
- Has a CFFI implementation that can work with PyPy
- Support sub-interpreter on CPython 3.12+
- `ZstdFile` class has C language level performance
- Supports [Zstandard Seekable Format](https://github.com/facebook/zstd/blob/dev/contrib/seekable_format/zstd_seekable_compression_format.md)
- Has a command line interface: `python -m pyzstd --help`
