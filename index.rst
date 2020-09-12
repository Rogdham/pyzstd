.. title:: pyzstd moudle

``pyzstd`` module provides classes and functions for compressing and decompressing data using Facebook's `Zstandard <http://www.zstd.net>`_ (or zstd as short name) algorithm.

The interface provided by this module is similar to Python's bz2/lzma module.

Links: `GitHub page <https://github.com/animalize/pyzstd>`_, `PyPI page <https://pypi.org/project/pyzstd>`_.

Exception
---------

.. py:exception:: ZstdError

    This exception is raised when an error occurs when calling the underlying zstd library.


Common functions
----------------

    This section contains function :py:func:`compress`, :py:func:`decompress`.

    .. hint::
        If there is a big number of same type individual data, you may reuse a :ref:`stream <stream_classes>` object, the overhead of creating context / setting parameters / loading dictionary can be removed.


.. py:function:: compress(data, level_or_option=None, zstd_dict=None)

    Compress *data*, return the compressed data.

    :param data: Data to be compressed.
    :type data: bytes-like object
    :param level_or_option: When it's an ``int`` object, it represents :ref:`compression level<compression_level>`. When it's a ``dict`` object, it contains :ref:`advanced compress parameters<CParameter>`. The default value ``None`` means to use zstd's default compression level/parameters.
    :type level_or_option: int or dict
    :param zstd_dict: Pre-trained dictionary for compression.
    :type zstd_dict: ZstdDict
    :return: Compressed data
    :rtype: bytes

.. sourcecode:: python

    # use compression level
    compressed_dat = compress(raw_dat, 12)

    # use option
    option = {CParameter.compressionLevel : 10,
              CParameter.checksumFlag : 1}
    compressed_dat = compress(raw_dat, option)

.. _compression_level:

.. note:: About compression level

    Compression levels are just numbers that map to a set of compress parameters. The parameters may be adjusted by underlying zstd library after gathering some infomation, such as data size, using dictionary or not.

    zstd library supports regular compression levels from ``1`` up to ``22`` (currently). Levels >= 20, labeled *ultra*, should be used with caution, as they require more memory.

    ``0`` means use default compression level, which is currently ``3`` defined by underlying zstd library. zstd library also offers negative compression levels, which extend the range of speed vs ratio preferences. The lower the level, the faster the speed (at the cost of compression).

    :py:data:`compressionLevel_values` is some values defined by underlying zstd library.


.. py:function:: decompress(data, zstd_dict=None, option=None)

    Decompress *data*, return the decompressed data.

    :param data: Data to be decompressed.
    :type data: bytes-like object
    :param zstd_dict: Pre-trained dictionary for decompression.
    :type zstd_dict: ZstdDict
    :param option: A ``dict`` object that contains :py:ref:`advanced decompress parameters<DParameter>`. The default value ``None`` means to use zstd's default decompression parameters.
    :type option: dict
    :return: Decompressed data
    :rtype: bytes


.. _stream_classes:

Stream classes
--------------

    This section contains class :py:class:`ZstdCompressor`, :py:class:`ZstdDecompressor`.

    It would be nice to know some knowledge about zstd data:

.. note:: Frame and block

    zstd data consists of one or more independent "frames", so a zstd data doesn't have an end marker like other compression algorithms.

    A frame is completely independent, it has a frame header and epilogue, and a set of parameters which tells the decoder how to decompress it.

    In ``pyzstd`` module, :py:class:`ZstdCompressor` can still compress data after end a frame. :py:class:`ZstdDecompressor` doesn't have an ``eof`` maker, can decompress data endlessly as long as data is provided.

    A frame encapsulates one or multiple "blocks". Each block contains arbitrary content, which is described by its header, and has a guaranteed maximum content size, which depends on frame parameters. Unlike frames, each block depends on previous blocks for proper decoding. However, each block can be decompressed without waiting for its successor, allowing streaming operations.

