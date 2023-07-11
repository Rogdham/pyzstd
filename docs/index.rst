.. title:: pyzstd module

Introduction
------------

Pyzstd module provides classes and functions for compressing and decompressing data using Facebook's `Zstandard <http://www.zstd.net>`_ (or zstd as short name) algorithm.

The API style is similar to Python's bz2/lzma/zlib modules.

* Includes the latest zstd library source code
* Can also dynamically link to zstd library provided by system, see :ref:`this note<build_pyzstd>`.
* Has a CFFI implementation that can work with PyPy
* :py:class:`ZstdFile` class has C language level performance
* Supports `Zstandard Seekable Format <https://github.com/facebook/zstd/blob/dev/contrib/seekable_format/zstd_seekable_compression_format.md>`__
* Has a command line interface, ``python -m pyzstd --help``.

Links: `GitHub page <https://github.com/animalize/pyzstd>`_, `PyPI page <https://pypi.org/project/pyzstd>`_.

Features of zstd:

* Fast compression and decompression speed.
* If use :ref:`multi-threaded compression<mt_compression>`, the compression speed improves significantly.
* If use pre-trained :ref:`dictionary<zstd_dict>`, the compression ratio on small data (a few KiB) improves dramatically.
* :ref:`Frame and block<frame_block>` allow the use more flexible, suitable for many scenarios.
* Can be used as a :ref:`patching engine<patching_engine>`.

.. note::
    Two other zstd modules on PyPI:

    * `zstd <https://pypi.org/project/zstd/>`_, a very simple module.
    * `zstandard <https://pypi.org/project/zstandard/>`_, provides rich API.

Exception
---------

.. py:exception:: ZstdError

    This exception is raised when an error occurs when calling the underlying zstd library. Subclass of ``Exception``.


Simple compression/decompression
--------------------------------

    This section contains:

        * function :py:func:`compress`
        * function :py:func:`decompress`

    .. hint::
        If there are a big number of same type individual data, reuse these objects may eliminate the small overhead of creating context / setting parameters / loading dictionary.

        * :py:class:`ZstdCompressor`
        * :py:class:`RichMemZstdCompressor`


.. py:function:: compress(data, level_or_option=None, zstd_dict=None)

    Compress *data*, return the compressed data.

    Compressing ``b''`` will get an empty content frame (9 bytes or more).

    :py:func:`richmem_compress` function is faster in some cases.

    :param data: Data to be compressed.
    :type data: bytes-like object
    :param level_or_option: When it's an ``int`` object, it represents :ref:`compression level<compression_level>`. When it's a ``dict`` object, it contains :ref:`advanced compression parameters<CParameter>`. The default value ``None`` means to use zstd's default compression level/parameters.
    :type level_or_option: int or dict
    :param zstd_dict: Pre-trained dictionary for compression.
    :type zstd_dict: ZstdDict
    :return: Compressed data
    :rtype: bytes

.. sourcecode:: python

    # int compression level
    compressed_dat = compress(raw_dat, 10)

    # dict option, use 6 threads to compress, and append a 4-byte checksum.
    option = {CParameter.compressionLevel : 10,
              CParameter.nbWorkers : 6,
              CParameter.checksumFlag : 1}
    compressed_dat = compress(raw_dat, option)


.. py:function:: decompress(data, zstd_dict=None, option=None)

    Decompress *data*, return the decompressed data.

    Support multiple concatenated :ref:`frames<frame_block>`.

    :param data: Data to be decompressed.
    :type data: bytes-like object
    :param zstd_dict: Pre-trained dictionary for decompression.
    :type zstd_dict: ZstdDict
    :param option: A ``dict`` object that contains :py:ref:`advanced decompression parameters<DParameter>`. The default value ``None`` means to use zstd's default decompression parameters.
    :type option: dict
    :return: Decompressed data
    :rtype: bytes
    :raises ZstdError: If decompression fails.


Rich memory compression
-----------------------

    Compress data using :ref:`rich memory mode<rich_mem>`. This mode allocates more memory for output buffer, it's faster in some cases.

    This section contains:

        * function :py:func:`richmem_compress`
        * class :py:class:`RichMemZstdCompressor`, a reusable compressor.

.. py:function:: richmem_compress(data, level_or_option=None, zstd_dict=None)

    Use :ref:`rich memory mode<rich_mem>` to compress *data*. It's faster than :py:func:`compress` in some cases, but allocates more memory.

    The parameters are the same as :py:func:`compress` function.

    Compressing ``b''`` will get an empty content frame (9 bytes or more).


.. py:class:: RichMemZstdCompressor

    A reusable compressor using :ref:`rich memory mode<rich_mem>`. It can be reused for big number of same type individual data.

    Since it can only generates individual :ref:`frames<frame_block>`, it's not suitable for streaming compression, otherwise the compression ratio will be reduced, and some programs can't decompress multiple frames data. For streaming compression, see :ref:`this section<stream_compression>`.

    Thread-safe at method level.

    .. py:method:: __init__(self, level_or_option=None, zstd_dict=None)

        The parameters are the same as :py:meth:`ZstdCompressor.__init__` method.

    .. py:method:: compress(self, data)

        Compress *data* using :ref:`rich memory mode<rich_mem>`, return a single zstd :ref:`frame<frame_block>`.

        Compressing ``b''`` will get an empty content frame (9 bytes or more).

        :param data: Data to be compressed.
        :type data: bytes-like object
        :return: A single zstd frame.
        :rtype: bytes

    .. sourcecode:: python

        c = RichMemZstdCompressor()
        frame1 = c.compress(raw_dat1)
        frame2 = c.compress(raw_dat2)


.. _stream_compression:

Streaming compression
---------------------

    This section contains:

        * function :py:func:`compress_stream`, a fast and convenient function.
        * class :py:class:`ZstdCompressor`, similar to compressors in Python standard library.

    It would be nice to know some knowledge about zstd data, see :ref:`frame and block<frame_block>`.

.. py:function:: compress_stream(input_stream, output_stream, *, level_or_option=None, zstd_dict=None, pledged_input_size=None, read_size=131_072, write_size=131_591, callback=None)

    A fast and convenient function, compresses *input_stream* and writes the compressed data to *output_stream*, it doesn't close the streams.

    If input stream is ``b''``, nothing will be written to output stream.

    This function tries to zero-copy as much as possible. If the OS has read prefetching and write buffer, it may perform the tasks (read/compress/write) in parallel to some degree.

    The default values of *read_size* and *write_size* parameters are the buffer sizes recommended by zstd, increasing them may be faster, and reduces the number of callback function calls.

    .. versionadded:: 0.14.2

    :param input_stream: Input stream that has a `.readinto(b) <https://docs.python.org/3/library/io.html#io.RawIOBase.readinto>`_ method.
    :param output_stream: Output stream that has a `.write(b) <https://docs.python.org/3/library/io.html#io.RawIOBase.write>`_ method. If use *callback* function, this parameter can be ``None``.
    :param level_or_option: When it's an ``int`` object, it represents :ref:`compression level<compression_level>`. When it's a ``dict`` object, it contains :ref:`advanced compression parameters<CParameter>`. The default value ``None`` means to use zstd's default compression level/parameters.
    :type level_or_option: int or dict
    :param zstd_dict: Pre-trained dictionary for compression.
    :type zstd_dict: ZstdDict
    :param pledged_input_size: If set this parameter to the size of input data, the :ref:`size<content_size>` will be written into the frame header. If the actual input data doesn't match it, a :py:class:`ZstdError` exception will be raised. It may increase compression ratio slightly, and help decompression code to allocate output buffer faster.
    :type pledged_input_size: int
    :param read_size: Input buffer size, in bytes.
    :type read_size: int
    :param write_size: Output buffer size, in bytes.
    :type write_size: int
    :param callback: A callback function that accepts four parameters: ``(total_input, total_output, read_data, write_data)``. The first two are ``int`` objects. The last two are readonly `memoryview <https://docs.python.org/3/library/stdtypes.html#memory-views>`_ objects, if want to reference the data (or its slice) outside the callback function, `convert <https://docs.python.org/3/library/stdtypes.html#memoryview.tobytes>`_ them to ``bytes`` objects. If input stream is ``b''``, the callback function will not be called.
    :type callback: callable
    :return: A 2-item tuple, ``(total_input, total_output)``, the items are ``int`` objects.

    .. sourcecode:: python

        # compress an input file, and write to an output file.
        with io.open(input_file_path, 'rb') as ifh:
            with io.open(output_file_path, 'wb') as ofh:
                compress_stream(ifh, ofh, level_or_option=5)

        # compress a bytes object, and write to a file.
        with io.BytesIO(raw_dat) as bi:
            with io.open(output_file_path, 'wb') as ofh:
                compress_stream(bi, ofh, pledged_input_size=len(raw_dat))

        # Compress an input file, obtain a bytes object.
        # It's faster than reading a file and compressing it in
        # memory, tested on Ubuntu(Python3.8)/Windows(Python3.9).
        # Maybe the OS has prefetching, it can read and compress
        # data in parallel to some degree, reading file from HDD
        # is the bottleneck in this case.
        with io.open(input_file_path, 'rb') as ifh:
            with io.BytesIO() as bo:
                compress_stream(ifh, bo)
                compressed_dat = bo.getvalue()

        # Print progress using callback function
        def compress_print_progress(input_file_path, output_file_path):
            input_file_size = os.path.getsize(input_file_path)

            def func(total_input, total_output, read_data, write_data):
                # If input stream is empty, the callback function
                # will not be called. So no ZeroDivisionError here.
                percent = 100 * total_input / input_file_size
                print(f'Progress: {percent:.1f}%', end='\r')

            with io.open(input_file_path, 'rb') as ifh:
                with io.open(output_file_path, 'wb') as ofh:
                    compress_stream(ifh, ofh, callback=func)


