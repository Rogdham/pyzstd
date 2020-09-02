``pyzstd`` module provides classes and functions for compressing and decompressing data using Facebook's `Zstandard <https://github.com/facebook/zstd>`_ (or ``zstd`` as short version) algorithm.

The interface provided by this module is very similar to that of Python's bz2/lzma module.


exception **ZstdError**

    This exception is raised when an error occurs when calling the zstd library.


function **compress(data, level_or_option=None, zstd_dict=None)**

    Compress *data* (a bytes-like object), returning the compressed data as a bytes object.

    *level_or_option* argument can be an ``int`` object, in this case represents the compression level. It can also be a ``dict`` object for setting advanced parameters. The default value ``None`` means to use zstd's default compression level/parameters.

    *zstd_dict* argument is pre-trained dictionary for compression, a ``ZstdDict`` object.

.. sourcecode:: python

    >>> option = {CompressParameter.compressionLevel:10,
    ...           CompressParameter.checksumFlag:1}
    >>> d2 = compress(d1, option)
    

function **decompress(zstd_dict=None, option=None)**

    Decompress data (a bytes-like object), returning the uncompressed data as a bytes object.

    *zstd_dict* argument is re-trained dictionary for decompression, a ``ZstdDict`` object.

    *option* argument is a ``dict`` that contains advanced parameters. The default value ``None`` means to use zstd's default decompression parameters.


class **ZstdDict(dict_content)**

    Initialize a ZstdDict object, it can be used for compress/decompress. ZstdDict object supports pickle.
    
    Using dictionary, the compression ratio achievable on small data improves dramatically.
    
    *dict_content* argument is dictionary's content, a bytes-like object.
      
    **dict_id**
    
        ID of Zstd dictionary, a 32-bit unsigned int value.

    **dict_content**
    
        The content of the Zstd dictionary, a bytes object.


function **train_dict(iterable_of_chunks, dict_size=100*1024)**

    Train a zstd dictionary, return a ZstdDict object.
    
    *iterable_of_chunks* is an iterable of samples. *dict_size* is the dictinary's size, in bytes.

    In general:
    
    1. A reasonable dictionary has a size of ~100 KB. It's possible to select smaller or larger size, just by specifying *dict_size* argument.
    
    2. It's recommended to provide a few thousands samples, though this can vary a lot.
    
    3. It's recommended that total size of all samples be about ~x100 times the target size of dictionary.

.. sourcecode:: python

    def chunks():
        rootdir = r"C:\data"
        
        # Note that the order of the files may be different,
        # therefore the generated dictionary may be different.
        for parent, dirnames, filenames in os.walk(rootdir):
            for filename in filenames:
                path = os.path.join(parent, filename)
                with io.open(path, 'rb') as f:
                    dat = f.read()
                yield dat
    
    dic = pyzstd.train_dict(chunks(), 200*1024)


class **ZstdCompressor(level_or_option=None, zstd_dict=None)**

    Initialize a ZstdCompressor object.

    *level_or_option* argument can be an ``int`` object, in this case represents the compression level. It can also be a ``dict`` object for setting advanced parameters. The default value ``None`` means to use zstd's default compression level/parameters.

    *zstd_dict* argument is pre-trained dictionary for compression, a ``ZstdDict`` object.
    
    
    **compress(data, end_directive=EndDirective.CONTINUE)**
    
        Provide data to the compressor object.
        Returns a chunk of compressed data if possible, or b'' otherwise.
        
        *data* argument is data to be compressed, a bytes-like object.

        *end_directive* can be these values:

            ``EndDirective.CONTINUE``: Collect more data, encoder decides when to output compressed result, for optimal compression ratio. Usually used for ordinary streaming compression.
            
            ``EndDirective.FLUSH``: Flush any remaining data, but don't end current frame. Usually used for communication, the receiver can decode the data immediately.
            
            ``EndDirective.END``: Flush any remaining data **and** close current frame.
   

    **flush(end_frame=True)**

        Finish the compression process.
        Returns the compressed data left in internal buffers.

        Since zstd data consists of one or more independent frames, the compressor object can be used after this method is called.

        When *end_frame* argument is ``True``, flush data and end the frame.
        When ``False`` flush data, but don't end the frame, usually used for communication, the receiver can decode the data immediately.
            
    **last_end_directive**
    
        The last end directive, initialized as ``EndDirective.END``.
        
        You may use this flag to get the current state of the compress stream. Such as, a block ends or a frame ends.


