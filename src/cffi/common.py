import sys
from collections import namedtuple
from enum import IntEnum
from functools import lru_cache

from ._cffi_zstd import ffi, lib as m

PYZSTD_CONFIG = (64 if sys.maxsize > 2**32 else 32,
                 'cffi', bool(m.pyzstd_static_link), False)

_ZSTD_CStreamSizes = (m.ZSTD_CStreamInSize(), m.ZSTD_CStreamOutSize())
_ZSTD_DStreamSizes = (m.ZSTD_DStreamInSize(), m.ZSTD_DStreamOutSize())

zstd_version = ffi.string(m.ZSTD_versionString()).decode('ascii')
zstd_version_info = tuple(int(i) for i in zstd_version.split('.'))

_nt_values = namedtuple('values', ['default', 'min', 'max'])
compressionLevel_values = _nt_values(m.ZSTD_defaultCLevel(),
                                     m.ZSTD_minCLevel(),
                                     m.ZSTD_maxCLevel())

_new_nonzero = ffi.new_allocator(should_clear_after_alloc=False)

def _nbytes(dat):
    if isinstance(dat, (bytes, bytearray)):
        return len(dat)
    with memoryview(dat) as mv:
        return mv.nbytes

class ZstdError(Exception):
    "Call to the underlying zstd library failed."
    pass

def _get_param_bounds(is_compress, key):
    # Get parameter bounds
    if is_compress:
        bounds = m.ZSTD_cParam_getBounds(key)
        if m.ZSTD_isError(bounds.error):
            _set_zstd_error(_ErrorType.ERR_GET_C_BOUNDS, bounds.error)
    else:
        bounds = m.ZSTD_dParam_getBounds(key)
        if m.ZSTD_isError(bounds.error):
            _set_zstd_error(_ErrorType.ERR_GET_D_BOUNDS, bounds.error)

    return (bounds.lowerBound, bounds.upperBound)

class CParameter(IntEnum):
    """Compression parameters"""

    compressionLevel           = m.ZSTD_c_compressionLevel
    windowLog                  = m.ZSTD_c_windowLog
    hashLog                    = m.ZSTD_c_hashLog
    chainLog                   = m.ZSTD_c_chainLog
    searchLog                  = m.ZSTD_c_searchLog
    minMatch                   = m.ZSTD_c_minMatch
    targetLength               = m.ZSTD_c_targetLength
    strategy                   = m.ZSTD_c_strategy

    enableLongDistanceMatching = m.ZSTD_c_enableLongDistanceMatching
    ldmHashLog                 = m.ZSTD_c_ldmHashLog
    ldmMinMatch                = m.ZSTD_c_ldmMinMatch
    ldmBucketSizeLog           = m.ZSTD_c_ldmBucketSizeLog
    ldmHashRateLog             = m.ZSTD_c_ldmHashRateLog

    contentSizeFlag            = m.ZSTD_c_contentSizeFlag
    checksumFlag               = m.ZSTD_c_checksumFlag
    dictIDFlag                 = m.ZSTD_c_dictIDFlag

    nbWorkers                  = m.ZSTD_c_nbWorkers
    jobSize                    = m.ZSTD_c_jobSize
    overlapLog                 = m.ZSTD_c_overlapLog

    @lru_cache(maxsize=None)
    def bounds(self):
        """Return lower and upper bounds of a compression parameter, both inclusive."""
        # 1 means compression parameter
        return _get_param_bounds(1, self.value)

class DParameter(IntEnum):
    """Decompression parameters"""

    windowLogMax = m.ZSTD_d_windowLogMax

    @lru_cache(maxsize=None)
    def bounds(self):
        """Return lower and upper bounds of a decompression parameter, both inclusive."""
        # 0 means decompression parameter
        return _get_param_bounds(0, self.value)

class Strategy(IntEnum):
    """Compression strategies, listed from fastest to strongest.

    Note : new strategies _might_ be added in the future, only the order
    (from fast to strong) is guaranteed.
    """
    fast     = m.ZSTD_fast
    dfast    = m.ZSTD_dfast
    greedy   = m.ZSTD_greedy
    lazy     = m.ZSTD_lazy
    lazy2    = m.ZSTD_lazy2
    btlazy2  = m.ZSTD_btlazy2
    btopt    = m.ZSTD_btopt
    btultra  = m.ZSTD_btultra
    btultra2 = m.ZSTD_btultra2