.. py:class:: ZstdCompressor

    A streaming compressor. It's thread-safe at method level.

    .. py:method:: __init__(self, level_or_option=None, zstd_dict=None)

        Initialize a ZstdCompressor object.

        :param level_or_option: When it's an ``int`` object, it represents the :ref:`compression level<compression_level>`. When it's a ``dict`` object, it contains :ref:`advanced compression parameters<CParameter>`. The default value ``None`` means to use zstd's default compression level/parameters.
        :type level_or_option: int or dict
        :param zstd_dict: Pre-trained dictionary for compression.
        :type zstd_dict: ZstdDict

    .. py:method:: compress(self, data, mode=ZstdCompressor.CONTINUE)

        Provide data to the compressor object.

        :param data: Data to be compressed.
        :type data: bytes-like object
        :param mode: Can be these 3 values: :py:attr:`ZstdCompressor.CONTINUE`, :py:attr:`ZstdCompressor.FLUSH_BLOCK`, :py:attr:`ZstdCompressor.FLUSH_FRAME`.
        :return: A chunk of compressed data if possible, or ``b''`` otherwise.
        :rtype: bytes

    .. py:method:: flush(self, mode=ZstdCompressor.FLUSH_FRAME)

        Flush any remaining data in internal buffer.

        Since zstd data consists of one or more independent frames, the compressor object can still be used after this method is called.

        **Note**: Abuse of this method will reduce compression ratio, and some programs can only decompress single frame data. Use it only when necessary.

        :param mode: Can be these 2 values: :py:attr:`ZstdCompressor.FLUSH_FRAME`, :py:attr:`ZstdCompressor.FLUSH_BLOCK`.
        :return: Flushed data.
        :rtype: bytes

    .. py:attribute:: last_mode

        The last mode used to this compressor, its value can be :py:attr:`~ZstdCompressor.CONTINUE`, :py:attr:`~ZstdCompressor.FLUSH_BLOCK`, :py:attr:`~ZstdCompressor.FLUSH_FRAME`. Initialized to :py:attr:`~ZstdCompressor.FLUSH_FRAME`.

        It can be used to get the current state of a compressor, such as, data flushed, a frame ended.

    .. py:attribute:: CONTINUE

        Used for *mode* parameter in :py:meth:`~ZstdCompressor.compress` method.

        Collect more data, encoder decides when to output compressed result, for optimal compression ratio. Usually used for traditional streaming compression.

    .. py:attribute:: FLUSH_BLOCK

        Used for *mode* parameter in :py:meth:`~ZstdCompressor.compress`, :py:meth:`~ZstdCompressor.flush` methods.

        Flush any remaining data, but don't close the current :ref:`frame<frame_block>`. Usually used for communication scenarios.

        If there is data, it creates at least one new :ref:`block<frame_block>`, that can be decoded immediately on reception. If no remaining data, no block is created, return ``b''``.

        **Note**: Abuse of this mode will reduce compression ratio. Use it only when necessary.

    .. py:attribute:: FLUSH_FRAME

        Used for *mode* parameter in :py:meth:`~ZstdCompressor.compress`, :py:meth:`~ZstdCompressor.flush` methods.

        Flush any remaining data, and close the current :ref:`frame<frame_block>`. Usually used for traditional flush.

        Since zstd data consists of one or more independent frames, data can still be provided after a frame is closed.

        **Note**: Abuse of this mode will reduce compression ratio, and some programs can only decompress single frame data. Use it only when necessary.

    .. sourcecode:: python

        c = ZstdCompressor()

        # traditional streaming compression
        dat1 = c.compress(b'123456')
        dat2 = c.compress(b'abcdef')
        dat3 = c.flush()

        # use .compress() method with mode argument
        compressed_dat1 = c.compress(raw_dat1, c.FLUSH_BLOCK)
        compressed_dat2 = c.compress(raw_dat2, c.FLUSH_FRAME)

    .. hint:: Why :py:meth:`ZstdCompressor.compress` method has a *mode* parameter?

        #. When reuse :py:class:`ZstdCompressor` object for big number of same type individual data, make operation more convenient. The object is thread-safe at method level.
        #. If data is generated by a single :py:attr:`~ZstdCompressor.FLUSH_FRAME` mode, the size of uncompressed data will be recorded in frame header.


Streaming decompression
-----------------------

    This section contains:

        * function :py:func:`decompress_stream`, a fast and convenient function.
        * class :py:class:`ZstdDecompressor`, similar to decompressors in Python standard library.
        * class :py:class:`EndlessZstdDecompressor`, a decompressor accepts multiple concatenated :ref:`frames<frame_block>`.

.. py:function:: decompress_stream(input_stream, output_stream, *, zstd_dict=None, option=None, read_size=131_075, write_size=131_072, callback=None)

    A fast and convenient function, decompresses *input_stream* and writes the decompressed data to *output_stream*, it doesn't close the streams.

    Supports multiple concatenated :ref:`frames<frame_block>`.

    This function tries to zero-copy as much as possible. If the OS has read prefetching and write buffer, it may perform the tasks (read/decompress/write) in parallel to some degree.

    The default values of *read_size* and *write_size* parameters are the buffer sizes recommended by zstd, increasing them may be faster, and reduces the number of callback function calls.

    .. versionadded:: 0.14.2

    :param input_stream: Input stream that has a `.readinto(b) <https://docs.python.org/3/library/io.html#io.RawIOBase.readinto>`_ method.
    :param output_stream: Output stream that has a `.write(b) <https://docs.python.org/3/library/io.html#io.RawIOBase.write>`_ method. If use *callback* function, this parameter can be ``None``.
    :param zstd_dict: Pre-trained dictionary for decompression.
    :type zstd_dict: ZstdDict
    :param option: A ``dict`` object, contains :ref:`advanced decompression parameters<DParameter>`.
    :type option: dict
    :param read_size: Input buffer size, in bytes.
    :type read_size: int
    :param write_size: Output buffer size, in bytes.
    :type write_size: int
    :param callback: A callback function that accepts four parameters: ``(total_input, total_output, read_data, write_data)``. The first two are ``int`` objects. The last two are readonly `memoryview <https://docs.python.org/3/library/stdtypes.html#memory-views>`_ objects, if want to reference the data (or its slice) outside the callback function, `convert <https://docs.python.org/3/library/stdtypes.html#memoryview.tobytes>`_ them to ``bytes`` objects. If input stream is ``b''``, the callback function will not be called.
    :type callback: callable
    :return: A 2-item tuple, ``(total_input, total_output)``, the items are ``int`` objects.
    :raises ZstdError: If decompression fails.

    .. sourcecode:: python

        # decompress an input file, and write to an output file.
        with io.open(input_file_path, 'rb') as ifh:
            with io.open(output_file_path, 'wb') as ofh:
                decompress_stream(ifh, ofh)

        # decompress a bytes object, and write to a file.
        with io.BytesIO(compressed_dat) as bi:
            with io.open(output_file_path, 'wb') as ofh:
                decompress_stream(bi, ofh)

        # Decompress an input file, obtain a bytes object.
        # It's faster than reading a file and decompressing it in
        # memory, tested on Ubuntu(Python3.8)/Windows(Python3.9).
        # Maybe the OS has prefetching, it can read and decompress
        # data in parallel to some degree, reading file from HDD
        # is the bottleneck in this case.
        with io.open(input_file_path, 'rb') as ifh:
            with io.BytesIO() as bo:
                decompress_stream(ifh, bo)
                decompressed_dat = bo.getvalue()

        # Print progress using callback function
        def decompress_print_progress(input_file_path, output_file_path):
            input_file_size = os.path.getsize(input_file_path)

            def func(total_input, total_output, read_data, write_data):
                # If input stream is empty, the callback function
                # will not be called. So no ZeroDivisionError here.
                percent = 100 * total_input / input_file_size
                print(f'Progress: {percent:.1f}%', end='\r')

            with io.open(input_file_path, 'rb') as ifh:
                with io.open(output_file_path, 'wb') as ofh:
                    decompress_stream(ifh, ofh, callback=func)


