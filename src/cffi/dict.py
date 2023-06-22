from threading import Lock

from .common import m, ffi, ZstdError, \
                    _set_zstd_error, _ErrorType

_DICT_TYPE_DIGESTED   = 0
_DICT_TYPE_UNDIGESTED = 1
_DICT_TYPE_PREFIX     = 2

class ZstdDict:
    """Zstd dictionary, used for compression/decompression."""

    def __init__(self, dict_content, is_raw=False):
        """Initialize a ZstdDict object.

        Parameters
        dict_content: A bytes-like object, dictionary's content.
        is_raw:       This parameter is for advanced user. True means dict_content
                      argument is a "raw content" dictionary, free of any format
                      restriction. False means dict_content argument is an ordinary
                      zstd dictionary, was created by zstd functions, follow a
                      specified format.
        """
        self.__cdicts = {}
        self.__ddict = ffi.NULL
        self.__lock = Lock()

        # Check dict_content's type
        try:
            self.__dict_content = bytes(dict_content)
        except:
            raise TypeError("dict_content argument should be bytes-like object.")

        # Both ordinary dictionary and "raw content" dictionary should
        # at least 8 bytes
        if len(self.__dict_content) < 8:
            raise ValueError('Zstd dictionary content should at least 8 bytes.')

        # Get dict_id, 0 means "raw content" dictionary.
        self.__dict_id = m.ZSTD_getDictID_fromDict(
                                    ffi.from_buffer(self.__dict_content),
                                    len(self.__dict_content))

        # Check validity for ordinary dictionary
        if not is_raw and self.__dict_id == 0:
            msg = ('The dict_content argument is not a valid zstd '
                   'dictionary. The first 4 bytes of a valid zstd dictionary '
                   'should be a magic number: b"\\x37\\xA4\\x30\\xEC".\n'
                   'If you are an advanced user, and can be sure that '
                   'dict_content argument is a "raw content" zstd '
                   'dictionary, set is_raw parameter to True.')
            raise ValueError(msg)

    def __del__(self):
        try:
            for level, cdict in self.__cdicts.items():
                m.ZSTD_freeCDict(cdict)
                self.__cdicts[level] = ffi.NULL
        except AttributeError:
            pass

        try:
            m.ZSTD_freeDDict(self.__ddict)
            self.__ddict = ffi.NULL
        except AttributeError:
            pass

    @property
    def dict_content(self):
        """The content of zstd dictionary, a bytes object, it's the same as dict_content
        argument in ZstdDict.__init__() method. It can be used with other programs.
        """
        return self.__dict_content

    @property
    def dict_id(self):
        """ID of zstd dictionary, a 32-bit unsigned int value.

        Non-zero means ordinary dictionary, was created by zstd functions, follow
        a specified format.

        0 means a "raw content" dictionary, free of any format restriction, used
        for advanced user.
        """
        return self.__dict_id

    @property
    def as_digested_dict(self):
        """Load as a digested dictionary to compressor, by passing this attribute as
        zstd_dict argument: compress(dat, zstd_dict=zd.as_digested_dict)
        1, Some advanced compression parameters of compressor may be overridden
           by parameters of digested dictionary.
        2, ZstdDict has a digested dictionaries cache for each compression level.
           It's faster when loading again a digested dictionary with the same
           compression level.
        3, No need to use this for decompression.
        """
        return (self, _DICT_TYPE_DIGESTED)

    @property
    def as_undigested_dict(self):
        """Load as an undigested dictionary to compressor, by passing this attribute as
        zstd_dict argument: compress(dat, zstd_dict=zd.as_undigested_dict)
        1, The advanced compression parameters of compressor will not be overridden.
        2, Loading an undigested dictionary is costly. If load an undigested dictionary
           multiple times, consider reusing a compressor object.
        3, No need to use this for decompression.
        """
        return (self, _DICT_TYPE_UNDIGESTED)

    @property
    def as_prefix(self):
        """Load as a prefix to compressor/decompressor, by passing this attribute as
        zstd_dict argument: compress(dat, zstd_dict=zd.as_prefix)
        1, Prefix is compatible with long distance matching, while dictionary is not.
        2, It only works for the first frame, then the compressor/decompressor will
           return to no prefix state.
        3, When decompressing, must use the same prefix as when compressing.
        """
        return (self, _DICT_TYPE_PREFIX)

    def __str__(self):
        return '<ZstdDict dict_id=%d dict_size=%d>' % \
               (self.__dict_id, len(self.__dict_content))

    def __len__(self):
        return len(self.__dict_content)

    def __reduce__(self):
        msg = ("ZstdDict object intentionally doesn't support pickle. If need "
               "to save zstd dictionary to disk, please save .dict_content "
               "attribute, it's a bytes object. So that the zstd dictionary "
               "can be used with other programs.")
        raise TypeError(msg)

    def _get_cdict(self, level):
        with self.__lock:
            # Already cached
            if level in self.__cdicts:
                cdict = self.__cdicts[level]
            else:
                # Create ZSTD_CDict instance
                cdict = m.ZSTD_createCDict(ffi.from_buffer(self.__dict_content),
                                           len(self.__dict_content), level)
                if cdict == ffi.NULL:
                    msg = ("Failed to create ZSTD_CDict instance from zstd "
                           "dictionary content. Maybe the content is corrupted.")
                    raise ZstdError(msg)
                self.__cdicts[level] = cdict
            return cdict

    def _get_ddict(self):
        # Already created
        if self.__ddict != ffi.NULL:
            return self.__ddict

        with self.__lock:
            # Create ZSTD_DDict instance from dictionary content
            self.__ddict = m.ZSTD_createDDict(
                                    ffi.from_buffer(self.__dict_content),
                                    len(self.__dict_content))

            if self.__ddict == ffi.NULL:
                msg = ("Failed to create ZSTD_DDict instance from zstd "
                       "dictionary content. Maybe the content is corrupted.")
                raise ZstdError(msg)

            return self.__ddict