.. py:class:: ZstdCompressor

    A stream compressor. It's thread-safe at method level.

    .. py:method:: __init__(self, level_or_option=None, zstd_dict=None)

        Initialize a ZstdCompressor object.

        :param level_or_option: When it's an ``int`` object, it represents the :ref:`compression level<compression_level>`. When it's a ``dict`` object, it contains :ref:`advanced compress parameters<CParameter>`. The default value ``None`` means to use zstd's default compression level/parameters.
        :type level_or_option: int or dict
        :param zstd_dict: Pre-trained dictionary for compression.
        :type zstd_dict: ZstdDict

    .. py:method:: compress(self, data, mode=ZstdCompressor.CONTINUE)

        Provide data to the compressor object.

        :param data: Data to be compressed.
        :type data: bytes-like object
        :param mode: Can be these values: :py:attr:`ZstdCompressor.CONTINUE`, :py:attr:`ZstdCompressor.FLUSH_BLOCK`, :py:attr:`ZstdCompressor.FLUSH_FRAME`.
        :return: A chunk of compressed data if possible, or ``b''`` otherwise.
        :rtype: bytes

        .. hint:: Why there is a *mode* parameter?

            #. Can generate frames flexibly.
            #. Can reuse :py:class:`ZstdCompressor` object for big number of individual data, it can operate in different threads easily.
            #. If data is generated by a single :py:attr:`~ZstdCompressor.FLUSH_FRAME` mode, the size of uncompressed data will be recorded in frame header.
            #. Convenient than compress() followed by a flush().

    .. py:method:: flush(self, end_frame=True)

        Flush the data in internal buffer.

        Since zstd data consists of one or more independent frames, the compressor object can be used after this method is called.

        ``c.flush(True)`` is equivalent to ``c.compress(b'', c.FLUSH_FRAME)``

        ``c.flush(False)`` is equivalent to ``c.compress(b'', c.FLUSH_BLOCK)``

        :param end_frame: When ``True``, flush data and end the frame, usually used for classical flush() operation. When ``False``, flush data but don't end the frame, usually used for communication, the receiver can decode the data immediately.
        :type end_frame: bool
        :return: Flushed data
        :rtype: bytes

    .. py:attribute:: last_mode

        The last mode used to this compressor, its value can be :py:attr:`~ZstdCompressor.CONTINUE`, :py:attr:`~ZstdCompressor.FLUSH_BLOCK`, :py:attr:`~ZstdCompressor.FLUSH_FRAME`. Initialized to :py:attr:`~ZstdCompressor.FLUSH_FRAME`.

        It can be used to get the current state of a compressor, such as, a block ends, a frame ends.

    .. py:attribute:: CONTINUE

        Used for :py:meth:`ZstdCompressor.compress` *mode* argument.

        Collect more data, encoder decides when to output compressed result, for optimal compression ratio. Usually used for ordinary streaming compression.

    .. py:attribute:: FLUSH_BLOCK

        Used for :py:meth:`ZstdCompressor.compress` *mode* argument.

        Flush any remaining data, but don't close current frame. If there is data, it creates at least one new block, that can be decoded immediately on reception. Usually used for communication.

    .. py:attribute:: FLUSH_FRAME

        Used for :py:meth:`ZstdCompressor.compress` *mode* argument.

        Flush any remaining data, and close current frame. Since zstd data consists of one or more independent frames, data can still be provided after a frame is closed. Usually used for classical flush.

    .. sourcecode:: python

        c = ZstdCompressor()

        dat1 = c.compress(b'123456')
        dat2 = c.compress(b'abcdef')
        dat3 = c.flush()

        dat1 = c.compress(b'123456')
        dat2 = c.compress(b'abcdef', c.FLUSH_FRAME)


.. py:class:: ZstdDecompressor

    A stream decompressor. It's thread-safe at method level.

    .. py:method:: __init__(self, zstd_dict=None, option=None)

        Initialize a ZstdDecompressor object.

        :param zstd_dict: Pre-trained dictionary for decompression.
        :type zstd_dict: ZstdDict
        :param dict option: A ``dict`` object that contains :ref:`advanced decompress parameters<DParameter>`. The default value ``None`` means to use zstd's default decompression parameters.

    .. py:method:: decompress(self, data, max_length=-1)

        Decompress *data*, returning uncompressed data as bytes.

        :param int max_length: When *max_length* is negative, the size of output buffer is unlimited. When *max_length* is nonnegative, returns at most *max_length* bytes of decompressed data. If this limit is reached and further output can be produced, the :py:attr:`~ZstdDecompressor.needs_input` attribute will be set to ``False``. In this case, the next call to this method may provide *data* as ``b''`` to obtain more of the output.

    .. py:attribute:: needs_input

        If *max_length* argument is nonnegative, and decompressor has (or may has) unconsumed input data, it will be set to ``False``. In this case, pass empty bytes ``b''`` to :py:meth:`~ZstdDecompressor.decompress` method can output unconsumed data.

    .. py:attribute:: at_frame_edge

        ``True`` when the output is at a frame edge, means a frame is completely decoded and fully flushed, or the decompressor just be initialized.

        Note that the input stream is not necessarily at a frame edge.