.. py:class:: ZstdDecompressor

    A streaming decompressor.

    After a :ref:`frame<frame_block>` is decompressed, it stops and sets :py:attr:`~ZstdDecompressor.eof` flag to ``True``.

    For multiple frames data, use :py:class:`EndlessZstdDecompressor`.

    Thread-safe at method level.

    .. py:method:: __init__(self, zstd_dict=None, option=None)

        Initialize a ZstdDecompressor object.

        :param zstd_dict: Pre-trained dictionary for decompression.
        :type zstd_dict: ZstdDict
        :param dict option: A ``dict`` object that contains :ref:`advanced decompression parameters<DParameter>`. The default value ``None`` means to use zstd's default decompression parameters.

    .. py:method:: decompress(self, data, max_length=-1)

        Decompress *data*, returning decompressed data as a ``bytes`` object.

        After a :ref:`frame<frame_block>` is decompressed, it stops and sets :py:attr:`~ZstdDecompressor.eof` flag to ``True``.

        :param data: Data to be decompressed.
        :type data: bytes-like object
        :param int max_length: Maximum size of returned data. When it's negative, the output size is unlimited. When it's non-negative, returns at most *max_length* bytes of decompressed data. If this limit is reached and further output can (or may) be produced, the :py:attr:`~ZstdDecompressor.needs_input` attribute will be set to ``False``. In this case, the next call to this method may provide *data* as ``b''`` to obtain more of the output.

    .. py:attribute:: needs_input

        If the *max_length* output limit in :py:meth:`~ZstdDecompressor.decompress` method has been reached, and the decompressor has (or may has) unconsumed input data, it will be set to ``False``. In this case, pass ``b''`` to :py:meth:`~ZstdDecompressor.decompress` method may output further data.

        If ignore this attribute when there is unconsumed input data, there will be a little performance loss because of extra memory copy.

    .. py:attribute:: eof

        ``True`` means the end of the first frame has been reached. If decompress data after that, an ``EOFError`` exception will be raised.

    .. py:attribute:: unused_data

        A bytes object. When ZstdDecompressor object stops after decompressing a frame, unused input data after the first frame. Otherwise this will be ``b''``.

    .. sourcecode:: python

        # --- unlimited output ---
        d1 = ZstdDecompressor()

        decompressed_dat1 = d1.decompress(dat1)
        decompressed_dat2 = d1.decompress(dat2)
        decompressed_dat3 = d1.decompress(dat3)

        assert d1.eof, 'data is an incomplete zstd frame.'

        # --- limited output ---
        d2 = ZstdDecompressor()

        while True:
            if d2.needs_input:
                dat = read_input(2*1024*1024) # read 2 MiB input data
                if not dat: # input stream ends
                    raise Exception('Input stream ends, but the end of '
                                    'the first frame is not reached.')
            else: # maybe there is unconsumed input data
                dat = b''

            chunk = d2.decompress(dat, 10*1024*1024) # limit output buffer to 10 MiB
            write_output(chunk)

            if d2.eof: # reach the end of the first frame
                break


.. py:class:: EndlessZstdDecompressor

    A streaming decompressor.

    It doesn't stop after a :ref:`frame<frame_block>` is decompressed, can be used to decompress multiple concatenated frames.

    Thread-safe at method level.

    .. py:method:: __init__(self, zstd_dict=None, option=None)

        The parameters are the same as :py:meth:`ZstdDecompressor.__init__` method.

    .. py:method:: decompress(self, data, max_length=-1)

        The parameters are the same as :py:meth:`ZstdDecompressor.decompress` method.

        After decompressing a frame, it doesn't stop like :py:meth:`ZstdDecompressor.decompress`.

    .. py:attribute:: needs_input

        It's the same as :py:attr:`ZstdDecompressor.needs_input`.

    .. py:attribute:: at_frame_edge

        ``True`` when both the input and output streams are at a :ref:`frame<frame_block>` edge, or the decompressor just be initialized.

        This flag could be used to check data integrity in some cases.

    .. sourcecode:: python

        # --- streaming decompression, unlimited output ---
        d1 = EndlessZstdDecompressor()

        decompressed_dat1 = d1.decompress(dat1)
        decompressed_dat2 = d1.decompress(dat2)
        decompressed_dat3 = d1.decompress(dat3)

        assert d1.at_frame_edge, 'data ends in an incomplete frame.'

        # --- streaming decompression, limited output ---
        d2 = EndlessZstdDecompressor()

        while True:
            if d2.needs_input:
                dat = read_input(2*1024*1024) # read 2 MiB input data
                if not dat: # input stream ends
                    if not d2.at_frame_edge:
                        raise Exception('data ends in an incomplete frame.')
                    break
            else: # maybe there is unconsumed input data
                dat = b''

            chunk = d2.decompress(dat, 10*1024*1024) # limit output buffer to 10 MiB
            write_output(chunk)

    .. hint:: Why :py:class:`EndlessZstdDecompressor` doesn't stop at frame edges?

        If so, unused input data after an edge will be copied to an internal buffer, this may be a performance overhead.

        If want to stop at frame edges, write a wrapper using :py:class:`ZstdDecompressor` class. And don't feed too much data every time, the overhead of copying unused input data to :py:attr:`ZstdDecompressor.unused_data` attribute still exists.


.. _zstd_dict:

Dictionary
----------

    This section contains:

        * class :py:class:`ZstdDict`
        * function :py:func:`train_dict`
        * function :py:func:`finalize_dict`

.. note::
    If use pre-trained zstd dictionary, the compression ratio achievable on small data (a few KiB) improves dramatically.

    **Background**

    The smaller the amount of data to compress, the more difficult it is to compress. This problem is common to all compression algorithms, and reason is, compression algorithms learn from past data how to compress future data. But at the beginning of a new data set, there is no "past" to build upon.

    Zstd training mode can be used to tune the algorithm for a selected type of data. Training is achieved by providing it with a few samples (one file per sample). The result of this training is stored in a file called "dictionary", which must be loaded before compression and decompression.

    See the FAQ in `this file <https://github.com/facebook/zstd/blob/dev/lib/zdict.h>`_ for details.

    .. attention::

        #. If you lose a zstd dictionary, then can't decompress the corresponding data.
        #. Zstd dictionary has negligible effect on large data (multi-MiB) compression. If want to use large dictionary content, see prefix(:py:attr:`ZstdDict.as_prefix`).
        #. There is a possibility that the dictionary content could be maliciously tampered by a third party.

    **Advanced dictionary training**

    Pyzstd module only uses zstd library's stable API. The stable API only exposes two dictionary training functions that corresponding to :py:func:`train_dict` and :py:func:`finalize_dict`.

    If want to adjust advanced training parameters, you may use zstd's CLI program (not pyzstd module's CLI), it has entries to zstd library's experimental API.

.. py:class:: ZstdDict

    Represents a zstd dictionary, can be used for compression/decompression.

    It's thread-safe, and can be shared by multiple :py:class:`ZstdCompressor` / :py:class:`ZstdDecompressor` objects.

    .. sourcecode:: python

        # load a zstd dictionary from file
        with io.open(dict_path, 'rb') as f:
            file_content = f.read()
        zd = ZstdDict(file_content)

        # use the dictionary to compress.
        # if use a dictionary for compressor multiple times, reusing
        # a compressor object is faster, see .as_undigested_dict doc.
        compressed_dat = compress(raw_dat, zstd_dict=zd)

        # use the dictionary to decompress
        decompressed_dat = decompress(compressed_dat, zstd_dict=zd)

    .. versionchanged:: 0.15.7
        When compressing, load undigested dictionary instead of digested dictionary by default, see :py:attr:`~ZstdDict.as_digested_dict`. Also add ``.__len__()`` method that returning content size.

    .. py:method:: __init__(self, dict_content, is_raw=False)

        Initialize a ZstdDict object.

        :param dict_content: Dictionary's content.
        :type dict_content: bytes-like object
        :param is_raw: This parameter is for advanced user. ``True`` means *dict_content* argument is a "raw content" dictionary, free of any format restriction. ``False`` means *dict_content* argument is an ordinary zstd dictionary, was created by zstd functions, follow a specified format.
        :type is_raw: bool

    .. py:attribute:: dict_content

        The content of zstd dictionary, a ``bytes`` object. It's the same as *dict_content* argument in :py:meth:`~ZstdDict.__init__` method. It can be used with other programs.

    .. py:attribute:: dict_id

        ID of zstd dictionary, a 32-bit unsigned integer value. See :ref:`this note<dict_id>` for details.

        Non-zero means ordinary dictionary, was created by zstd functions, follow a specified format.

        ``0`` means a "raw content" dictionary, free of any format restriction, used for advanced user. (Note that the meaning of ``0`` is different from ``dictionary_id`` in :py:func:`get_frame_info` function.)

    .. py:attribute:: as_digested_dict

        Load as a digested dictionary, see below.

        .. versionadded:: 0.15.7

    .. py:attribute:: as_undigested_dict

        Load as an undigested dictionary.

        Digesting dictionary is a costly operation. These two attributes can control how the dictionary is loaded to compressor, by passing them as `zstd_dict` argument: ``compress(dat, zstd_dict=zd.as_digested_dict)``

        If don't specify these two attributes, use **undigested** dictionary for compression by default: ``compress(dat, zstd_dict=zd)``

        .. list-table:: Difference for compression
            :widths: 12 12 12
            :header-rows: 1

            * -
              - | Digested
                | dictionary
              - | Undigested
                | dictionary
            * - | Some advanced
                | parameters of
                | compressor may
                | be overridden
                | by dictionary's
                | parameters
              - | ``windowLog``, ``hashLog``,
                | ``chainLog``, ``searchLog``,
                | ``minMatch``, ``targetLength``,
                | ``strategy``,
                | ``enableLongDistanceMatching``,
                | ``ldmHashLog``, ``ldmMinMatch``,
                | ``ldmBucketSizeLog``,
                | ``ldmHashRateLog``, and some
                | non-public parameters.
              - No
            * - | ZstdDict has
                | internal cache
                | for this
              - | Yes. It's faster when
                | loading again a digested
                | dictionary with the same
                | compression level.
              - | No. If load an undigested
                | dictionary multiple times,
                | consider reusing a
                | compressor object.

        For decompression, they have the same effect. Pyzstd uses **digested** dictionary for decompression by default, which is faster when loading again: ``decompress(dat, zstd_dict=zd)``

        .. versionadded:: 0.15.7

    .. py:attribute:: as_prefix

        Load the dictionary content to compressor/decompressor as a "prefix", by passing this attribute as `zstd_dict` argument: ``compress(dat, zstd_dict=zd.as_prefix)``

        Prefix can be used for :ref:`patching engine<patching_engine>` scenario.

        #. Prefix is compatible with "long distance matching", while dictionary is not.
        #. Prefix only work for the first frame, then the compressor/decompressor will return to no prefix state. This is different from dictionary that can be used for all subsequent frames. Therefore, be careful when using with ZstdFile/SeekableZstdFile.
        #. When decompressing, must use the same prefix as when compressing.
        #. Loading prefix to compressor is costly.
        #. Loading prefix to decompressor is not costly.

        .. versionadded:: 0.15.7


