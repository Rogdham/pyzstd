# Changelog

All notable changes to this project will be documented in this file.

## 0.17.0 (May 10, 2025)

- Upgrade zstd source code from v1.5.6 to [v1.5.7](https://github.com/facebook/zstd/releases/tag/v1.5.7)
- Raise an exception when attempting to decompress empty data
- Add `ZstdFile.name` property
- Deprecate `(de)compress_stream` functions
- Use a leading `_` for private objects
- Build wheels for Windows ARM64
- Support for PyPy 3.11

## 0.16.2 (October 10, 2024)

- Build wheels for Python 3.13
- Deprecate support for Python version before 3.9 and stop building wheels for them

## 0.16.1 (August 4, 2024)

- Compatibility with Python 3.13

## 0.16.0 (May 20, 2024)

- Upgrade zstd source code from v1.5.5 to [v1.5.6](https://github.com/facebook/zstd/releases/tag/v1.5.6)
- Fix pyzstd_pep517 parameter name in `get_requires_for_build_wheel`
- Deprecate support for Python version before 3.8 and stop building wheels for them
- Minor fixes in type hints
- Refactor README & CHANGELOG files

## 0.15.10 (Mar 24, 2024)

- Fix `SeekableZstdFile` class can't open new file in appending mode.
- Support sub-interpreter on CPython 3.12+, can utilize [per-interpreter GIL](https://docs.python.org/3.12/whatsnew/3.12.html#pep-684-a-per-interpreter-gil).
- On CPython(3.5~3.12)+Linux, use another output buffer code that can utilize the `mremap` mechanism.
- Change repository URL and maintainer following the deletion of the GitHub account of the original author, Ma Lin (animalize). See [#1](https://github.com/Rogdham/pyzstd/issues/1).

## 0.15.9 (Jun 24, 2023)

ZstdFile class related changes:

- Add [`SeekableZstdFile`](https://pyzstd.readthedocs.io/#SeekableZstdFile) class, it's a subclass of `ZstdFile`, supports [Zstandard Seekable Format](https://github.com/facebook/zstd/blob/dev/contrib/seekable_format/zstd_seekable_compression_format.md).
- Add _mode_ argument to `ZstdFile.flush()` method, now it can flush a zstd frame.
- Add _read_size_ and _write_size_ arguments to `ZstdFile.__init__()` method, can work with Network File Systems better.
- Optimize `ZstdFile` performance to C language level.

## 0.15.7 (Apr 21, 2023)

ZstdDict class changes:

- Fix these advanced compression parameters may be ignored when loading a dictionary: `windowLog`, `hashLog`, `chainLog`, `searchLog`, `minMatch`, `targetLength`, `strategy`, `enableLongDistanceMatching`, `ldmHashLog`, `ldmMinMatch`, `ldmBucketSizeLog`, `ldmHashRateLog`, and some non-public parameters.
- When compressing, load undigested dictionary instead of digested dictionary by default. Loading again an undigested is slower, see [differences](https://pyzstd.readthedocs.io/#ZstdDict.as_digested_dict).
- Add [`.as_prefix`](https://pyzstd.readthedocs.io/#ZstdDict.as_prefix) attribute. Can use zstd as a [patching engine](https://pyzstd.readthedocs.io/#patching-engine).

## 0.15.6 (Apr 5, 2023)

- Upgrade zstd source code from v1.5.4 to [v1.5.5](https://github.com/facebook/zstd/releases/tag/v1.5.5).

## 0.15.4 (Feb 24, 2023)

- Upgrade zstd source code from v1.5.2 to [v1.5.4](https://github.com/facebook/zstd/releases/tag/v1.5.4). v1.5.3 is a non-public release.
- Support `pyproject.toml` build mechanism (PEP-517). Note that specifying build options in old way may be invalid, see [build commands](https://pyzstd.readthedocs.io/#build-pyzstd).
- Support "multi-phase initialization" (PEP-489) on CPython 3.11+, can work with CPython sub-interpreters in the future. Currently this build option is disabled by default.
- Add a command line interface (CLI).

## 0.15.3 (Aug 3, 2022)

- Fix `ZstdError` object can't be pickled.

## 0.15.2 (Jan 22, 2022)

- Upgrade zstd source code from v1.5.1 to [v1.5.2](https://github.com/facebook/zstd/releases/tag/v1.5.2).

## 0.15.1 (Dec 25, 2021)

- Upgrade zstd source code from v1.5.0 to [v1.5.1](https://github.com/facebook/zstd/releases/tag/v1.5.1).
- Fix `ZstdFile.write()` / `train_dict()` / `finalize_dict()` may use wrong length for some buffer protocol objects.
- Two behavior changes:
  - Setting `CParameter.nbWorkers` to `1` now means "1-thread multi-threaded mode", rather than "single-threaded mode".
  - If the underlying zstd library doesn't support multi-threaded compression, no longer automatically fallback to "single-threaded mode", now raise a `ZstdError` exception.
- Add a module level variable [`zstd_support_multithread`](https://pyzstd.readthedocs.io/#zstd_support_multithread).
- Add a setup.py option `--avx2`, see [build options](https://pyzstd.readthedocs.io/#build-pyzstd).

## 0.15.0 (May 18, 2021)

- Upgrade zstd source code from v1.4.9 to [v1.5.0](https://github.com/facebook/zstd/releases/tag/v1.5.0).
- Some improvements, no API changes.

## 0.14.4 (Mar 24, 2021)

- Add a CFFI implementation that can work with PyPy.
- Allow dynamically link to zstd library.

## 0.14.3 (Mar 4, 2021)

- Upgrade zstd source code from v1.4.8 to [v1.4.9](https://github.com/facebook/zstd/releases/tag/v1.4.9).

## 0.14.2 (Feb 24, 2021)

- Add two convenient functions: [`compress_stream()`](https://pyzstd.readthedocs.io/#compress_stream) and [`decompress_stream()`](https://pyzstd.readthedocs.io/#decompress_stream).
- Some improvements.

## 0.14.1 (Dec 19, 2020)

- Upgrade zstd source code from v1.4.5 to [v1.4.8](https://github.com/facebook/zstd/releases/tag/v1.4.8).
  - v1.4.6 is a non-public release for Linux kernel.
  - v1.4.8 is a hotfix for [v1.4.7](https://github.com/facebook/zstd/releases/tag/v1.4.7).
- Some improvements, no API changes.

## 0.13.0 (Nov 7, 2020)

- `ZstdDecompressor` class: now it has the same API and behavior as BZ2Decompressor / LZMADecompressor classes in Python standard library, it stops after a frame is decompressed.
- Add an `EndlessZstdDecompressor` class, it accepts multiple concatenated frames. It is renamed from previous `ZstdDecompressor` class, but `.at_frame_edge` is `True` when both the input and output streams are at a frame edge.
- Rename `zstd_open()` function to `open()`, consistent with Python standard library.
- `decompress()` function:
  - ~9% faster when: there is one frame, and the decompressed size was recorded in frame header.
  - raises ZstdError when input **or** output data is not at a frame edge. Previously, it only raise for output data is not at a frame edge.

## 0.12.5 (Oct 12, 2020)

- No longer use [Argument Clinic](https://docs.python.org/3/howto/clinic.html), now supports Python 3.5+, previously 3.7+.

## 0.12.4 (Oct 7, 2020)

- It seems the API is stable.

## 0.2.4 (Sep 2, 2020)

- The first version upload to PyPI.
- Includes zstd [v1.4.5](https://github.com/facebook/zstd/releases/tag/v1.4.5) source code.
