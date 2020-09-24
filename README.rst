Introduction
------------

``pyzstd`` module provides classes and functions for compressing and decompressing data, using Facebook's `Zstandard <http://www.zstd.net>`_ (or zstd as short name) algorithm.

The interface is similar to Python's bz2/lzma module.

* Binds zstd v1.4.5 source code
* Due to the use of `Argument Clinic <https://docs.python.org/3/howto/clinic.html>`_, only supports Python 3.7+


Quick links
-----------

Documentation: https://pyzstd.readthedocs.io/en/latest/

GitHub: https://github.com/animalize/pyzstd


Installtion
-----------

On Windows/macOS/Linux: ``pip3 install pyzstd``

On Linux, should install ``python3-dev`` package first.


Release note
------------
**0.10.0  (Sep 24, 2020)**

Add ``RichMemZstdCompressor`` class.

Remove ``ZstdCompressor.rich_mem_compress`` method.

**0.9.4  (Sep 24, 2020)**

``ZstdCompressor.__init__()`` method no longer has a *rich_mem* argument.

Add a ``ZstdCompressor.rich_mem_compress()`` method.

**0.9.2  (Sep 18, 2020)**

Binds zstd v1.4.5 source code.