class _ErrorType:
    ERR_DECOMPRESS=0
    ERR_COMPRESS=1
    ERR_SET_PLEDGED_INPUT_SIZE=2

    ERR_LOAD_D_DICT=3
    ERR_LOAD_C_DICT=4

    ERR_GET_C_BOUNDS=5
    ERR_GET_D_BOUNDS=6
    ERR_SET_C_LEVEL=7

    ERR_TRAIN_DICT=8
    ERR_FINALIZE_DICT=9

    _TYPE_MSG = (
        "Unable to decompress zstd data: %s",
        "Unable to compress zstd data: %s",
        "Unable to set pledged uncompressed content size: %s",

        "Unable to load zstd dictionary or prefix for decompression: %s",
        "Unable to load zstd dictionary or prefix for compression: %s",

        "Unable to get zstd compression parameter bounds: %s",
        "Unable to get zstd decompression parameter bounds: %s",
        "Unable to set zstd compression level: %s",

        "Unable to train zstd dictionary: %s",
        "Unable to finalize zstd dictionary: %s")

    @staticmethod
    def get_type_msg(type):
        return _ErrorType._TYPE_MSG[type]

def _set_zstd_error(type, zstd_ret):
    msg = _ErrorType.get_type_msg(type) % \
          ffi.string(m.ZSTD_getErrorName(zstd_ret)).decode('utf-8')
    raise ZstdError(msg)

def _set_parameter_error(is_compress, key, value):
    COMPRESS_PARAMETERS = \
    {m.ZSTD_c_compressionLevel: "compressionLevel",
     m.ZSTD_c_windowLog:        "windowLog",
     m.ZSTD_c_hashLog:          "hashLog",
     m.ZSTD_c_chainLog:         "chainLog",
     m.ZSTD_c_searchLog:        "searchLog",
     m.ZSTD_c_minMatch:         "minMatch",
     m.ZSTD_c_targetLength:     "targetLength",
     m.ZSTD_c_strategy:         "strategy",

     m.ZSTD_c_enableLongDistanceMatching: "enableLongDistanceMatching",
     m.ZSTD_c_ldmHashLog:       "ldmHashLog",
     m.ZSTD_c_ldmMinMatch:      "ldmMinMatch",
     m.ZSTD_c_ldmBucketSizeLog: "ldmBucketSizeLog",
     m.ZSTD_c_ldmHashRateLog:   "ldmHashRateLog",

     m.ZSTD_c_contentSizeFlag:  "contentSizeFlag",
     m.ZSTD_c_checksumFlag:     "checksumFlag",
     m.ZSTD_c_dictIDFlag:       "dictIDFlag",

     m.ZSTD_c_nbWorkers:        "nbWorkers",
     m.ZSTD_c_jobSize:          "jobSize",
     m.ZSTD_c_overlapLog:       "overlapLog"}

    DECOMPRESS_PARAMETERS = {m.ZSTD_d_windowLogMax: "windowLogMax"}

    if is_compress:
        parameters = COMPRESS_PARAMETERS
        type_msg = "compression"
    else:
        parameters = DECOMPRESS_PARAMETERS
        type_msg = "decompression"

    # Find parameter's name
    name = parameters.get(key)
    # Unknown parameter
    if name is None:
        name = 'unknown parameter (key %d)' % key

    # Get parameter bounds
    if is_compress:
        bounds = m.ZSTD_cParam_getBounds(key)
    else:
        bounds = m.ZSTD_dParam_getBounds(key)
    if m.ZSTD_isError(bounds.error):
        msg = 'Zstd %s parameter "%s" is invalid. (zstd v%s)' % \
              (type_msg, name, zstd_version)
        raise ZstdError(msg)

    # Error message
    msg = ('Error when setting zstd %s parameter "%s", it '
           'should %d <= value <= %d, provided value is %d. '
           '(zstd v%s, %d-bit build)') % \
          (type_msg, name,
           bounds.lowerBound, bounds.upperBound, value,
           zstd_version, PYZSTD_CONFIG[0])
    raise ZstdError(msg)