Dictionary
----------

    This section contains class :py:class:`ZstdDict`, function :py:func:`train_dict`.

.. attention::
    Using zstd dictionary, the compression ratio achievable on small data (a few KB) improves dramatically. Please note:

        #. If you lose a zstd dictionary, then can't decompress the corresponding data.
        #. zstd dictionary is vulnerable.
        #. zstd dictionary has very little effect on large data.


.. py:class:: ZstdDict

    Represents a pre-trained zstd dictionary, it can be used for compression/decompression.

    ZstdDict object is thread-safe, and can be shared by multiple :py:class:`ZstdCompressor` / :py:class:`ZstdDecompressor` objects.

    .. py:method:: __init__(self, dict_content)

        Initialize a ZstdDict object.

        :param dict_content: Dictionary's content.
        :type dict_content: bytes-like object
        :raises ValueError: If *dict_content* is not a valid zstd dictionary.

    .. py:attribute:: dict_content

        The content of the zstd dictionary, a bytes object. Can be used with other programs.

    .. py:attribute:: dict_id

        ID of zstd dictionary, a 32-bit unsigned integer value.


.. py:function:: train_dict(iterable_of_chunks, dict_size)

    Train a zstd dictionary.

    :param iterable_of_chunks: An iterable of samples.
    :type iterable_of_chunks: iterable
    :param int dict_size: The zstd dictionary's size, in bytes.
    :return: Trained zstd dictionary.
    :rtype: ZstdDict

.. tip:: Training a zstd dictionary

   1. A reasonable dictionary has a size of ~100 KB. It's possible to select smaller or larger size, just by specifying *dict_size* argument.
   2. It's recommended to provide a few thousands samples, though this can vary a lot.
   3. It's recommended that total size of all samples be about ~x100 times the target size of dictionary.
   4. Dictionary training will fail if there are not enough samples to construct a dictionary, or if most of the samples are too small (< 8 bytes being the lower limit). If dictionary training fails, you should use zstd without a dictionary, as the dictionary would've been ineffective anyways.

.. sourcecode:: python

    def chunks():
        rootdir = r"E:\data"

        # Note that the order of the files may be different,
        # therefore the generated dictionary may be different.
        for parent, dirnames, filenames in os.walk(rootdir):
            for filename in filenames:
                path = os.path.join(parent, filename)
                with open(path, 'rb') as f:
                    dat = f.read()
                yield dat

    dic = pyzstd.train_dict(chunks(), 100*1024)


Module-level functions
----------------------

    This section contains function :py:func:`get_frame_info`, :py:func:`get_frame_size`.

.. py:function:: get_frame_info(frame_buffer)

    Get zstd frame infomation from a frame header.

    Return a two-items namedtuple: (decompressed_size, dictionary_id). If decompressed size is unknown (generated by stream compression), it will be ``None``. If no dictionary, dictionary_id will be ``0``.

    It's possible to add more items to the namedtuple in the future.

    :param frame_buffer: It should starts from the beginning of a frame, and contain at least the frame header (6 to 18 bytes).
    :type frame_buffer: bytes-like object
    :return: Information about a frame.
    :rtype: namedtuple

.. sourcecode:: python

    >>> pyzstd.get_frame_info(compressed_dat)
    frame_info(decompressed_size=687379, dictionary_id=1040992268)


.. py:function:: get_frame_size(frame_buffer)

    Get the size of a zstd frame, including frame header and epilogue.

    It will iterate all blocks' header within a frame, to accumulate the frame's size.

    :param frame_buffer: It should starts from the beginning of a frame, and contain at least one complete frame.
    :type frame_buffer: bytes-like object
    :return: The size of a zstd frame.
    :rtype: int

.. sourcecode:: python

    >>> pyzstd.get_frame_size(compressed_dat)
    252874


Module-level variables
----------------------

    This section contains :py:data:`zstd_version`, :py:data:`zstd_version_info`, :py:data:`compressionLevel_values`.

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

    A three-items namedtuple, values defined by underlying zstd library, see :ref:`compression level<compression_level>` for details.

    ``default`` is default compression level, it is used when compression level is set to ``0``.

    ``min``/``max`` are minimum/maximum avaliable values of compression level, both inclusive.

.. sourcecode:: python

    >>> pyzstd.compressionLevel_values
    values(default=3, min=-131072, max=22)