def _load_c_dict(cctx, zstd_dict, level):
    if isinstance(zstd_dict, ZstdDict):
        # When compressing, use undigested dictionary by default.
        zd = zstd_dict
        type = _DICT_TYPE_UNDIGESTED
    elif isinstance(zstd_dict, tuple) and \
         len(zstd_dict) == 2 and \
         isinstance(zstd_dict[0], ZstdDict) and \
         zstd_dict[1] in {_DICT_TYPE_DIGESTED,
                          _DICT_TYPE_UNDIGESTED,
                          _DICT_TYPE_PREFIX}:
        zd = zstd_dict[0]
        type = zstd_dict[1]
    else:
        raise TypeError("zstd_dict argument should be ZstdDict object.")

    if type == _DICT_TYPE_DIGESTED:
        # Get ZSTD_CDict
        c_dict = zd._get_cdict(level)
        # Reference a prepared dictionary.
        # It overrides some compression context's parameters.
        zstd_ret = m.ZSTD_CCtx_refCDict(cctx, c_dict)
    elif type == _DICT_TYPE_UNDIGESTED:
        # Load a dictionary.
        # It doesn't override compression context's parameters.
        zstd_ret = m.ZSTD_CCtx_loadDictionary(
                                    cctx,
                                    ffi.from_buffer(zd.dict_content),
                                    len(zd.dict_content))
    elif type == _DICT_TYPE_PREFIX:
        # Reference as prefix
        zstd_ret = m.ZSTD_CCtx_refPrefix(
                                    cctx,
                                    ffi.from_buffer(zd.dict_content),
                                    len(zd.dict_content))
    else:
        raise SystemError('_load_c_dict() impossible code path')

    if m.ZSTD_isError(zstd_ret):
        _set_zstd_error(_ErrorType.ERR_LOAD_C_DICT, zstd_ret)

def _load_d_dict(dctx, zstd_dict):
    if isinstance(zstd_dict, ZstdDict):
        # When decompressing, use digested dictionary by default.
        zd = zstd_dict
        type = _DICT_TYPE_DIGESTED
    elif isinstance(zstd_dict, tuple) and \
         len(zstd_dict) == 2 and \
         isinstance(zstd_dict[0], ZstdDict) and \
         zstd_dict[1] in {_DICT_TYPE_DIGESTED,
                          _DICT_TYPE_UNDIGESTED,
                          _DICT_TYPE_PREFIX}:
        zd = zstd_dict[0]
        type = zstd_dict[1]
    else:
        raise TypeError("zstd_dict argument should be ZstdDict object.")

    if type == _DICT_TYPE_DIGESTED:
        # Get ZSTD_DDict
        d_dict = zd._get_ddict()
        # Reference a prepared dictionary
        zstd_ret = m.ZSTD_DCtx_refDDict(dctx, d_dict)
    elif type == _DICT_TYPE_UNDIGESTED:
        # Load a dictionary
        zstd_ret = m.ZSTD_DCtx_loadDictionary(
                                    dctx,
                                    ffi.from_buffer(zd.dict_content),
                                    len(zd.dict_content))
    elif type == _DICT_TYPE_PREFIX:
        # Reference as prefix
        zstd_ret = m.ZSTD_DCtx_refPrefix(
                                    dctx,
                                    ffi.from_buffer(zd.dict_content),
                                    len(zd.dict_content))
    else:
        raise SystemError('_load_d_dict() impossible code path')

    if m.ZSTD_isError(zstd_ret):
        _set_zstd_error(_ErrorType.ERR_LOAD_D_DICT, zstd_ret)