def _check_int32_value(value, name):
    try:
        if value > 2147483647 or value < -2147483648:
            raise Exception
    except:
        raise ValueError("%s should be 32-bit signed int value." % name)

# return: (compressionLevel, use_multithread)
def _set_c_parameters(cctx, level_or_option):
    if isinstance(level_or_option, int):
        _check_int32_value(level_or_option, "Compression level")

        # Set compression level
        zstd_ret = m.ZSTD_CCtx_setParameter(cctx, m.ZSTD_c_compressionLevel,
                                            level_or_option)
        if m.ZSTD_isError(zstd_ret):
            _set_zstd_error(_ErrorType.ERR_SET_C_LEVEL, zstd_ret)

        return level_or_option, False

    if isinstance(level_or_option, dict):
        level = 0  # 0 means use zstd's default compression level
        use_multithread = False

        for key, value in level_or_option.items():
            # Check key type
            if type(key) == DParameter:
                raise TypeError("Key of compression option dict should "
                                "NOT be DParameter.")

            # Both key & value should be 32-bit signed int
            _check_int32_value(key, "Key of option dict")
            _check_int32_value(value, "Value of option dict")

            if key == m.ZSTD_c_compressionLevel:
                level = value
            elif key == m.ZSTD_c_nbWorkers:
                if value != 0:
                    use_multithread = True

            # Set parameter
            zstd_ret = m.ZSTD_CCtx_setParameter(cctx, key, value)
            if m.ZSTD_isError(zstd_ret):
                _set_parameter_error(True, key, value)

        return level, use_multithread

    raise TypeError("level_or_option argument wrong type.")

def _set_d_parameters(dctx, option):
    if not isinstance(option, dict):
        raise TypeError("option argument should be dict object.")

    for key, value in option.items():
        # Check key type
        if type(key) == CParameter:
            raise TypeError("Key of decompression option dict should "
                            "NOT be CParameter.")

        # Both key & value should be 32-bit signed int
        _check_int32_value(key, "Key of option dict")
        _check_int32_value(value, "Value of option dict")

        # Set parameter
        zstd_ret = m.ZSTD_DCtx_setParameter(dctx, key, value)
        if m.ZSTD_isError(zstd_ret):
            _set_parameter_error(False, key, value)

# Write output data to fp.
# If (out_b.pos == 0), do nothing.
def _write_to_fp(func_name, fp, out_mv, out_b):
    if out_b.pos == 0:
        return

    write_ret = fp.write(out_mv[:out_b.pos])
    if write_ret != out_b.pos:
        msg = ("%s returned invalid length %d "
               "(should be %d <= value <= %d)") % \
               (func_name, write_ret, out_b.pos, out_b.pos)
        raise ValueError(msg)

def _train_dict(samples_bytes, samples_size_list, dict_size):
    # C code
    if dict_size <= 0:
        raise ValueError("dict_size argument should be positive number.")

    # Prepare chunk_sizes
    _chunks_number = len(samples_size_list)
    _sizes = _new_nonzero("size_t[]", _chunks_number)
    if _sizes == ffi.NULL:
        raise MemoryError

    _sizes_sum = 0
    for i, size in enumerate(samples_size_list):
        _sizes[i] = size
        _sizes_sum += size

    if _sizes_sum != _nbytes(samples_bytes):
        msg = "The samples size list doesn't match the concatenation's size."
        raise ValueError(msg)

    # Allocate dict buffer
    _dst_dict_bytes = _new_nonzero("char[]", dict_size)
    if _dst_dict_bytes == ffi.NULL:
        raise MemoryError

    # Train
    zstd_ret = m.ZDICT_trainFromBuffer(_dst_dict_bytes, dict_size,
                                       ffi.from_buffer(samples_bytes),
                                       _sizes, _chunks_number)
    if m.ZDICT_isError(zstd_ret):
        _set_zstd_error(_ErrorType.ERR_TRAIN_DICT, zstd_ret)

    # Resize dict_buffer
    b = ffi.buffer(_dst_dict_bytes)[:zstd_ret]
    return b