Advanced parameters
-------------------

    This section contains class :py:class:`CParameter`, :py:class:`DParameter`, :py:class:`Strategy`.


.. _CParameter:

.. py:class:: CParameter(IntEnum)

    Advanced compress parameters.

    Each parameter should belong to an interval with lower and upper bounds, otherwise they will either trigger an error or be automatically clamped.

    View the constant values defined in `zstd.h <https://github.com/facebook/zstd/blob/master/lib/zstd.h>`_, note that these values may be different in different zstd versions.

    .. sourcecode:: python

        option = {CParameter.compressionLevel : 10,
                  CParameter.checksumFlag : 1}

        # used with compress() function
        compressed_dat = compress(raw_dat, option)

        # used with ZstdCompressor object
        c = ZstdCompressor(option=option)
        compressed_dat1 = c.compress(raw_dat)
        compressed_dat2 = c.flush()

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

        Set compression parameters according to pre-defined cLevel table, see :ref:`compression level<compression_level>` for details.

        Note that exact compression parameters are dynamically determined, depending on both compression level and data size (when known).

        Special: value ``0`` means use default compression level, which is controlled by ZSTD_CLEVEL_DEFAULT \*.

        Note 1 : it's possible to pass a negative compression level.

        Note 2 : setting a level does not automatically set all other compression parameters to default. Setting this will however eventually dynamically impact the compression parameters which have not been manually set. The manually set ones will 'stick'.

        \* ZSTD_CLEVEL_DEFAULT is ``3`` in zstd v1.4.5

    .. py:attribute:: windowLog

        Maximum allowed back-reference distance, expressed as power of 2.

        This will set a memory budget for streaming decompression, with larger values requiring more memory and typically compressing more.

        Must be clamped between ZSTD_WINDOWLOG_MIN and ZSTD_WINDOWLOG_MAX.

        Special: value ``0`` means "use default windowLog".

        Note: Using a windowLog greater than ZSTD_WINDOWLOG_LIMIT_DEFAULT \* requires explicitly allowing such size at streaming decompression stage.

        \* ZSTD_WINDOWLOG_LIMIT_DEFAULT is ``27`` in zstd v1.4.5

    .. py:attribute:: hashLog

        Size of the initial probe table, as a power of 2.

        Resulting memory usage is ``(1 << (hashLog+2))``.

        Must be clamped between ZSTD_HASHLOG_MIN and ZSTD_HASHLOG_MAX.

        Larger tables improve compression ratio of strategies <= :py:attr:`~Strategy.dfast`, and improve speed of strategies > :py:attr:`~Strategy.dfast`.

        Special: value ``0`` means "use default hashLog".

    .. py:attribute:: chainLog

        Size of the multi-probe search table, as a power of 2.

        Resulting memory usage is ``(1 << (chainLog+2))``.

        Must be clamped between ZSTD_CHAINLOG_MIN and ZSTD_CHAINLOG_MAX.

        Larger tables result in better and slower compression.

        This parameter is useless for :py:attr:`~Strategy.fast` strategy.

        It's still useful when using :py:attr:`~Strategy.dfast` strategy, in which case it defines a secondary probe table.

        Special: value ``0`` means "use default chainLog".

    .. py:attribute:: searchLog

        Number of search attempts, as a power of 2.

        More attempts result in better and slower compression.

        This parameter is useless for :py:attr:`~Strategy.fast` and :py:attr:`~Strategy.dfast` strategies.

        Special: value ``0`` means "use default searchLog".

    .. py:attribute:: minMatch

        Minimum size of searched matches.

        Note that Zstandard can still find matches of smaller size, it just tweaks its search algorithm to look for this size and larger.

        Larger values increase compression and decompression speed, but decrease ratio.

        Must be clamped between ZSTD_MINMATCH_MIN and ZSTD_MINMATCH_MAX.

        Note that currently, for all strategies < :py:attr:`~Strategy.btopt`, effective minimum is ``4``, for all strategies > :py:attr:`~Strategy.fast`, effective maximum is ``6``.

        Special: value ``0`` means "use default minMatchLength".

    .. py:attribute:: targetLength

        Impact of this field depends on strategy.

        For strategies :py:attr:`~Strategy.btopt`, :py:attr:`~Strategy.btultra` & :py:attr:`~Strategy.btultra2`:

            Length of Match considered "good enough" to stop search.

            Larger values make compression stronger, and slower.

        For strategy :py:attr:`~Strategy.fast`:

            Distance between match sampling.

            Larger values make compression faster, and weaker.

        Special: value ``0`` means "use default targetLength".

    .. py:attribute:: strategy

        See :py:attr:`Strategy` class definition.

        The higher the value of selected strategy, the more complex it is, resulting in stronger and slower compression.

        Special: value ``0`` means "use default strategy".

    .. py:attribute:: enableLongDistanceMatching

        Enable long distance matching.

        This parameter is designed to improve compression ratio, for large inputs, by finding large matches at long distance.

        It increases memory usage and window size.

        Note: enabling this parameter increases default :py:attr:`~CParameter.windowLog` to 128 MB except when expressly set to a different value.

    .. py:attribute:: ldmHashLog

        Size of the table for long distance matching, as a power of 2.

        Larger values increase memory usage and compression ratio, but decrease compression speed.

        Must be clamped between ZSTD_HASHLOG_MIN and ZSTD_HASHLOG_MAX, default: :py:attr:`~CParameter.windowLog` - 7.

        Special: value ``0`` means "automatically determine hashlog".

    .. py:attribute:: ldmMinMatch

        Minimum match size for long distance matcher.

        Larger/too small values usually decrease compression ratio.

        Must be clamped between ZSTD_LDM_MINMATCH_MIN and ZSTD_LDM_MINMATCH_MAX.

        Special: value ``0`` means "use default value" (default: 64).

    .. py:attribute:: ldmBucketSizeLog

        Log size of each bucket in the LDM hash table for collision resolution.

        Larger values improve collision resolution but decrease compression speed.

        The maximum value is ZSTD_LDM_BUCKETSIZELOG_MAX.

        Special: value ``0`` means "use default value" (default: 3).

    .. py:attribute:: ldmHashRateLog

        Frequency of inserting/looking up entries into the LDM hash table.

        Must be clamped between 0 and (ZSTD_WINDOWLOG_MAX - ZSTD_HASHLOG_MIN).

        Default is MAX(0, (:py:attr:`~CParameter.windowLog` - :py:attr:`~CParameter.ldmHashLog`)), optimizing hash table usage.

        Larger values improve compression speed.

        Deviating far from default value will likely result in a compression ratio decrease.

        Special: value ``0`` means "automatically determine hashRateLog".

    .. py:attribute:: contentSizeFlag

        Content size will be written into frame header **whenever known** (default:1)

        Content size must be known at the beginning of compression, such as using :py:func:`compress` function, or using :py:meth:`ZstdCompressor.compress` with a single :py:attr:`ZstdCompressor.FLUSH_FRAME` mode.

    .. py:attribute:: checksumFlag

        A 32-bits checksum of content is written at end of frame (default:0)

    .. py:attribute:: dictIDFlag

        When applicable, dictionary's ID is written into frame header (default:1)


