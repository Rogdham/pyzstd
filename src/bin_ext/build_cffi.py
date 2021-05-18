try:
    from cffi import FFI
except ImportError:
    # PyPy includes cffi by default
    msg = ('To build the CFFI implementation of pyzstd module, need to '
           'install cffi module like this: "sudo python3 -m pip install '
           'cffi". On CPython, CFFI implementation is slower than C '
           'implementation.')
    raise ImportError(msg)

ffibuilder = FFI()

ffibuilder.cdef("""
#define ZSTD_VERSION_NUMBER ...
#define ZSTD_CONTENTSIZE_UNKNOWN ...
#define ZSTD_CONTENTSIZE_ERROR ...

typedef ... ZSTD_CDict;
typedef ... ZSTD_DDict;
typedef ... ZSTD_CCtx;
typedef ... ZSTD_DCtx;

typedef struct {
    size_t error;
    int lowerBound;
    int upperBound;
} ZSTD_bounds;

typedef enum {
    ZSTD_e_continue,
    ZSTD_e_flush,
    ZSTD_e_end
} ZSTD_EndDirective;

typedef struct ZSTD_inBuffer_s {
  const void* src;
  size_t size;
  size_t pos;
} ZSTD_inBuffer;

typedef struct ZSTD_outBuffer_s {
  void*  dst;
  size_t size;
  size_t pos;
} ZSTD_outBuffer;

typedef enum {
    /* Compression parameters */
    ZSTD_c_compressionLevel,

    /* Advanced compression parameters */
    ZSTD_c_windowLog,
    ZSTD_c_hashLog,
    ZSTD_c_chainLog,
    ZSTD_c_searchLog,
    ZSTD_c_minMatch,
    ZSTD_c_targetLength,
    ZSTD_c_strategy,

    /* LDM mode parameters */
    ZSTD_c_enableLongDistanceMatching,
    ZSTD_c_ldmHashLog,
    ZSTD_c_ldmMinMatch,
    ZSTD_c_ldmBucketSizeLog,
    ZSTD_c_ldmHashRateLog,

    /* frame parameters */
    ZSTD_c_contentSizeFlag,
    ZSTD_c_checksumFlag,
    ZSTD_c_dictIDFlag,

    /* multi-threading parameters */
    ZSTD_c_nbWorkers,
    ZSTD_c_jobSize,
    ZSTD_c_overlapLog,
} ZSTD_cParameter;

typedef enum {
    ZSTD_d_windowLogMax,
} ZSTD_dParameter;

typedef enum {
    ZSTD_fast,
    ZSTD_dfast,
    ZSTD_greedy,
    ZSTD_lazy,
    ZSTD_lazy2,
    ZSTD_btlazy2,
    ZSTD_btopt,
    ZSTD_btultra,
    ZSTD_btultra2
} ZSTD_strategy;

typedef enum {
    ZSTD_reset_session_only,
    ...
} ZSTD_ResetDirective;

size_t      ZSTD_compressBound(size_t srcSize);
unsigned    ZSTD_isError(size_t code);
const char* ZSTD_getErrorName(size_t code);
int         ZSTD_minCLevel(void);
int         ZSTD_maxCLevel(void);
int         ZSTD_defaultCLevel(void);

unsigned long long ZSTD_getFrameContentSize(const void *src, size_t srcSize);
unsigned ZSTD_getDictID_fromFrame(const void* src, size_t srcSize);
size_t ZSTD_findFrameCompressedSize(const void* src, size_t srcSize);

unsigned ZSTD_versionNumber(void);
const char* ZSTD_versionString(void);

ZSTD_bounds ZSTD_cParam_getBounds(ZSTD_cParameter cParam);
ZSTD_bounds ZSTD_dParam_getBounds(ZSTD_dParameter dParam);
size_t ZSTD_CCtx_setParameter(ZSTD_CCtx* cctx, ZSTD_cParameter param, int value);
size_t ZSTD_DCtx_setParameter(ZSTD_DCtx* dctx, ZSTD_dParameter param, int value);

size_t ZSTD_CStreamInSize(void);
size_t ZSTD_CStreamOutSize(void);
size_t ZSTD_DStreamInSize(void);
size_t ZSTD_DStreamOutSize(void);

ZSTD_CDict* ZSTD_createCDict(const void* dictBuffer, size_t dictSize,
                             int compressionLevel);
size_t ZSTD_CCtx_refCDict(ZSTD_CCtx* cctx, const ZSTD_CDict* cdict);
size_t      ZSTD_freeCDict(ZSTD_CDict* CDict);
size_t ZSTD_sizeof_CDict(const ZSTD_CDict* cdict);

ZSTD_DDict* ZSTD_createDDict(const void* dictBuffer, size_t dictSize);
size_t ZSTD_DCtx_refDDict(ZSTD_DCtx* dctx, const ZSTD_DDict* ddict);
size_t      ZSTD_freeDDict(ZSTD_DDict* ddict);
size_t ZSTD_sizeof_DDict(const ZSTD_DDict* ddict);
unsigned ZSTD_getDictID_fromDDict(const ZSTD_DDict* ddict);

ZSTD_CCtx* ZSTD_createCCtx(void);
size_t     ZSTD_freeCCtx(ZSTD_CCtx* cctx);
size_t ZSTD_CCtx_reset(ZSTD_CCtx* cctx, ZSTD_ResetDirective reset);
size_t ZSTD_sizeof_CCtx(const ZSTD_CCtx* cctx);
size_t ZSTD_CCtx_setPledgedSrcSize(ZSTD_CCtx* cctx, unsigned long long pledgedSrcSize);
size_t ZSTD_compressStream2(ZSTD_CCtx* cctx,
                            ZSTD_outBuffer* output,
                            ZSTD_inBuffer* input,
                            ZSTD_EndDirective endOp);

ZSTD_DCtx* ZSTD_createDCtx(void);
size_t     ZSTD_freeDCtx(ZSTD_DCtx* dctx);
size_t ZSTD_DCtx_reset(ZSTD_DCtx* dctx, ZSTD_ResetDirective reset);
size_t ZSTD_sizeof_DCtx(const ZSTD_DCtx* dctx);
size_t ZSTD_decompressStream(ZSTD_DCtx* dctx,
                             ZSTD_outBuffer* output,
                             ZSTD_inBuffer* input);

unsigned ZDICT_isError(size_t errorCode);
size_t ZDICT_trainFromBuffer(void* dictBuffer, size_t dictBufferCapacity,
                             const void* samplesBuffer,
                             const size_t* samplesSizes, unsigned nbSamples);


typedef struct {
    int      compressionLevel;
    unsigned notificationLevel;
    unsigned dictID;
} ZDICT_params_t;

size_t ZDICT_finalizeDictionary(void* dstDictBuffer, size_t maxDictSize,
                                const void* dictContent, size_t dictContentSize,
                                const void* samplesBuffer, const size_t* samplesSizes, unsigned nbSamples,
                                ZDICT_params_t parameters);
""")

source = """
#include "zstd.h"
#include "zdict.h"

#if ZSTD_VERSION_NUMBER < 10400
    #error "pyzstd module requires zstd v1.4.0+"
#endif

#if ZSTD_VERSION_NUMBER < 10405
typedef struct {
    int      compressionLevel;
    unsigned notificationLevel;
    unsigned dictID;
} ZDICT_params_t;

size_t ZDICT_finalizeDictionary(void* dstDictBuffer, size_t maxDictSize,
                                const void* dictContent, size_t dictContentSize,
                                const void* samplesBuffer, const size_t* samplesSizes, unsigned nbSamples,
                                ZDICT_params_t parameters)
{
    return 0;
}
#endif

#if ZSTD_VERSION_NUMBER < 10500
int ZSTD_defaultCLevel(void)
{
    return ZSTD_CLEVEL_DEFAULT;
}
#endif
"""

def set_kwargs(**kwargs):
    ffibuilder.set_source(source=source, **kwargs)
