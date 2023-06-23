Introduction
------------

Pyzstd module provides classes and functions for compressing and decompressing data, using Facebook's `Zstandard <http://www.zstd.net>`_ (or zstd as short name) algorithm.

The API style is similar to Python's bz2/lzma/zlib modules.

* Includes zstd v1.5.5 source code
* Can also dynamically link to zstd library provided by system, see `this note <https://pyzstd.readthedocs.io/en/latest/#build-pyzstd>`_.
* Has a CFFI implementation that can work with PyPy
* ``ZstdFile`` class has C language level performance
* Supports `Zstandard Seekable Format <https://github.com/facebook/zstd/blob/dev/contrib/seekable_format/zstd_seekable_compression_format.md>`__
* Has a command line interface: ``python -m pyzstd --help``

Links
-----------

Documentation: https://pyzstd.readthedocs.io/en/latest

GitHub: https://github.com/animalize/pyzstd


Release note
------------
**0.15.9  (Jun 24, 2023)**

ZstdFile class related changes:

#. Add `SeekableZstdFile <https://pyzstd.readthedocs.io/en/latest/#SeekableZstdFile>`_ class, it's a subclass of ``ZstdFile``, supports `Zstandard Seekable Format <https://github.com/facebook/zstd/blob/dev/contrib/seekable_format/zstd_seekable_compression_format.md>`__.

#. Add *mode* argument to ``ZstdFile.flush()`` method, now it can flush a zstd frame.

#. Add *read_size* and *write_size* arguments to ``ZstdFile.__init__()`` method, can work with Network File Systems better.

#. Optimize ``ZstdFile`` performance to C language level.

**0.15.7  (Apr 21, 2023)**

ZstdDict class changes:

#. Fix these advanced compression parameters may be ignored when loading a dictionary: ``windowLog``, ``hashLog``, ``chainLog``, ``searchLog``, ``minMatch``, ``targetLength``, ``strategy``, ``enableLongDistanceMatching``, ``ldmHashLog``, ``ldmMinMatch``, ``ldmBucketSizeLog``, ``ldmHashRateLog``, and some non-public parameters.

#. When compressing, load undigested dictionary instead of digested dictionary by default. Loading again an undigested is slower, see `differences <https://pyzstd.readthedocs.io/en/latest/#ZstdDict.as_digested_dict>`_.

#. Add `.as_prefix <https://pyzstd.readthedocs.io/en/latest/#ZstdDict.as_prefix>`_ attribute. Can use zstd as a `patching engine <https://pyzstd.readthedocs.io/en/latest/#patching-engine>`_.

**0.15.6  (Apr 5, 2023)**

Upgrade zstd source code from v1.5.4 to `v1.5.5 <https://github.com/facebook/zstd/releases/tag/v1.5.5>`_.

**0.15.4  (Feb 24, 2023)**

#. Upgrade zstd source code from v1.5.2 to `v1.5.4 <https://github.com/facebook/zstd/releases/tag/v1.5.4>`_. v1.5.3 is a non-public release.

#. Support ``pyproject.toml`` build mechanism (PEP-517). Note that specifying build options in old way may be invalid, see `build commands <https://pyzstd.readthedocs.io/en/latest/#build-pyzstd>`_.

#. Support "multi-phase initialization" (PEP-489) on CPython 3.11+, can work with CPython sub-interpreters in the future. Currently this build option is disabled by default.

#. Add a command line interface (CLI).

**0.15.3  (Aug 3, 2022)**

Fix ``ZstdError`` object can't be pickled.

**0.15.2  (Jan 22, 2022)**

Upgrade zstd source code from v1.5.1 to `v1.5.2 <https://github.com/facebook/zstd/releases/tag/v1.5.2>`_.

**0.15.1  (Dec 25, 2021)**

#. Upgrade zstd source code from v1.5.0 to `v1.5.1 <https://github.com/facebook/zstd/releases/tag/v1.5.1>`_.

#. Fix ``ZstdFile.write()`` / ``train_dict()`` / ``finalize_dict()`` may use wrong length for some buffer protocol objects, see `this issue <https://github.com/animalize/pyzstd/issues/4>`_.

#. Two behavior changes:

    * Setting ``CParameter.nbWorkers`` to ``1`` now means "1-thread multi-threaded mode", rather than "single-threaded mode".

    * If the underlying zstd library doesn't support multi-threaded compression, no longer automatically fallback to "single-threaded mode", now raise a ``ZstdError`` exception.

#. Add a module level variable `zstd_support_multithread <https://pyzstd.readthedocs.io/en/latest/#zstd_support_multithread>`_.

#. Add a setup.py option ``--avx2``, see `build options <https://pyzstd.readthedocs.io/en/latest/#build-pyzstd>`_.

**0.15.0  (May 18, 2021)**

#. Upgrade zstd source code from v1.4.9 to `v1.5.0 <https://github.com/facebook/zstd/releases/tag/v1.5.0>`_.

#. Some improvements, no API changes.

**0.14.4  (Mar 24, 2021)**

#. Add a CFFI implementation that can work with PyPy.

#. Allow dynamically link to zstd library.

**0.14.3  (Mar 4, 2021)**

Upgrade zstd source code from v1.4.8 to `v1.4.9 <https://github.com/facebook/zstd/releases/tag/v1.4.9>`_.

**0.14.2  (Feb 24, 2021)**

#. Add two convenient functions: `compress_stream() <https://pyzstd.readthedocs.io/en/latest/#compress_stream>`_, `decompress_stream() <https://pyzstd.readthedocs.io/en/latest/#decompress_stream>`_.

#. Some improvements.

**0.14.1  (Dec 19, 2020)**

#. Upgrade zstd source code from v1.4.5 to `v1.4.8 <https://github.com/facebook/zstd/releases/tag/v1.4.8>`_.

    * v1.4.6 is a non-public release for Linux kernel.

    * v1.4.8 is a hotfix for `v1.4.7 <https://github.com/facebook/zstd/releases/tag/v1.4.7>`_.

#. Some improvements, no API changes.

**0.13.0  (Nov 7, 2020)**

#. ``ZstdDecompressor`` class: now it has the same API and behavior as BZ2Decompressor / LZMADecompressor classes in Python standard library, it stops after a frame is decompressed.

#. Add an ``EndlessZstdDecompressor`` class, it accepts multiple concatenated frames. It is renamed from previous ``ZstdDecompressor`` class, but ``.at_frame_edge`` is ``True`` when both the input and output streams are at a frame edge.

#. Rename ``zstd_open()`` function to ``open()``, consistent with Python standard library.

#. ``decompress()`` function:

    * ~9% faster when: there is one frame, and the decompressed size was recorded in frame header.

    * raises ZstdError when input **or** output data is not at a frame edge. Previously, it only raise for output data is not at a frame edge.

**0.12.5  (Oct 12, 2020)**

No longer use `Argument Clinic <https://docs.python.org/3/howto/clinic.html>`_, now supports Python 3.5+, previously 3.7+.

**0.12.4  (Oct 7, 2020)**

It seems the API is stable.

**0.2.4  (Sep 2, 2020)**

The first version upload to PyPI.

Includes zstd `v1.4.5 <https://github.com/facebook/zstd/releases/tag/v1.4.5>`_ source code.