.. py:function:: train_dict(samples, dict_size)

    Train a zstd dictionary.

    See the FAQ in `this file <https://github.com/facebook/zstd/blob/release/lib/zdict.h>`_ for details.

    :param samples: An iterable of samples, a sample is a bytes-like object represents a file.
    :type samples: iterable
    :param int dict_size: Returned zstd dictionary's **maximum** size, in bytes.
    :return: Trained zstd dictionary. If want to save the dictionary to a file, save the :py:attr:`ZstdDict.dict_content` attribute.
    :rtype: ZstdDict

    .. sourcecode:: python

        def samples():
            rootdir = r"E:\data"

            # Note that the order of the files may be different,
            # therefore the generated dictionary may be different.
            for parent, dirnames, filenames in os.walk(rootdir):
                for filename in filenames:
                    path = os.path.join(parent, filename)
                    with io.open(path, 'rb') as f:
                        dat = f.read()
                    yield dat

        dic = pyzstd.train_dict(samples(), 100*1024)

.. py:function:: finalize_dict(zstd_dict, samples, dict_size, level)

    Given a custom content as a basis for dictionary, and a set of samples, finalize dictionary by adding headers and statistics according to the zstd dictionary format.

    See the FAQ in `this file <https://github.com/facebook/zstd/blob/release/lib/zdict.h>`_ for details.

    :param zstd_dict: A basis dictionary.
    :type zstd_dict: ZstdDict
    :param samples: An iterable of samples, a sample is a bytes-like object represents a file.
    :type samples: iterable
    :param int dict_size: Returned zstd dictionary's **maximum** size, in bytes.
    :param int level: The compression level expected to use in production. The statistics for each compression level differ, so tuning the dictionary for the compression level can help quite a bit.
    :return: Finalized zstd dictionary. If want to save the dictionary to a file, save the :py:attr:`ZstdDict.dict_content` attribute.
    :rtype: ZstdDict


Module-level functions
----------------------

    This section contains:

        * function :py:func:`get_frame_info`, get frame information from a frame header.
        * function :py:func:`get_frame_size`, get a frame's size.

.. py:function:: get_frame_info(frame_buffer)

    Get zstd frame information from a frame header.

    Return a 2-item namedtuple: (decompressed_size, dictionary_id)

    If ``decompressed_size`` is ``None``, decompressed size is unknown.

    ``dictionary_id`` is a 32-bit unsigned integer value. ``0`` means dictionary ID was not recorded in frame header, the frame may or may not need a dictionary to be decoded, and the ID of such a dictionary is not specified. (Note that the meaning of ``0`` is different from :py:attr:`ZstdDict.dict_id` attribute.)

    It's possible to append more items to the namedtuple in the future.

    :param frame_buffer: It should starts from the beginning of a frame, and contains at least the frame header (6 to 18 bytes).
    :type frame_buffer: bytes-like object
    :return: Information about a frame.
    :rtype: namedtuple
    :raises ZstdError: When parsing the frame header fails.

.. sourcecode:: python

    >>> pyzstd.get_frame_info(compressed_dat[:20])
    frame_info(decompressed_size=687379, dictionary_id=1040992268)


.. py:function:: get_frame_size(frame_buffer)

    Get the size of a zstd frame, including frame header and 4-byte checksum if it has.

    It will iterate all blocks' header within a frame, to accumulate the frame's size.

    :param frame_buffer: It should starts from the beginning of a frame, and contains at least one complete frame.
    :type frame_buffer: bytes-like object
    :return: The size of a zstd frame.
    :rtype: int
    :raises ZstdError: When it fails.

.. sourcecode:: python

    >>> pyzstd.get_frame_size(compressed_dat)
    252874


Module-level variables
----------------------

    This section contains:

        * :py:data:`zstd_version`, a ``str``.
        * :py:data:`zstd_version_info`, a ``tuple``.
        * :py:data:`compressionLevel_values`, some values defined by the underlying zstd library.
        * :py:data:`zstd_support_multithread`, whether the underlying zstd library supports multi-threaded compression.

.. py:data:: zstd_version

    Underlying zstd library's version, ``str`` form.

.. sourcecode:: python

    >>> pyzstd.zstd_version
    '1.4.5'


.. py:data:: zstd_version_info

    Underlying zstd library's version, ``tuple`` form.

.. sourcecode:: python

    >>> pyzstd.zstd_version_info
    (1, 4, 5)


.. py:data:: compressionLevel_values

    A 3-item namedtuple, values defined by the underlying zstd library, see :ref:`compression level<compression_level>` for details.

    ``default`` is default compression level, it is used when compression level is set to ``0`` or not set.

    ``min``/``max`` are minimum/maximum available values of compression level, both inclusive.

.. sourcecode:: python

    >>> pyzstd.compressionLevel_values  # 131072 = 128*1024
    values(default=3, min=-131072, max=22)


.. py:data:: zstd_support_multithread

    Whether the underlying zstd library was compiled with :ref:`multi-threaded compression<mt_compression>` support.

    It's almost always ``True``.

    It's ``False`` when dynamically linked to zstd library that compiled without multi-threaded support. Ordinary users will not meet this situation.

.. versionadded:: 0.15.1

.. sourcecode:: python

    >>> pyzstd.zstd_support_multithread
    True


ZstdFile class and open() function
----------------------------------

    This section contains:

        * class :py:class:`ZstdFile`, open a zstd-compressed file in binary mode.
        * function :py:func:`open`, open a zstd-compressed file in binary or text mode.

.. py:class:: ZstdFile

    Open a zstd-compressed file in binary mode.

    This class is very similar to `bz2.BZ2File <https://docs.python.org/3/library/bz2.html#bz2.BZ2File>`_ /  `gzip.GzipFile <https://docs.python.org/3/library/gzip.html#gzip.GzipFile>`_ / `lzma.LZMAFile <https://docs.python.org/3/library/lzma.html#lzma.LZMAFile>`_ classes in Python standard library. But the performance is much better than them.

    Like BZ2File/GzipFile/LZMAFile classes, ZstdFile is not thread-safe, so if you need to use a single ZstdFile object from multiple threads, it is necessary to protect it with a lock.

    It can be used with Python's ``tarfile`` module, see :ref:`this note<with_tarfile>`.

    .. py:method:: __init__(self, filename, mode="r", *, level_or_option=None, zstd_dict=None, read_size=131_075, write_size=131_591)

        The *filename* argument can be an existing `file object <https://docs.python.org/3/glossary.html#term-file-object>`_ to wrap, or the name of the file to open (as a ``str``, ``bytes`` or `path-like <https://docs.python.org/3/glossary.html#term-path-like-object>`_ object). When wrapping an existing file object, the wrapped file will not be closed when the ZstdFile is closed.

        The *mode* argument can be either "r" for reading (default), "w" for overwriting, "x" for exclusive creation, or "a" for appending. These can equivalently be given as "rb", "wb", "xb" and "ab" respectively.

        In reading mode (decompression), *read_size* argument is bytes number that read from the underlying file object each time, default value is zstd's recommended value. If use with Network File System, increasing it may get better performance.

        In writing modes (compression), *write_size* argument is output buffer's size, default value is zstd's recommended value. If use with Network File System, increasing it may get better performance.

    .. versionchanged:: 0.15.9
        Add *read_size* and *write_size* arguments.

    In reading mode (decompression), these methods and statement are available:

        * `.read(size=-1) <https://docs.python.org/3/library/io.html#io.BufferedReader.read>`_
        * `.read1(size=-1) <https://docs.python.org/3/library/io.html#io.BufferedReader.read1>`_
        * `.readinto(b) <https://docs.python.org/3/library/io.html#io.BufferedIOBase.readinto>`_
        * `.readinto1(b) <https://docs.python.org/3/library/io.html#io.BufferedIOBase.readinto1>`_
        * `.readline(size=-1) <https://docs.python.org/3/library/io.html#io.IOBase.readline>`_
        * `.seek(offset, whence=io.SEEK_SET) <https://docs.python.org/3/library/io.html#io.IOBase.seek>`_, note that if seek to a position before the current position, or seek to a position relative to the end of the file (the first time), the decompression has to be restarted from zero. If seek, consider using :py:class:`SeekableZstdFile` class.
        * `.peek(size=-1) <https://docs.python.org/3/library/io.html#io.BufferedReader.peek>`_
        * `Iteration <https://docs.python.org/3/library/io.html#io.IOBase>`_, yield lines, line terminator is ``b'\n'``.

