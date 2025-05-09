from collections import namedtuple
from enum import IntEnum

from ._zstd import (
    EndlessZstdDecompressor,
    PYZSTD_CONFIG,
    RichMemZstdCompressor,
    ZstdCompressor,
    ZstdDecompressor,
    ZstdDict,
    ZstdError,
    ZstdFileReader,
    ZstdFileWriter,
    _ZSTD_CStreamSizes,
    _ZSTD_DStreamSizes,
    _ZSTD_btlazy2,
    _ZSTD_btopt,
    _ZSTD_btultra,
    _ZSTD_btultra2,
    _ZSTD_c_chainLog,
    _ZSTD_c_checksumFlag,
    _ZSTD_c_compressionLevel,
    _ZSTD_c_contentSizeFlag,
    _ZSTD_c_dictIDFlag,
    _ZSTD_c_enableLongDistanceMatching,
    _ZSTD_c_hashLog,
    _ZSTD_c_jobSize,
    _ZSTD_c_ldmBucketSizeLog,
    _ZSTD_c_ldmHashLog,
    _ZSTD_c_ldmHashRateLog,
    _ZSTD_c_ldmMinMatch,
    _ZSTD_c_minMatch,
    _ZSTD_c_nbWorkers,
    _ZSTD_c_overlapLog,
    _ZSTD_c_searchLog,
    _ZSTD_c_strategy,
    _ZSTD_c_targetCBlockSize,
    _ZSTD_c_targetLength,
    _ZSTD_c_windowLog,
    _ZSTD_d_windowLogMax,
    _ZSTD_dfast,
    _ZSTD_fast,
    _ZSTD_greedy,
    _ZSTD_lazy,
    _ZSTD_lazy2,
    _compressionLevel_values,
    _finalize_dict,
    _get_frame_info,
    _get_param_bounds,
    _set_parameter_types,
    _train_dict,
    compress_stream,
    decompress,
    decompress_stream,
    get_frame_size,
    zstd_version,
    zstd_version_info
)

__all__ = (# From this file
           'compressionLevel_values', 'get_frame_info',
           'CParameter', 'DParameter', 'Strategy',
           # From _zstd
           'ZstdCompressor', 'RichMemZstdCompressor',
           'ZstdDecompressor', 'EndlessZstdDecompressor',
           'ZstdDict', 'ZstdError', 'decompress', 'get_frame_size',
           'compress_stream', 'decompress_stream',
           'zstd_version', 'zstd_version_info',
           '_train_dict', '_finalize_dict',
           'ZstdFileReader', 'ZstdFileWriter',
           '_ZSTD_CStreamSizes', '_ZSTD_DStreamSizes',
           'PYZSTD_CONFIG')


# compressionLevel_values
_nt_values = namedtuple('values', ['default', 'min', 'max'])
compressionLevel_values = _nt_values(*_compressionLevel_values)


_nt_frame_info = namedtuple('frame_info',
                            ['decompressed_size', 'dictionary_id'])

def get_frame_info(frame_buffer):
    """Get zstd frame information from a frame header.

    Parameter
    frame_buffer: A bytes-like object. It should starts from the beginning of
                  a frame, and needs to include at least the frame header (6 to
                  18 bytes).

    Return a two-items namedtuple: (decompressed_size, dictionary_id)

    If decompressed_size is None, decompressed size is unknown.

    dictionary_id is a 32-bit unsigned integer value. 0 means dictionary ID was
    not recorded in the frame header, the frame may or may not need a dictionary
    to be decoded, and the ID of such a dictionary is not specified.

    It's possible to append more items to the namedtuple in the future."""

    ret_tuple = _get_frame_info(frame_buffer)
    return _nt_frame_info(*ret_tuple)


class _UnsupportedCParameter:
    def __set_name__(self, _, name):
        self.name = name

    def __get__(self, *_, **__):
        msg = ("%s CParameter only available when the underlying "
               "zstd library's version is greater than or equal to v1.5.6. "
               "At pyzstd module's run-time, zstd version is %s.") % \
               (self.name, zstd_version)
        raise NotImplementedError(msg)


class CParameter(IntEnum):
    """Compression parameters"""

    compressionLevel           = _ZSTD_c_compressionLevel
    windowLog                  = _ZSTD_c_windowLog
    hashLog                    = _ZSTD_c_hashLog
    chainLog                   = _ZSTD_c_chainLog
    searchLog                  = _ZSTD_c_searchLog
    minMatch                   = _ZSTD_c_minMatch
    targetLength               = _ZSTD_c_targetLength
    strategy                   = _ZSTD_c_strategy
    if zstd_version_info >= (1, 5, 6):
        targetCBlockSize       = _ZSTD_c_targetCBlockSize
    else:
        targetCBlockSize       = _UnsupportedCParameter()

    enableLongDistanceMatching = _ZSTD_c_enableLongDistanceMatching
    ldmHashLog                 = _ZSTD_c_ldmHashLog
    ldmMinMatch                = _ZSTD_c_ldmMinMatch
    ldmBucketSizeLog           = _ZSTD_c_ldmBucketSizeLog
    ldmHashRateLog             = _ZSTD_c_ldmHashRateLog

    contentSizeFlag            = _ZSTD_c_contentSizeFlag
    checksumFlag               = _ZSTD_c_checksumFlag
    dictIDFlag                 = _ZSTD_c_dictIDFlag

    nbWorkers                  = _ZSTD_c_nbWorkers
    jobSize                    = _ZSTD_c_jobSize
    overlapLog                 = _ZSTD_c_overlapLog

    def bounds(self):
        """Return lower and upper bounds of a compression parameter, both inclusive."""
        # 1 means compression parameter
        return _get_param_bounds(1, self.value)


class DParameter(IntEnum):
    """Decompression parameters"""

    windowLogMax = _ZSTD_d_windowLogMax

    def bounds(self):
        """Return lower and upper bounds of a decompression parameter, both inclusive."""
        # 0 means decompression parameter
        return _get_param_bounds(0, self.value)


class Strategy(IntEnum):
    """Compression strategies, listed from fastest to strongest.

    Note : new strategies _might_ be added in the future, only the order
    (from fast to strong) is guaranteed.
    """
    fast     = _ZSTD_fast
    dfast    = _ZSTD_dfast
    greedy   = _ZSTD_greedy
    lazy     = _ZSTD_lazy
    lazy2    = _ZSTD_lazy2
    btlazy2  = _ZSTD_btlazy2
    btopt    = _ZSTD_btopt
    btultra  = _ZSTD_btultra
    btultra2 = _ZSTD_btultra2


# Set CParameter/DParameter types for validity check
_set_parameter_types(CParameter, DParameter)
