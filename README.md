<div align="center" size="15px">

# pyzstd

Python bindings to Zstandard (zstd) compression library

[![GitHub build status](https://img.shields.io/github/actions/workflow/status/rogdham/pyzstd/build.yml?branch=master)](https://github.com/rogdham/pyzstd/actions?query=branch:master)
[![Release on PyPI](https://img.shields.io/pypi/v/pyzstd)](https://pypi.org/project/pyzstd/)
[![BSD-3-Clause License](https://img.shields.io/pypi/l/pyzstd)](https://github.com/Rogdham/pyzstd/blob/master/LICENSE.txt)

---

[📖 Documentation][doc]&nbsp;&nbsp;&nbsp;|&nbsp;&nbsp;&nbsp;[📃 Changelog](./CHANGELOG.md)

</div>

---

The `pyzstd` module provides Python support for [Zstandard](http://www.zstd.net), using
an API style similar to the `bz2`, `lzma`, and `zlib` modules.

> [!WARNING]
>
> Zstandard is now natively supported in Python’s standard library via the
> [`compression.zstd` module][compression.zstd]. For older Python versions, use the
> [`backports.zstd` library][backports.zstd] as a fallback.
>
> We recommend new projects to use the standard library, and existing ones to consider
> migrating.
>
> `pyzstd` internally uses `compression.zstd` since version 0.19.0.
>
> See [`pyzstd`'s documentation][doc] for details and a migration guide.

[doc]: https://pyzstd.readthedocs.io/
[compression.zstd]: https://docs.python.org/3.14/library/compression.zstd.html
[backports.zstd]: https://github.com/Rogdham/backports.zstd