def _finalize_dict(custom_dict_bytes,
                   samples_bytes, samples_size_list,
                   dict_size, compression_level):
    # If m.ZSTD_VERSION_NUMBER < 10405, m.ZDICT_finalizeDictionary() is an
    # empty function defined in build_cffi.py.
    # If m.ZSTD_versionNumber() < 10405, m.ZDICT_finalizeDictionary() doesn't
    # exist in run-time zstd library.
    if (m.ZSTD_VERSION_NUMBER < 10405          # compile-time version
          or m.ZSTD_versionNumber() < 10405):  # run-time version
        msg = ("finalize_dict function only available when the underlying "
               "zstd library's version is greater than or equal to v1.4.5. "
               "At pyzstd module's compile-time, zstd version is %d. At "
               "pyzstd module's run-time, zstd version is %d.") % \
               (m.ZSTD_VERSION_NUMBER, m.ZSTD_versionNumber())
        raise NotImplementedError(msg)

    # C code
    if dict_size <= 0:
        raise ValueError("dict_size argument should be positive number.")

    # Prepare chunk_sizes
    _chunks_number = len(samples_size_list)
    _sizes = _new_nonzero("size_t[]", _chunks_number)
    if _sizes == ffi.NULL:
        raise MemoryError

    _sizes_sum = 0
    for i, size in enumerate(samples_size_list):
        _sizes[i] = size
        _sizes_sum += size

    if _sizes_sum != _nbytes(samples_bytes):
        msg = "The samples size list doesn't match the concatenation's size."
        raise ValueError(msg)

    # Allocate dict buffer
    _dst_dict_bytes = _new_nonzero("char[]", dict_size)
    if _dst_dict_bytes == ffi.NULL:
        raise MemoryError

    # Parameters
    params = _new_nonzero("ZDICT_params_t *")
    if params == ffi.NULL:
        raise MemoryError
    # Optimize for a specific zstd compression level, 0 means default.
    params.compressionLevel = compression_level
    # Write log to stderr, 0 = none.
    params.notificationLevel = 0
    # Force dictID value, 0 means auto mode (32-bits random value).
    params.dictID = 0

    # Finalize
    zstd_ret = m.ZDICT_finalizeDictionary(
                   _dst_dict_bytes, dict_size,
                   ffi.from_buffer(custom_dict_bytes), _nbytes(custom_dict_bytes),
                   ffi.from_buffer(samples_bytes), _sizes, _chunks_number,
                   params[0])
    if m.ZDICT_isError(zstd_ret):
        _set_zstd_error(_ErrorType.ERR_FINALIZE_DICT, zstd_ret)

    # Resize dict_buffer
    b = ffi.buffer(_dst_dict_bytes)[:zstd_ret]
    return b

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

    It's possible to append more items to the namedtuple in the future.
    """

    decompressed_size = m.ZSTD_getFrameContentSize(
                            ffi.from_buffer(frame_buffer), len(frame_buffer))
    if decompressed_size == m.ZSTD_CONTENTSIZE_UNKNOWN:
        decompressed_size = None
    elif decompressed_size == m.ZSTD_CONTENTSIZE_ERROR:
        msg = ("Error when getting information from the header of "
               "a zstd frame. Make sure the frame_buffer argument "
               "starts from the beginning of a frame, and its length "
               "not less than the frame header (6~18 bytes).")
        raise ZstdError(msg)

    dict_id = m.ZSTD_getDictID_fromFrame(
                  ffi.from_buffer(frame_buffer), len(frame_buffer))

    ret = _nt_frame_info(decompressed_size, dict_id)
    return ret

def get_frame_size(frame_buffer):
    """Get the size of a zstd frame, including frame header and 4-byte checksum if it
    has.

    It will iterate all blocks' header within a frame, to accumulate the frame size.

    Parameter
    frame_buffer: A bytes-like object, it should starts from the beginning of a
                  frame, and contains at least one complete frame.
    """

    frame_size = m.ZSTD_findFrameCompressedSize(
                     ffi.from_buffer(frame_buffer), len(frame_buffer))
    if m.ZSTD_isError(frame_size):
        msg = ("Error when finding the compressed size of a zstd frame. "
               "Make sure the frame_buffer argument starts from the "
               "beginning of a frame, and its length not less than this "
               "complete frame. Zstd error message: %s.") % \
               ffi.string(m.ZSTD_getErrorName(frame_size)).decode('utf-8')
        raise ZstdError(msg)

    return frame_size