.. _write_methods:

    In writing modes (compression), these methods are available:

        * `.write(b) <https://docs.python.org/3/library/io.html#io.BufferedIOBase.write>`_
        * `.flush(mode=ZstdFile.FLUSH_BLOCK) <https://docs.python.org/3/library/io.html#io.IOBase.flush>`_, flush to the underlying stream:

            #. The *mode* argument can be ``ZstdFile.FLUSH_BLOCK``, ``ZstdFile.FLUSH_FRAME``.
            #. Contiguously invoking this method with ``.FLUSH_FRAME`` will not generate empty content frames.
            #. Abuse of this method will reduce compression ratio, use it only when necessary.
            #. If the program is interrupted afterwards, all data can be recovered. To ensure saving to disk, also need `os.fsync(fd) <https://docs.python.org/3/library/os.html#os.fsync>`_.

            (*Added in version 0.15.1, added mode argument in version 0.15.9.*)

    In both reading and writing modes, these methods and property are available:

        * `.close() <https://docs.python.org/3/library/io.html#io.IOBase.close>`_
        * `.tell() <https://docs.python.org/3/library/io.html#io.IOBase.tell>`_, return the current position of uncompressed content. In append mode, the initial position is 0.
        * `.fileno() <https://docs.python.org/3/library/io.html#io.IOBase.fileno>`_
        * `.closed <https://docs.python.org/3/library/io.html#io.IOBase.closed>`_ (a property attribute)
        * `.writable() <https://docs.python.org/3/library/io.html#io.IOBase.writable>`_
        * `.readable() <https://docs.python.org/3/library/io.html#io.IOBase.readable>`_
        * `.seekable() <https://docs.python.org/3/library/io.html#io.IOBase.seekable>`_

.. py:function:: open(filename, mode="rb", *, level_or_option=None, zstd_dict=None, encoding=None, errors=None, newline=None)

    Open a zstd-compressed file in binary or text mode, returning a file object.

    This function is very similar to `bz2.open() <https://docs.python.org/3/library/bz2.html#bz2.open>`_ / `gzip.open() <https://docs.python.org/3/library/gzip.html#gzip.open>`_ / `lzma.open() <https://docs.python.org/3/library/lzma.html#lzma.open>`_ functions in Python standard library.

    The *filename* parameter can be an existing `file object <https://docs.python.org/3/glossary.html#term-file-object>`_ to wrap, or the name of the file to open (as a ``str``, ``bytes`` or `path-like <https://docs.python.org/3/glossary.html#term-path-like-object>`_ object). When wrapping an existing file object, the wrapped file will not be closed when the returned file object is closed.

    The *mode* parameter can be any of "r", "rb", "w", "wb", "x", "xb", "a" or "ab" for binary mode, or "rt", "wt", "xt", or "at" for text mode. The default is "rb".

    If in reading mode (decompression), the *level_or_option* parameter can only be a ``dict`` object, that represents decompression option. It doesn't support ``int`` type compression level in this case.

    In binary mode, a :py:class:`ZstdFile` object is returned.

    In text mode, a :py:class:`ZstdFile` object is created, and wrapped in an `io.TextIOWrapper <https://docs.python.org/3/library/io.html#io.TextIOWrapper>`_ object with the specified encoding, error handling behavior, and line ending(s).

SeekableZstdFile class
----------------------

    This section contains facilities that supporting `Zstandard Seekable Format <https://github.com/facebook/zstd/blob/dev/contrib/seekable_format/zstd_seekable_compression_format.md>`_:

        * exception :py:class:`SeekableFormatError`
        * class :py:class:`SeekableZstdFile`

.. py:exception:: SeekableFormatError

    An error related to "Zstandard Seekable Format". Subclass of ``Exception``.

    .. versionadded:: 0.15.9

.. py:class:: SeekableZstdFile

    Subclass of :py:class:`ZstdFile`. This class can **only** create/write/read `Zstandard Seekable Format <https://github.com/facebook/zstd/blob/dev/contrib/seekable_format/zstd_seekable_compression_format.md>`_ file, or read 0-size file. It provides relatively fast seeking ability in read mode.

    Note that it doesn't verify/write the XXH64 checksum fields, using :py:attr:`~CParameter.checksumFlag` is faster and more flexible.

    :py:class:`ZstdFile` class can also read "Zstandard Seekable Format" file, but no fast seeking ability.

    .. versionadded:: 0.15.9

    .. py:method:: __init__(self, filename, mode="r", *, level_or_option=None, zstd_dict=None, read_size=131_075, write_size=131_591, max_frame_content_size=1024*1024*1024)

        Same as :py:meth:`ZstdFile.__init__`. Except in append mode (a, ab), *filename* argument can't be a file object, please use file path (str/bytes/PathLike form) in this mode.

        .. attention::

            *max_frame_content_size* argument is used for compression modes (w, wb, a, ab, x, xb).

            When the uncompressed data length reaches *max_frame_content_size*, the current :ref:`frame<frame_block>` is closed automatically.

            The default value (1 GiB) is almost useless. User should set this value based on the data and seeking requirement.

            To retrieve a byte, need to decompress all data before this byte in that frame. So if the size is small, it will increase seeking speed, but reduce compression ratio. If the size is large, it will reduce seeking speed, but increase compression ratio.

            Avoid really tiny frame sizes (<1 KiB), that would hurt compression ratio considerably.

            You can also manually close a frame using :ref:`f.flush(mode=f.FLUSH_FRAME)<write_methods>`.

    .. py:staticmethod:: is_seekable_format_file(filename)

        This static method checks if a file is "Zstandard Seekable Format" file or 0-size file.

        It parses the seek table at the end of the file, returns ``True`` if no format error.

        :param filename: A file to be checked
        :type filename: File path (str/bytes/PathLike), or file object in reading mode.
        :return: Result
        :rtype: bool

    .. sourcecode:: python

        # Convert an existing zstd file to Zstandard Seekable Format file.
        # 10 MiB per frame.
        with ZstdFile(IN_FILE, 'r') as ifh:
            with SeekableZstdFile(OUT_FILE, 'w',
                                  max_frame_content_size=10*1024*1024) as ofh:
                while True:
                    dat = ifh.read(30*1024*1024)
                    if not dat:
                        break
                    ofh.write(dat)

        # return True
        SeekableZstdFile.is_seekable_format_file(OUT_FILE)

Advanced parameters
-------------------

    This section contains class :py:class:`CParameter`, :py:class:`DParameter`, :py:class:`Strategy`, they are subclasses of ``IntEnum``, used for setting advanced parameters.

    Attributes of :py:class:`CParameter` class:

        - Compression level (:py:attr:`~CParameter.compressionLevel`)
        - Compress algorithm parameters (:py:attr:`~CParameter.windowLog`, :py:attr:`~CParameter.hashLog`, :py:attr:`~CParameter.chainLog`, :py:attr:`~CParameter.searchLog`, :py:attr:`~CParameter.minMatch`, :py:attr:`~CParameter.targetLength`, :py:attr:`~CParameter.strategy`)
        - Long distance matching (:py:attr:`~CParameter.enableLongDistanceMatching`, :py:attr:`~CParameter.ldmHashLog`, :py:attr:`~CParameter.ldmMinMatch`, :py:attr:`~CParameter.ldmBucketSizeLog`, :py:attr:`~CParameter.ldmHashRateLog`)
        - Misc (:py:attr:`~CParameter.contentSizeFlag`, :py:attr:`~CParameter.checksumFlag`, :py:attr:`~CParameter.dictIDFlag`)
        - Multi-threaded compression (:py:attr:`~CParameter.nbWorkers`, :py:attr:`~CParameter.jobSize`, :py:attr:`~CParameter.overlapLog`)

    Attribute of :py:class:`DParameter` class:

        - Decompression parameter (:py:attr:`~DParameter.windowLogMax`)

    Attributes of :py:class:`Strategy` class:

        :py:attr:`~Strategy.fast`, :py:attr:`~Strategy.dfast`, :py:attr:`~Strategy.greedy`, :py:attr:`~Strategy.lazy`, :py:attr:`~Strategy.lazy2`, :py:attr:`~Strategy.btlazy2`, :py:attr:`~Strategy.btopt`, :py:attr:`~Strategy.btultra`, :py:attr:`~Strategy.btultra2`.

.. _CParameter:

.. py:class:: CParameter(IntEnum)

    Advanced compression parameters.

    When using, put the parameters in a ``dict`` object, the key is a :py:class:`CParameter` name, the value is a 32-bit signed integer value.

    .. sourcecode:: python

        option = {CParameter.compressionLevel : 10,
                  CParameter.checksumFlag : 1}

        # used with compress() function
        compressed_dat = compress(raw_dat, option)

        # used with ZstdCompressor object
        c = ZstdCompressor(level_or_option=option)
        compressed_dat1 = c.compress(raw_dat)
        compressed_dat2 = c.flush()

    Parameter value should belong to an interval with lower and upper bounds, otherwise they will either trigger an error or be clamped silently.

    The constant values mentioned below are defined in `zstd.h <https://github.com/facebook/zstd/blob/release/lib/zstd.h>`_, note that these values may be different in different zstd versions.

    .. py:method:: bounds(self)

        Return lower and upper bounds of a parameter, both inclusive.

        .. sourcecode:: python

            >>> CParameter.compressionLevel.bounds()
            (-131072, 22)
            >>> CParameter.windowLog.bounds()
            (10, 31)
            >>> CParameter.enableLongDistanceMatching.bounds()
            (0, 1)

    .. py:attribute:: compressionLevel

        Set compression parameters according to pre-defined compressionLevel table, see :ref:`compression level<compression_level>` for details.

        Setting a compression level does not set all other compression parameters to default. Setting this will dynamically impact the compression parameters which have not been manually set, the manually set ones will "stick".

    .. py:attribute:: windowLog

        Maximum allowed back-reference distance, expressed as power of 2, ``1 << windowLog`` bytes.

        Larger values requiring more memory and typically compressing more.

        This will set a memory budget for streaming decompression. Using a value greater than ``ZSTD_WINDOWLOG_LIMIT_DEFAULT`` requires explicitly allowing such size at streaming decompression stage, see :py:attr:`DParameter.windowLogMax`. ``ZSTD_WINDOWLOG_LIMIT_DEFAULT`` is 27 in zstd v1.2+, means 128 MiB (1 << 27).

        Must be clamped between ``ZSTD_WINDOWLOG_MIN`` and ``ZSTD_WINDOWLOG_MAX``.

        Special: value ``0`` means "use default windowLog", then the value is dynamically set, see "W" column in `this table <https://github.com/facebook/zstd/blob/release/lib/compress/clevels.h>`_.

    .. py:attribute:: hashLog

        Size of the initial probe table, as a power of 2, resulting memory usage is ``1 << (hashLog+2)`` bytes.

        Must be clamped between ``ZSTD_HASHLOG_MIN`` and ``ZSTD_HASHLOG_MAX``.

        Larger tables improve compression ratio of strategies <= :py:attr:`~Strategy.dfast`, and improve speed of strategies > :py:attr:`~Strategy.dfast`.

        Special: value ``0`` means "use default hashLog", then the value is dynamically set, see "H" column in `this table <https://github.com/facebook/zstd/blob/release/lib/compress/clevels.h>`_.

    .. py:attribute:: chainLog

        Size of the multi-probe search table, as a power of 2, resulting memory usage is ``1 << (chainLog+2)`` bytes.

        Must be clamped between ``ZSTD_CHAINLOG_MIN`` and ``ZSTD_CHAINLOG_MAX``.

        Larger tables result in better and slower compression.

        This parameter is useless for :py:attr:`~Strategy.fast` strategy.

        It's still useful when using :py:attr:`~Strategy.dfast` strategy, in which case it defines a secondary probe table.

        Special: value ``0`` means "use default chainLog", then the value is dynamically set, see "C" column in `this table <https://github.com/facebook/zstd/blob/release/lib/compress/clevels.h>`_.

    .. py:attribute:: searchLog

        Number of search attempts, as a power of 2.

        More attempts result in better and slower compression.

        This parameter is useless for :py:attr:`~Strategy.fast` and :py:attr:`~Strategy.dfast` strategies.

        Special: value ``0`` means "use default searchLog", then the value is dynamically set, see "S" column in `this table <https://github.com/facebook/zstd/blob/release/lib/compress/clevels.h>`_.

    .. py:attribute:: minMatch

        Minimum size of searched matches.

        Note that Zstandard can still find matches of smaller size, it just tweaks its search algorithm to look for this size and larger.

        Larger values increase compression and decompression speed, but decrease ratio.

        Must be clamped between ``ZSTD_MINMATCH_MIN`` and ``ZSTD_MINMATCH_MAX``.

        Note that currently, for all strategies < :py:attr:`~Strategy.btopt`, effective minimum is ``4``, for all strategies > :py:attr:`~Strategy.fast`, effective maximum is ``6``.

        Special: value ``0`` means "use default minMatchLength", then the value is dynamically set, see "L" column in `this table <https://github.com/facebook/zstd/blob/release/lib/compress/clevels.h>`_.

    .. py:attribute:: targetLength

        Impact of this field depends on strategy.

        For strategies :py:attr:`~Strategy.btopt`, :py:attr:`~Strategy.btultra` & :py:attr:`~Strategy.btultra2`:

            Length of Match considered "good enough" to stop search.

            Larger values make compression stronger, and slower.

        For strategy :py:attr:`~Strategy.fast`:

            Distance between match sampling.

            Larger values make compression faster, and weaker.

        Special: value ``0`` means "use default targetLength", then the value is dynamically set, see "TL" column in `this table <https://github.com/facebook/zstd/blob/release/lib/compress/clevels.h>`_.

    .. py:attribute:: strategy

        See :py:attr:`Strategy` class definition.

        The higher the value of selected strategy, the more complex it is, resulting in stronger and slower compression.

        Special: value ``0`` means "use default strategy", then the value is dynamically set, see "strat" column in `this table <https://github.com/facebook/zstd/blob/release/lib/compress/clevels.h>`_.

    .. py:attribute:: enableLongDistanceMatching

        Enable long distance matching.

        Default value is ``0``, can be ``1``.

        This parameter is designed to improve compression ratio, for large inputs, by finding large matches at long distance. It increases memory usage and window size.

        Note:
            * Enabling this parameter increases default :py:attr:`~CParameter.windowLog` to 128 MiB except when expressly set to a different value.
            * This will be enabled by default if :py:attr:`~CParameter.windowLog` >= 128 MiB and compression strategy >= :py:attr:`~Strategy.btopt` (compression level 16+).

    .. py:attribute:: ldmHashLog

        Size of the table for long distance matching, as a power of 2.

        Larger values increase memory usage and compression ratio, but decrease compression speed.

        Must be clamped between ``ZSTD_HASHLOG_MIN`` and ``ZSTD_HASHLOG_MAX``, default: :py:attr:`~CParameter.windowLog` - 7.

        Special: value ``0`` means "automatically determine hashlog".

    .. py:attribute:: ldmMinMatch

        Minimum match size for long distance matcher.

        Larger/too small values usually decrease compression ratio.

        Must be clamped between ``ZSTD_LDM_MINMATCH_MIN`` and ``ZSTD_LDM_MINMATCH_MAX``.

        Special: value ``0`` means "use default value" (default: 64).

    .. py:attribute:: ldmBucketSizeLog

        Log size of each bucket in the LDM hash table for collision resolution.

        Larger values improve collision resolution but decrease compression speed.

        The maximum value is ``ZSTD_LDM_BUCKETSIZELOG_MAX``.

        Special: value ``0`` means "use default value" (default: 3).

    .. py:attribute:: ldmHashRateLog

        Frequency of inserting/looking up entries into the LDM hash table.

        Must be clamped between 0 and ``(ZSTD_WINDOWLOG_MAX - ZSTD_HASHLOG_MIN)``.

        Default is MAX(0, (:py:attr:`~CParameter.windowLog` - :py:attr:`~CParameter.ldmHashLog`)), optimizing hash table usage.

        Larger values improve compression speed.

        Deviating far from default value will likely result in a compression ratio decrease.

        Special: value ``0`` means "automatically determine hashRateLog".

    .. _content_size:

    .. py:attribute:: contentSizeFlag

        Uncompressed content size will be written into frame header whenever known.

        Default value is ``1``, can be ``0``.

        In traditional streaming compression, content size is unknown.

        In these compressions, the content size is known:

            * :py:func:`compress` function
            * :py:func:`richmem_compress` function
            * :py:class:`ZstdCompressor` class using a single :py:attr:`~ZstdCompressor.FLUSH_FRAME` mode
            * :py:class:`RichMemZstdCompressor` class
            * :py:func:`compress_stream` function setting *pledged_input_size* parameter

        The field in frame header is 1/2/4/8 bytes, depending on size value. It may help decompression code to allocate output buffer faster.

        \* :py:class:`ZstdCompressor` has an undocumented method to set the size, ``help(ZstdCompressor._set_pledged_input_size)`` to see the usage.

    .. py:attribute:: checksumFlag

        A 4-byte checksum (XXH64) of uncompressed content is written at the end of frame.

        Default value is ``0``, can be ``1``.

        Zstd's decompression code verifies it. If checksum mismatch, raises a :py:class:`ZstdError` exception, with a message like "Restored data doesn't match checksum".

    .. py:attribute:: dictIDFlag

        When applicable, dictionary's ID is written into frame header. See :ref:`this note<dict_id>` for details.

        Default value is ``1``, can be ``0``.

    .. py:attribute:: nbWorkers

        Select how many threads will be spawned to compress in parallel.

        When nbWorkers >= ``1``, enables multi-threaded compression, ``1`` means "1-thread multi-threaded mode". See :ref:`zstd multi-threaded compression<mt_compression>` for details.

        More workers improve speed, but also increase memory usage.

        ``0`` (default) means "single-threaded mode", no worker is spawned, compression is performed inside caller's thread.

    .. versionchanged:: 0.15.1
        Setting to ``1`` means "1-thread multi-threaded mode", instead of "single-threaded mode".

    .. py:attribute:: jobSize

        Size of a compression job, in bytes.

        This value is enforced only when :py:attr:`~CParameter.nbWorkers` >= 1.

        Each compression job is completed in parallel, so this value can indirectly impact the number of active threads.

        ``0`` means default, which is dynamically determined based on compression parameters.

        Non-zero value will be silently clamped to:

        * minimum value: ``max(overlap_size, 512_KiB)``. overlap_size is specified by :py:attr:`~CParameter.overlapLog` parameter.
        * maximum value: ``512_MiB if 32_bit_build else 1024_MiB``.

    .. py:attribute:: overlapLog

        Control the overlap size, as a fraction of window size. (The "window size" here is not strict :py:attr:`~CParameter.windowLog`, see zstd source code.)

        This value is enforced only when :py:attr:`~CParameter.nbWorkers` >= 1.

        The overlap size is an amount of data reloaded from previous job at the beginning of a new job. It helps preserve compression ratio, while each job is compressed in parallel. Larger values increase compression ratio, but decrease speed.

        Possible values range from 0 to 9:

        - 0 means "default" : The value will be determined by the library. The value varies between 6 and 9, depending on :py:attr:`~CParameter.strategy`.
        - 1 means "no overlap"
        - 9 means "full overlap", using a full window size.

        Each intermediate rank increases/decreases load size by a factor 2:

        9: full window;  8: w/2;  7: w/4;  6: w/8;  5: w/16;  4: w/32;  3: w/64;  2: w/128;  1: no overlap;  0: default.


.. _DParameter:

.. py:class:: DParameter(IntEnum)

    Advanced decompression parameters.

    When using, put the parameters in a ``dict`` object, the key is a :py:class:`DParameter` name, the value is a 32-bit signed integer value.

    .. sourcecode:: python

        # set memory allocation limit to 16 MiB (1 << 24)
        option = {DParameter.windowLogMax : 24}

        # used with decompress() function
        decompressed_dat = decompress(dat, option=option)

        # used with ZstdDecompressor object
        d = ZstdDecompressor(option=option)
        decompressed_dat = d.decompress(dat)

    Parameter value should belong to an interval with lower and upper bounds, otherwise they will either trigger an error or be clamped silently.

    The constant values mentioned below are defined in `zstd.h <https://github.com/facebook/zstd/blob/release/lib/zstd.h>`_, note that these values may be different in different zstd versions.

    .. py:method:: bounds(self)

        Return lower and upper bounds of a parameter, both inclusive.

        .. sourcecode:: python

            >>> DParameter.windowLogMax.bounds()
            (10, 31)

    .. py:attribute:: windowLogMax

        Select a size limit (in power of 2) beyond which the streaming API will refuse to allocate memory buffer in order to protect the host from unreasonable memory requirements.

        If a :ref:`frame<frame_block>` requires more memory than the set value, raises a :py:class:`ZstdError` exception, with a message like "Frame requires too much memory for decoding".

        This parameter is only useful in streaming mode, since no internal buffer is allocated in single-pass mode. :py:func:`decompress` function may use streaming mode or single-pass mode.

        By default, a decompression context accepts window sizes <= ``(1 << ZSTD_WINDOWLOG_LIMIT_DEFAULT)``, the constant is ``27`` in zstd v1.2+, means 128 MiB (1 << 27). If frame requested window size is greater than this value, need to explicitly set this parameter.

        Special: value ``0`` means "use default maximum windowLog".


.. py:class:: Strategy(IntEnum)

    Used for :py:attr:`CParameter.strategy`.

    Compression strategies, listed from fastest to strongest.

    Note : new strategies **might** be added in the future, only the order (from fast to strong) is guaranteed.

    .. py:attribute:: fast
    .. py:attribute:: dfast
    .. py:attribute:: greedy
    .. py:attribute:: lazy
    .. py:attribute:: lazy2
    .. py:attribute:: btlazy2
    .. py:attribute:: btopt
    .. py:attribute:: btultra
    .. py:attribute:: btultra2

    .. sourcecode:: python

        option = {CParameter.strategy : Strategy.lazy2,
                  CParameter.checksumFlag : 1}
        compressed_dat = compress(raw_dat, option)


Informative notes
-----------------

Compression level
>>>>>>>>>>>>>>>>>

.. _compression_level:

.. note:: Compression level

    Compression level is an integer:

    * ``1`` to ``22`` (currently), regular levels. Levels >= 20, labeled *ultra*, should be used with caution, as they require more memory.
    * ``0`` means use the default level, which is currently ``3`` defined by the underlying zstd library.
    * ``-131072`` to ``-1``, negative levels extend the range of speed vs ratio preferences. The lower the level, the faster the speed, but at the cost of compression ratio. 131072 = 128*1024.

    :py:data:`compressionLevel_values` are some values defined by the underlying zstd library.

    **For advanced user**

    Compression levels are just numbers that map to a set of compression parameters, see `this table <https://github.com/facebook/zstd/blob/release/lib/compress/clevels.h>`_ for overview. The parameters may be adjusted by the underlying zstd library after gathering some information, such as data size, using dictionary or not.

    Setting a compression level does not set all other :ref:`compression parameters<CParameter>` to default. Setting this will dynamically impact the compression parameters which have not been manually set, the manually set ones will "stick".


Frame and block
>>>>>>>>>>>>>>>

.. _frame_block:

.. note:: Frame and block

    **Frame**

    Zstd data consists of one or more independent "frames". The decompressed content of multiple concatenated frames is the concatenation of each frame decompressed content.

    A frame is completely independent, has a frame header, and a set of parameters which tells the decoder how to decompress it.

    In addition to normal frame, there is `skippable frame <https://github.com/facebook/zstd/blob/release/doc/zstd_compression_format.md#skippable-frames>`_ that can contain any user-defined data, skippable frame will be decompressed to ``b''``.

    **Block**

    A frame encapsulates one or multiple "blocks". Block has a guaranteed maximum size (3 bytes block header + 128 KiB), the actual maximum size depends on frame parameters.

    Unlike independent frames, each block depends on previous blocks for proper decoding, but doesn't need the following blocks, a complete block can be fully decompressed. So flushing block may be used in communication scenarios, see :py:attr:`ZstdCompressor.FLUSH_BLOCK`.

    .. attention::

        In some `language bindings <https://facebook.github.io/zstd/#other-languages>`_, decompress() function doesn't support multiple frames, or/and doesn't support a frame with unknown :ref:`content size<content_size>`, pay attention when compressing data for other language bindings.


Multi-threaded compression
>>>>>>>>>>>>>>>>>>>>>>>>>>

.. _mt_compression:

.. note:: Multi-threaded compression

    Zstd library supports multi-threaded compression. Set :py:attr:`CParameter.nbWorkers` parameter >= ``1`` to enable multi-threaded compression, ``1`` means "1-thread multi-threaded mode".

    The threads are spawned by the underlying zstd library, not by pyzstd module.

    .. sourcecode:: python

        # use 4 threads to compress
        option = {CParameter.nbWorkers : 4}
        compressed_dat = compress(raw_dat, option)

    The data will be split into portions and compressed in parallel. The portion size can be specified by :py:attr:`CParameter.jobSize` parameter, the overlap size can be specified by :py:attr:`CParameter.overlapLog` parameter, usually don't need to set these.

    The multi-threaded output will be different than the single-threaded output. However, both are deterministic, and the multi-threaded output produces the same compressed data no matter how many threads used.

    The multi-threaded output is a single :ref:`frame<frame_block>`, it's larger a little. Compressing a 520.58 MiB data, single-threaded output is 273.55 MiB, multi-threaded output is 274.33 MiB.

    .. hint::
        Using "CPU physical cores number" as threads number may be the fastest, to get the number need to install third-party module. `os.cpu_count() <https://docs.python.org/3/library/os.html#os.cpu_count>`_ can only get "CPU logical cores number" (hyper-threading capability).


Rich memory mode
>>>>>>>>>>>>>>>>

.. _rich_mem:

.. note:: Rich memory mode

    pyzstd module has a "rich memory mode" for compression. It allocates more memory for output buffer, and faster in some cases. Suitable for extremely fast compression scenarios.

    There is a :py:func:`richmem_compress` function, a :py:class:`RichMemZstdCompressor` class.

    Currently it won't be faster when using :ref:`zstd multi-threaded compression <mt_compression>`, it will issue a ``ResourceWarnings`` in this case.

    Effects:

    * The output buffer is larger than input data a little.
    * If input data is larger than ~31.8KB, up to 22% faster. The lower the compression level, the much faster it is usually.

    When not using this mode, the output buffer grows `gradually <https://github.com/animalize/pyzstd/blob/0.15.7/src/bin_ext/_zstdmodule.c#L218-L243>`_, in order not to allocate too much memory. The negative effect is that pyzstd module usually need to call the underlying zstd library's compress function multiple times.

    When using this mode, the size of output buffer is provided by ZSTD_compressBound() function, which is larger than input data a little (maximum compressed size in worst case single-pass scenario). For a 100 MiB input data, the allocated output buffer is (100 MiB + 400 KiB). The underlying zstd library avoids extra memory copy for this output buffer size.

    .. sourcecode:: python

        # use richmem_compress() function
        compressed_dat = richmem_compress(raw_dat)

        # reuse RichMemZstdCompressor object
        c = RichMemZstdCompressor()
        frame1 = c.compress(raw_dat1)
        frame2 = c.compress(raw_dat2)

    Compressing a 520.58 MiB data, it accelerates from 5.40 seconds to 4.62 seconds.


Use with tarfile module
>>>>>>>>>>>>>>>>>>>>>>>

.. _with_tarfile:

.. note:: Use with tarfile module

    Python's `tarfile <https://docs.python.org/3/library/tarfile.html>`_ module supports arbitrary compression algorithms by providing a file object.

    This code encapsulates a ``ZstdTarFile`` class using :py:class:`ZstdFile`, it can be used like `tarfile.TarFile <https://docs.python.org/3/library/tarfile.html#tarfile.TarFile>`_ class:

    .. sourcecode:: python

        import tarfile

        # when using read mode (decompression), the level_or_option parameter
        # can only be a dict object, that represents decompression option. It
        # doesn't support int type compression level in this case.

        class ZstdTarFile(tarfile.TarFile):
            def __init__(self, name, mode='r', *, level_or_option=None, zstd_dict=None, **kwargs):
                self.zstd_file = ZstdFile(name, mode,
                                          level_or_option=level_or_option,
                                          zstd_dict=zstd_dict)
                try:
                    super().__init__(fileobj=self.zstd_file, mode=mode, **kwargs)
                except:
                    self.zstd_file.close()
                    raise

            def close(self):
                try:
                    super().close()
                finally:
                    self.zstd_file.close()

        # write .tar.zst file (compression)
        with ZstdTarFile('archive.tar.zst', mode='w', level_or_option=5) as tar:
            # do something

        # read .tar.zst file (decompression)
        with ZstdTarFile('archive.tar.zst', mode='r') as tar:
            # do something

    When the above code is in read mode (decompression), and selectively read files multiple times, it may seek to a position before the current position, then the decompression has to be restarted from zero. If this slows down the operations, you can:

        #. Use :py:class:`SeekableZstdFile` class to create/read .tar.zst file.
        #. Decompress the archive to a temporary file, and read from it. This code encapsulates the process:

    .. sourcecode:: python

        import contextlib
        import io
        import tarfile
        import tempfile
        from pyzstd import decompress_stream

        @contextlib.contextmanager
        def ZstdTarReader(name, *, zstd_dict=None, option=None, **kwargs):
            with io.open(name, 'rb') as ifh:
                with tempfile.TemporaryFile() as tmp_file:
                    decompress_stream(ifh, tmp_file,
                                      zstd_dict=zstd_dict, option=option)
                    tmp_file.seek(0)
                    with tarfile.TarFile(fileobj=tmp_file, **kwargs) as tar:
                        yield tar

        with ZstdTarReader('archive.tar.zst') as tar:
            # do something


