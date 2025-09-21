```{toctree}
:hidden:
:maxdepth: 2

Home <self>
Migration to stdlib <stdlib.md>
Module reference <pyzstd>
Deprecations <deprecated.md>
```

# pyzstd

The `pyzstd` library was created by Ma Lin in 2020 to provide Python support for [Zstandard](http://www.zstd.net), using an API style similar to the `bz2`, `lzma`, and `zlib` modules.

In 2025, an effort led by [Emma Smith](https://github.com/emmatyping) (now a CPython core developer) resulted in [PEP 784][] and the inclusion of the [`compression.zstd` module][compression.zstd] in the Python 3.14 standard library. The implementation was adapted from `pyzstd`, with its maintainer [Rogdham](https://github.com/rogdham) contributing directly to the effort. Rogdham also developed the [`backports.zstd` library][backports.zstd] which backports the `compression.zstd` APIs to older Python versions.

Recommendations:

- **New projects**: use the standard library [`compression.zstd` module][compression.zstd], with [`backports.zstd`][backports.zstd] as a fallback for older Python versions.
- **Existing projects**: consider [migrating to the standard library implementation](./stdlib.md).

In the meanwhile, [documentation for the `pyzstd` module is available here](./pyzstd.rst).

[PEP 784]: https://peps.python.org/pep-0784/
[compression.zstd]: https://docs.python.org/3.14/library/compression.zstd.html
[backports.zstd]: https://github.com/Rogdham/backports.zstd