class **ZstdDecompressor(zstd_dict=None, option=None)**

    Initialize a ZstdDecompressor object.
    
    *zstd_dict* argument is re-trained dictionary for decompression, a ``ZstdDict`` object.

    *option* argument is a ``dict`` that contains advanced parameters. The default value ``None`` means to use zstd's default decompression parameters.

    **decompress(data, max_length=-1)**
    
        Decompress *data*, returning uncompressed data as bytes.

        If *max_length* is nonnegative, returns at most *max_length* bytes of decompressed data. If this limit is reached and further output can be produced, the ``needs_input`` attribute will be set to ``False``. In this case, the next call to decompress() may provide data as ``b''`` to obtain more of the output.
        
    **needs_input**
    
        ``True`` if more input is needed before more decompressed data can be produced.
    
    **at_frame_edge**
    
        ``True`` when the output is at a frame edge, means a frame is completely decoded and fully flushed, or the decompressor just be initialized. Note that the input stream is not necessarily at a frame edge.


function **get_frame_info(frame_buffer)**

    Get zstd frame infomation from a frame header.

    Return a two-items tuple: (decompressed_size, dictinary_id). If decompressed size is unknown (generated by stream compression), it will be ``None``. If no dictionary, dictinary_id will be ``0``.
    
    *frame_buffer* argument is a bytes-like object. It should starts from the beginning of a frame, and needs to include at least the frame header (6 to 18 bytes).

.. sourcecode:: python

    >>> pyzstd.get_frame_info(frame_buffer)
    (1437307, 1602083250)


function **get_frame_size(frame_buffer)**

    Get the size of a zstd frame.

    It will iterate all blocks' header within a frame, to get the size of the frame.
    
    *frame_buffer* argument is a bytes-like object. It should starts from the beginning of a frame, and needs to contain at least one complete frame.

.. sourcecode:: python

    >>> pyzstd.get_frame_size(frame_buffer)
    252874


class **EndDirective(IntEnum)**

    Stream compressor's end directive.
    
    **CONTINUE**
        
        Collect more data, encoder decides when to output compressed result, for optimal compression ratio. Usually used for ordinary streaming compression.
        
    **FLUSH**
    
        Flush any remaining data, but don't end current frame. Usually used for communication, the receiver can decode immediately.
    
    **END**
    
        Flush any remaining data and close current frame.

class **Strategy(IntEnum)**

    Used for ``CompressParameter.strategy``.

    Note : new strategies **might** be added in the future, only the order (from fast to strong) is guaranteed

    **fast**
    
    **dfast**
    
    **greedy**
    
    **lazy**
    
    **lazy2**
    
    **btlazy2**
    
    **btopt**
    
    **btultra**
    
    **btultra2**