Zstd dictionary ID
>>>>>>>>>>>>>>>>>>

.. _dict_id:

.. note:: Zstd dictionary ID

    Dictionary ID is a 32-bit unsigned integer value. Decoder uses it to check if the correct dictionary is used.

    According to zstd dictionary format `specification <https://github.com/facebook/zstd/blob/release/doc/zstd_compression_format.md#dictionary-format>`_, if a dictionary is going to be distributed in public, the following ranges are reserved for future registrar and shall not be used:

        - low range: <= 32767
        - high range: >= 2^31

    Outside of these ranges, any value in (32767 < v < 2^31) can be used freely, even in public environment.

    In zstd frame header, the `Dictionary_ID <https://github.com/facebook/zstd/blob/release/doc/zstd_compression_format.md#dictionary_id>`_ field can be 0/1/2/4 bytes. If the value is small, this can save 2~3 bytes. Or don't write the ID by setting :py:attr:`CParameter.dictIDFlag` parameter.

    pyzstd module doesn't support specifying ID when training dictionary currently. If want to specify the ID, modify the dictionary content according to format specification, and take the corresponding risks.

    **Attention**

    In :py:class:`ZstdDict` class, :py:attr:`ZstdDict.dict_id` attribute == 0 means the dictionary is a "raw content" dictionary, free of any format restriction, used for advanced user. Non-zero means it's an ordinary dictionary, was created by zstd functions, follow the format specification.

    In :py:func:`get_frame_info` function, ``dictionary_id`` == 0 means dictionary ID was not recorded in the frame header, the frame may or may not need a dictionary to be decoded, and the ID of such a dictionary is not specified.


Use zstd as a patching engine
>>>>>>>>>>>>>>>>>>>>>>>>>>>>>

.. _patching_engine:

.. note:: Use zstd as a patching engine

    Zstd can be used as a great `patching engine <https://github.com/facebook/zstd/wiki/Zstandard-as-a-patching-engine>`_, although it has some limitations.

    In this particular scenario, pass :py:attr:`ZstdDict.as_prefix` attribute as `zstd_dict` argument. "Prefix" is similar to "raw content" dictionary, but zstd internally handles them differently, see `this issue <https://github.com/facebook/zstd/issues/2835>`_.

    Essentially, prefix is like being placed before the data to be compressed. See "ZSTD_c_deterministicRefPrefix" in `this file <https://github.com/facebook/zstd/blob/release/lib/zstd.h>`_.

    1, Generating a patch (compress)

    Assuming VER_1 and VER_2 are two versions.

    Let the "window" cover the longest version, by setting :py:attr:`CParameter.windowLog`. And enable "long distance matching" by setting :py:attr:`CParameter.enableLongDistanceMatching` to 1. The ``--patch-from`` option of zstd CLI also uses other parameters, but these two matter the most.

    The valid value of `windowLog` is [10,30] in 32-bit build, [10,31] in 64-bit build. So in 64-bit build, it has a `2GiB length limit <https://github.com/facebook/zstd/issues/2173>`_. Strictly speaking, the limit is (2GiB - ~100KiB). When this limit is exceeded, the patch becomes very large and loses the meaning of a patch.

    .. sourcecode:: python

        # use VER_1 as prefix
        v1 = ZstdDict(VER_1, is_raw=True)

        # let the window cover the longest version.
        # don't forget to clamp windowLog to valid range.
        # enable "long distance matching".
        windowLog = max(len(VER_1), len(VER_2)).bit_length()
        option = {CParameter.windowLog: windowLog,
                  CParameter.enableLongDistanceMatching: 1}

        # get a small PATCH
        PATCH = compress(VER_2, level_or_option=option, zstd_dict=v1.as_prefix)

    2, Applying the patch (decompress)

    Prefix is not dictionary, so the frame header doesn't record a :ref:`dictionary id<dict_id>`. When decompressing, must use the same prefix as when compressing. Otherwise ZstdError exception may be raised with a message like "Data corruption detected".

    Decompressing requires a window of the same size as when compressing, this may be a problem for small RAM device. If the window is larger than 128MiB, need to explicitly set :py:attr:`DParameter.windowLogMax` to allow larger window.

    .. sourcecode:: python

        # use VER_1 as prefix
        v1 = ZstdDict(VER_1, is_raw=True)

        # allow large window, the actual windowLog is from frame header.
        option = {DParameter.windowLogMax: 31}

        # get VER_2 from (VER_1 + PATCH)
        VER_2 = decompress(PATCH, zstd_dict=v1.as_prefix, option=option)


Build pyzstd module with options
>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>

.. _build_pyzstd:

.. note:: Build pyzstd module with options

    1️⃣ If provide ``--avx2`` build option, it will build with AVX2/BMI2 instructions. In MSVC build (static link), this brings some performance improvements. GCC/CLANG builds already dynamically dispatch some functions for BMI2 instructions, so no significant improvement, or worse.

    .. sourcecode:: shell

        # 🟠 pyzstd 0.15.4+ and pip 22.1+ support PEP-517:
        # build and install
        pip install --config-settings="--build-option=--avx2" -v pyzstd-0.15.4.tar.gz
        # build a redistributable wheel
        pip wheel --config-settings="--build-option=--avx2" -v pyzstd-0.15.4.tar.gz
        # 🟠 legacy commands:
        # build and install
        python setup.py install --avx2
        # build a redistributable wheel
        python setup.py bdist_wheel --avx2

    2️⃣ Pyzstd module supports:

        * Dynamically link to zstd library (provided by system or a DLL library), then the zstd source code in ``zstd`` folder will be ignored.
        * Provide a `CFFI <https://doc.pypy.org/en/latest/extending.html#cffi>`_ implementation that can work with PyPy.

    On CPython, provide these build options:

        #. no option: C implementation, statically link to zstd library.
        #. ``--dynamic-link-zstd``: C implementation, dynamically link to zstd library.
        #. ``--cffi``: CFFI implementation (slower), statically link to zstd library.
        #. ``--cffi --dynamic-link-zstd``: CFFI implementation (slower), dynamically link to zstd library.

    On PyPy, only CFFI implementation can be used, so ``--cffi`` is added implicitly. ``--dynamic-link-zstd`` is optional.

    .. sourcecode:: shell

        # 🟠 pyzstd 0.15.4+ and pip 22.1+ support PEP-517:
        # build and install
        pip3 install --config-settings="--build-option=--dynamic-link-zstd" -v pyzstd-0.15.4.tar.gz
        # build a redistributable wheel
        pip3 wheel --config-settings="--build-option=--dynamic-link-zstd" -v pyzstd-0.15.4.tar.gz
        # specify more than one option
        pip3 wheel --config-settings="--build-option=--dynamic-link-zstd --cffi" -v pyzstd-0.15.4.tar.gz
        # 🟠 legacy commands:
        # build and install
        python3 setup.py install --dynamic-link-zstd
        # build a redistributable wheel
        python3 setup.py bdist_wheel --dynamic-link-zstd

    Some notes:

        * The wheels on `PyPI <https://pypi.org/project/pyzstd>`_ use static linking, the packages on `Anaconda <https://anaconda.org/conda-forge/pyzstd>`_ use dynamic linking.
        * No matter static or dynamic linking, pyzstd module requires zstd v1.4.0+.
        * Static linking: Use zstd's official release without any change. If want to upgrade or downgrade the zstd library, just replace ``zstd`` folder.
        * Dynamic linking: If new zstd API is used at compile-time, linking to lower version run-time zstd library will fail. Use v1.5.0 new API if possible.

    On Windows, there is no system-wide zstd library. Pyzstd module can dynamically link to a DLL library, modify ``setup.py``:

    .. sourcecode:: python

        # E:\zstd_dll folder has zstd.h / zdict.h / libzstd.lib that
        # along with libzstd.dll
        if DYNAMIC_LINK:
            kwargs = {
            'include_dirs': ['E:\zstd_dll'], # .h directory
            'library_dirs': ['E:\zstd_dll'], # .lib directory
            'libraries': ['libzstd'],        # lib name, not filename, for the linker.
            ...

    And put ``libzstd.dll`` into one of these directories:

        * Directory added by `os.add_dll_directory() <https://docs.python.org/3/library/os.html#os.add_dll_directory>`_ function. (The unit-tests and the CLI can't utilize this)
        * Python's root directory that has python.exe.
        * %SystemRoot%\System32

    Note that the above list doesn't include the current working directory and %PATH% directories.

    3️⃣ Use "multi-phase initialization" on CPython.

    If provide ``--multi-phase-init`` build option, it will build with "multi-phase initialization" (`PEP-489 <https://peps.python.org/pep-0489/>`_) on CPython 3.11+.

    Since it adds a tiny overhead, it's disabled by default. It can be enabled after CPython's `sub-interpreters <https://peps.python.org/pep-0554/>`_ is mature.