.. _DParameter:

.. py:class:: DParameter(IntEnum)

    Advanced decompress parameters.

    Each parameter should belong to an interval with lower and upper bounds, otherwise they will either trigger an error or be automatically clamped.

    View the constant values defined in `zstd.h <https://github.com/facebook/zstd/blob/master/lib/zstd.h>`_, note that these values may be different in different zstd versions.

    .. sourcecode:: python

        option = {DParameter.windowLogMax : 20}

        # used with decompress() function
        decompressed_dat = decompress(dat, option=option)

        # used with ZstdDecompressor object
        d = ZstdDecompressor(option=option)
        decompressed_dat = d.decompress(dat)

    .. py:method:: bounds(self)

        Return lower and upper bounds of a parameter, both inclusive.

        .. sourcecode:: python

            >>> DParameter.windowLogMax.bounds()
            (10, 31)

    .. py:attribute:: windowLogMax

        Select a size limit (in power of 2) beyond which the streaming API will refuse to allocate memory buffer in order to protect the host from unreasonable memory requirements.

        This parameter is only useful in streaming mode \*, since no internal buffer is allocated in single-pass mode.

        By default, a decompression context accepts window sizes <= (1 << ZSTD_WINDOWLOG_LIMIT_DEFAULT). \*

        Special: value ``0`` means "use default maximum windowLog".

        \* pyzstd module uses streaming mode internally.

        \* ZSTD_WINDOWLOG_LIMIT_DEFAULT ``27`` in zstd v1.4.5


.. py:class:: Strategy(IntEnum)

    Used for :py:attr:`CParameter.strategy`, listed from fastest to strongest.

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