class **CompressParameter(IntEnum)**

    Advanced compress Parameters.
    
    function **bounds(self)**
        
        Return lower and upper bounds of a parameter, both inclusive.
        
    .. sourcecode:: python

        >>> CompressParameter.compressionLevel.bounds()
        (-131072, 22)
        >>> CompressParameter.enableLongDistanceMatching.bounds()
        (0, 1)


    **compressionLevel**
    
        Set compression parameters according to pre-defined cLevel table.

        Note that exact compression parameters are dynamically determined, depending on both compression level and srcSize (when known).

        Default level is ZSTD_CLEVEL_DEFAULT==3.
        
        Special: value 0 means default, which is controlled by ZSTD_CLEVEL_DEFAULT.
        
        Note 1 : it's possible to pass a negative compression level.
        
        Note 2 : setting a level does not automatically set all other compression parameters to default. Setting this will however eventually dynamically impact the compression parameters which have not been manually set. The manually set ones will 'stick'.
        
    **windowLog**
    
        Maximum allowed back-reference distance, expressed as power of 2.
        
        This will set a memory budget for streaming decompression, with larger values requiring more memory and typically compressing more.
        
        Must be clamped between ZSTD_WINDOWLOG_MIN and ZSTD_WINDOWLOG_MAX.
        
        Special: value 0 means "use default windowLog".
        
        Note: Using a windowLog greater than ZSTD_WINDOWLOG_LIMIT_DEFAULT requires explicitly allowing such size at streaming decompression stage.
    
    **hashLog**
    
        Size of the initial probe table, as a power of 2.
        
        Resulting memory usage is ``(1 << (hashLog+2))``.
        
        Must be clamped between ZSTD_HASHLOG_MIN and ZSTD_HASHLOG_MAX.
        
        Larger tables improve compression ratio of strategies <= dFast, and improve speed of strategies > dFast.
        
        Special: value 0 means "use default hashLog".
        
    **chainLog**
    
        Size of the multi-probe search table, as a power of 2.
        
        Resulting memory usage is ``(1 << (chainLog+2))``.
        
        Must be clamped between ZSTD_CHAINLOG_MIN and ZSTD_CHAINLOG_MAX.
        
        Larger tables result in better and slower compression.
        
        This parameter is useless for "fast" strategy.
        
        It's still useful when using "dfast" strategy, in which case it defines a secondary probe table.
        
        Special: value 0 means "use default chainLog".
    
    **searchLog**
    
        Number of search attempts, as a power of 2.
        
        More attempts result in better and slower compression.
        
        This parameter is useless for "fast" and "dFast" strategies.
        
        Special: value 0 means "use default searchLog".
        
    **minMatch**
    
        Minimum size of searched matches.
        
        Note that Zstandard can still find matches of smaller size, it just tweaks its search algorithm to look for this size and larger.
        
        Larger values increase compression and decompression speed, but decrease ratio.
        
        Must be clamped between ZSTD_MINMATCH_MIN and ZSTD_MINMATCH_MAX.
        
        Note that currently, for all strategies < btopt, effective minimum is 4, for all strategies > fast, effective maximum is 6.
        
        Special: value 0 means "use default minMatchLength".
    
    **targetLength**
    
        Impact of this field depends on strategy.
        
        For strategies btopt, btultra & btultra2:
        
            Length of Match considered "good enough" to stop search.
            
            Larger values make compression stronger, and slower.
        
        For strategy fast:
        
            Distance between match sampling.
            
            Larger values make compression faster, and weaker.
            
        Special: value 0 means "use default targetLength".
    
    **strategy**
    
        See ZSTD_strategy class definition.
        
        The higher the value of selected strategy, the more complex it is, resulting in stronger and slower compression.
        
        Special: value 0 means "use default strategy".
    
    **enableLongDistanceMatching**
    
        Enable long distance matching.
        
        This parameter is designed to improve compression ratio, for large inputs, by finding large matches at long distance.
        
        It increases memory usage and window size.
        
        Note: enabling this parameter increases default ZSTD_c_windowLog to 128 MB except when expressly set to a different value.
    
    **ldmHashLog**
    
        Size of the table for long distance matching, as a power of 2.
        
        Larger values increase memory usage and compression ratio, but decrease compression speed.
        
        Must be clamped between ZSTD_HASHLOG_MIN and ZSTD_HASHLOG_MAX, default: windowlog - 7.
    
        Special: value 0 means "automatically determine hashlog".
    
    **ldmMinMatch**
    
        Minimum match size for long distance matcher.
        
        Must be clamped between ZSTD_LDM_MINMATCH_MIN and ZSTD_LDM_MINMATCH_MAX.
        
        Special: value 0 means "use default value" (default: 64).
    
    **ldmBucketSizeLog**
    
        Log size of each bucket in the LDM hash table for collision resolution.
        
        Larger values improve collision resolution but decrease compression speed.
        
        The maximum value is ZSTD_LDM_BUCKETSIZELOG_MAX.
        
        Special: value 0 means "use default value" (default: 3). 
    
    **ldmHashRateLog**
    
        Frequency of inserting/looking up entries into the LDM hash table.
        
        Must be clamped between 0 and (ZSTD_WINDOWLOG_MAX - ZSTD_HASHLOG_MIN).
        
        Default is MAX(0, (windowLog - ldmHashLog)), optimizing hash table usage.
        
        Larger values improve compression speed.
        
        Deviating far from default value will likely result in a compression ratio decrease.
        
        Special: value 0 means "automatically determine hashRateLog".
    
    **contentSizeFlag**
    
        Content size will be written into frame header *whenever known* (default:1)
        
        Content size must be known at the beginning of compression.
        
        This is automatically the case when using ZSTD_compress2(),
        
        For streaming scenarios, content size must be provided with ZSTD_CCtx_setPledgedSrcSize()
    
    **checksumFlag**
    
        A 32-bits checksum of content is written at end of frame (default:0)
    
    **dictIDFlag**
    
        When applicable, dictionary's ID is written into frame header (default:1)


class **DecompressParameter(IntEnum)**

    Advanced decompress Parameters.

    function **bounds(self)**
        
        Return lower and upper bounds of a parameter, both inclusive.
        
    .. sourcecode:: python

        >>> DecompressParameter.windowLogMax.bounds()
        (10, 31)


    **windowLogMax**
    
        Select a size limit (in power of 2) beyond which the streaming API will refuse to allocate memory buffer in order to protect the host from unreasonable memory requirements.
        
        This parameter is only useful in streaming mode, since no internal buffer is allocated in single-pass mode.
        
        By default, a decompression context accepts window sizes <= (1 << ZSTD_WINDOWLOG_LIMIT_DEFAULT).
        
        Special: value 0 means "use default maximum windowLog".
                              