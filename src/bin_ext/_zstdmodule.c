/* pyzstd module for Python 3.5+
   https://github.com/animalize/pyzstd */

#include "stdint.h"     /* For MSVC + Python 3.5 */

#include "Python.h"
#include "pythread.h"   /* For Python 3.5 */
#include "structmember.h"

#include "zstd.h"
#include "zdict.h"

#if ZSTD_VERSION_NUMBER < 10400
    #error "pyzstd module requires zstd v1.4.0+"
#endif

#ifndef Py_UNREACHABLE
    #define Py_UNREACHABLE() assert(0)
#endif

typedef struct {
    PyObject_HEAD

    /* Content of the dictionary, bytes object. */
    PyObject *dict_content;
    /* Dictionary id */
    uint32_t dict_id;

    /* Reuseable compress/decompress dictionary, they are created once and
       can be shared by multiple threads concurrently, since its usage is
       read-only.
       c_dicts is a dict, int(compressionLevel):PyCapsule(ZSTD_CDict*) */
    PyObject *c_dicts;
    ZSTD_DDict *d_dict;

    /* Thread lock for generating ZSTD_CDict */
    PyThread_type_lock lock;

    /* __init__ has been called, 0 or 1. */
    char inited;
} ZstdDict;

typedef struct {
    PyObject_HEAD

    /* Compression context */
    ZSTD_CCtx *cctx;

    /* ZstdDict object in use */
    PyObject *dict;

    /* Last mode, initialized to ZSTD_e_end */
    int last_mode;

    /* Thread lock for compressing */
    PyThread_type_lock lock;

    /* Enabled zstd multi-threaded compression, 0 or 1. */
    char use_multithreaded;

    /* __init__ has been called, 0 or 1. */
    char inited;
} ZstdCompressor;

typedef struct {
    PyObject_HEAD

    /* Decompression context */
    ZSTD_DCtx *dctx;

    /* ZstdDict object in use */
    PyObject *dict;

    /* Unconsumed input data */
    char *input_buffer;
    size_t input_buffer_size;
    size_t in_begin, in_end;

    /* Thread lock for compressing */
    PyThread_type_lock lock;

    /* Unused data */
    PyObject *unused_data;

    /* 0 if decompressor has (or may has) unconsumed input data, 0 or 1. */
    char needs_input;

    /* For EndlessZstdDecomprssor, 0 or 1.
       1 when both input and output streams are at a frame edge, means a
       frame is completely decoded and fully flushed, or the decompressor
       just be initialized. */
    char at_frame_edge;

    /* For ZstdDecomprssor, 0 or 1.
       1 means the end of the first frame has been reached. */
    char eof;

    /* __init__ has been called, 0 or 1. */
    char inited;
} ZstdDecompressor;

typedef struct {
    PyTypeObject *ZstdDict_type;
    PyTypeObject *ZstdCompressor_type;
    PyTypeObject *RichMemZstdCompressor_type;
    PyTypeObject *ZstdDecompressor_type;
    PyTypeObject *EndlessZstdDecompressor_type;
    PyObject *ZstdError;
    PyObject *empty_bytes;
    PyObject *empty_readonly_memoryview;
    PyObject *str_readinto;
    PyObject *str_write;
    int support_multithreaded;
} _zstd_state;

static _zstd_state static_state;

/* ----------------------------
     BlocksOutputBuffer code
   ---------------------------- */
typedef struct {
    /* List of blocks */
    PyObject *list;
    /* Number of whole allocated size. */
    Py_ssize_t allocated;
    /* Max length of the buffer, negative number for unlimited length. */
    Py_ssize_t max_length;
} BlocksOutputBuffer;

static const char unable_allocate_msg[] = "Unable to allocate output buffer.";

/* Block size sequence. */
#define KB (1024)
#define MB (1024*1024)
static const Py_ssize_t BUFFER_BLOCK_SIZE[] =
    /* If change this list, also change:
         The CFFI implementation
         OutputBufferTestCase unittest
       If change the first blocks's size, also change:
         ZstdDecompressReader.seek() method
         ZstdFile.__init__() method
         ZstdFile.read1() method
         FileTestCase.test_decompress_limited() test */
    { 32*KB, 64*KB, 256*KB, 1*MB, 4*MB, 8*MB, 16*MB, 16*MB,
      32*MB, 32*MB, 32*MB, 32*MB, 64*MB, 64*MB, 128*MB, 128*MB,
      256*MB };

/* According to the block sizes defined by BUFFER_BLOCK_SIZE, the whole
   allocated size growth step is:
    1   32 KB       +32 KB
    2   96 KB       +64 KB
    3   352 KB      +256 KB
    4   1.34 MB     +1 MB
    5   5.34 MB     +4 MB
    6   13.34 MB    +8 MB
    7   29.34 MB    +16 MB
    8   45.34 MB    +16 MB
    9   77.34 MB    +32 MB
    10  109.34 MB   +32 MB
    11  141.34 MB   +32 MB
    12  173.34 MB   +32 MB
    13  237.34 MB   +64 MB
    14  301.34 MB   +64 MB
    15  429.34 MB   +128 MB
    16  557.34 MB   +128 MB
    17  813.34 MB   +256 MB
    18  1069.34 MB  +256 MB
    19  1325.34 MB  +256 MB
    20  1581.34 MB  +256 MB
    21  1837.34 MB  +256 MB
    22  2093.34 MB  +256 MB
    ...
*/

/* Initialize the buffer, and grow the buffer.
   max_length: Max length of the buffer, -1 for unlimited length.
   Return 0 on success
   Return -1 on failure
*/
static inline int
OutputBuffer_InitAndGrow(BlocksOutputBuffer *buffer, ZSTD_outBuffer *ob,
                         const Py_ssize_t max_length)
{
    PyObject *b;
    Py_ssize_t block_size;

    /* Ensure .list was set to NULL */
    assert(buffer->list == NULL);

    /* Get block size */
    if (0 <= max_length && max_length < BUFFER_BLOCK_SIZE[0]) {
        block_size = max_length;
    } else {
        block_size = BUFFER_BLOCK_SIZE[0];
    }

    /* The first block */
    b = PyBytes_FromStringAndSize(NULL, block_size);
    if (b == NULL) {
        return -1;
    }

    /* Create the list */
    buffer->list = PyList_New(1);
    if (buffer->list == NULL) {
        Py_DECREF(b);
        return -1;
    }
    PyList_SET_ITEM(buffer->list, 0, b);

    /* Set variables */
    buffer->allocated = block_size;
    buffer->max_length = max_length;

    ob->dst = PyBytes_AS_STRING(b);
    ob->size = (size_t) block_size;
    ob->pos = 0;
    return 0;
}

/* Initialize the buffer, with an initial size.
   init_size: the initial size.
   Return 0 on success
   Return -1 on failure
*/
static inline int
OutputBuffer_InitWithSize(BlocksOutputBuffer *buffer, ZSTD_outBuffer *ob,
                          const Py_ssize_t max_length,
                          const Py_ssize_t init_size)
{
    PyObject *b;
    Py_ssize_t block_size;

    /* Ensure .list was set to NULL */
    assert(buffer->list == NULL);

    /* Get block size */
    if (max_length >= 0) {
        block_size = Py_MIN(max_length, init_size);
    } else {
        block_size = init_size;
    }

    /* The first block */
    b = PyBytes_FromStringAndSize(NULL, block_size);
    if (b == NULL) {
        PyErr_SetString(PyExc_MemoryError, unable_allocate_msg);
        return -1;
    }

    /* Create the list */
    buffer->list = PyList_New(1);
    if (buffer->list == NULL) {
        Py_DECREF(b);
        return -1;
    }
    PyList_SET_ITEM(buffer->list, 0, b);

    /* Set variables */
    buffer->allocated = block_size;
    buffer->max_length = max_length;

    ob->dst = PyBytes_AS_STRING(b);
    ob->size = (size_t) block_size;
    ob->pos = 0;
    return 0;
}

/* Grow the buffer. The avail_out must be 0, please check it before calling.
   Return 0 on success
   Return -1 on failure
*/
static inline int
OutputBuffer_Grow(BlocksOutputBuffer *buffer, ZSTD_outBuffer *ob)
{
    PyObject *b;
    const Py_ssize_t list_len = Py_SIZE(buffer->list);
    Py_ssize_t block_size;

    /* Ensure no gaps in the data */
    assert(ob->pos == ob->size);

    /* Get block size */
    if (list_len < (Py_ssize_t) Py_ARRAY_LENGTH(BUFFER_BLOCK_SIZE)) {
        block_size = BUFFER_BLOCK_SIZE[list_len];
    } else {
        block_size = BUFFER_BLOCK_SIZE[Py_ARRAY_LENGTH(BUFFER_BLOCK_SIZE) - 1];
    }

    /* Check max_length */
    if (buffer->max_length >= 0) {
        /* If (rest == 0), should not grow the buffer. */
        Py_ssize_t rest = buffer->max_length - buffer->allocated;
        assert(rest > 0);

        /* block_size of the last block */
        if (block_size > rest) {
            block_size = rest;
        }
    }

    /* Check buffer->allocated overflow */
    if (block_size > PY_SSIZE_T_MAX - buffer->allocated) {
        PyErr_SetString(PyExc_MemoryError, unable_allocate_msg);
        return -1;
    }

    /* Create the block */
    b = PyBytes_FromStringAndSize(NULL, block_size);
    if (b == NULL) {
        PyErr_SetString(PyExc_MemoryError, unable_allocate_msg);
        return -1;
    }
    if (PyList_Append(buffer->list, b) < 0) {
        Py_DECREF(b);
        return -1;
    }
    Py_DECREF(b);

    /* Set variables */
    buffer->allocated += block_size;

    ob->dst = PyBytes_AS_STRING(b);
    ob->size = (size_t) block_size;
    ob->pos = 0;
    return 0;
}

/* Whether the output data has reached max_length.
   The avail_out must be 0, please check it before calling. */
static inline int
OutputBuffer_ReachedMaxLength(BlocksOutputBuffer *buffer, ZSTD_outBuffer *ob)
{
    /* Ensure (data size == allocated size) */
    assert(ob->pos == ob->size);

    return buffer->allocated == buffer->max_length;
}

/* Finish the buffer.
   Return a bytes object on success
   Return NULL on failure
*/
static inline PyObject *
OutputBuffer_Finish(BlocksOutputBuffer *buffer, ZSTD_outBuffer *ob)
{
    PyObject *result, *block;
    const Py_ssize_t list_len = Py_SIZE(buffer->list);

    /* Fast path for single block */
    if ((list_len == 1 && ob->pos == ob->size) ||
        (list_len == 2 && ob->pos == 0))
    {
        block = PyList_GET_ITEM(buffer->list, 0);
        Py_INCREF(block);

        Py_CLEAR(buffer->list);
        return block;
    }

    /* Final bytes object */
    result = PyBytes_FromStringAndSize(NULL, buffer->allocated - (ob->size - ob->pos));
    if (result == NULL) {
        PyErr_SetString(PyExc_MemoryError, unable_allocate_msg);
        return NULL;
    }

    /* Memory copy */
    if (list_len > 0) {
        char *posi = PyBytes_AS_STRING(result);

        /* Blocks except the last one */
        Py_ssize_t i = 0;
        for (; i < list_len-1; i++) {
            block = PyList_GET_ITEM(buffer->list, i);
            memcpy(posi, PyBytes_AS_STRING(block), Py_SIZE(block));
            posi += Py_SIZE(block);
        }
        /* The last block */
        block = PyList_GET_ITEM(buffer->list, i);
        memcpy(posi, PyBytes_AS_STRING(block), ob->pos);
    } else {
        assert(Py_SIZE(result) == 0);
    }

    Py_CLEAR(buffer->list);
    return result;
}

/* Clean up the buffer */
static inline void
OutputBuffer_OnError(BlocksOutputBuffer *buffer)
{
    Py_CLEAR(buffer->list);
}

/* -------------------------
     Parameters from zstd
   ------------------------- */
typedef struct {
    const int parameter;
    const char parameter_name[32];
} ParameterInfo;

static const ParameterInfo cp_list[] =
{
    {ZSTD_c_compressionLevel, "compressionLevel"},
    {ZSTD_c_windowLog,        "windowLog"},
    {ZSTD_c_hashLog,          "hashLog"},
    {ZSTD_c_chainLog,         "chainLog"},
    {ZSTD_c_searchLog,        "searchLog"},
    {ZSTD_c_minMatch,         "minMatch"},
    {ZSTD_c_targetLength,     "targetLength"},
    {ZSTD_c_strategy,         "strategy"},

    {ZSTD_c_enableLongDistanceMatching, "enableLongDistanceMatching"},
    {ZSTD_c_ldmHashLog,       "ldmHashLog"},
    {ZSTD_c_ldmMinMatch,      "ldmMinMatch"},
    {ZSTD_c_ldmBucketSizeLog, "ldmBucketSizeLog"},
    {ZSTD_c_ldmHashRateLog,   "ldmHashRateLog"},

    {ZSTD_c_contentSizeFlag,  "contentSizeFlag"},
    {ZSTD_c_checksumFlag,     "checksumFlag"},
    {ZSTD_c_dictIDFlag,       "dictIDFlag"},

    {ZSTD_c_nbWorkers,        "nbWorkers"},
    {ZSTD_c_jobSize,          "jobSize"},
    {ZSTD_c_overlapLog,       "overlapLog"}
};

static const ParameterInfo dp_list[] =
{
    {ZSTD_d_windowLogMax, "windowLogMax"}
};

/* Format an user friendly error message. */
static void
set_parameter_error(int is_compress, Py_ssize_t pos, int key_v, int value_v)
{
    ParameterInfo const *list;
    int list_size;
    char const *name;
    char *type;
    ZSTD_bounds bounds;
    int i;

    if (is_compress) {
        list = cp_list;
        list_size = Py_ARRAY_LENGTH(cp_list);
        type = "compression";
    } else {
        list = dp_list;
        list_size = Py_ARRAY_LENGTH(dp_list);
        type = "decompression";
    }

    /* Find parameter's name */
    name = NULL;
    for (i = 0; i < list_size; i++) {
        if (key_v == (list+i)->parameter) {
            name = (list+i)->parameter_name;
            break;
        }
    }

    /* Not a valid parameter */
    if (name == NULL) {
        PyErr_Format(static_state.ZstdError,
                     "The %zdth zstd %s parameter is invalid.",
                     pos, type);
        return;
    }

    /* Get parameter bounds */
    if (is_compress) {
        bounds = ZSTD_cParam_getBounds(key_v);
    } else {
        bounds = ZSTD_dParam_getBounds(key_v);
    }
    if (ZSTD_isError(bounds.error)) {
        PyErr_Format(static_state.ZstdError,
                     "Error when getting bounds of zstd %s parameter \"%s\".",
                     type, name);
        return;
    }

    /* Error message */
    PyErr_Format(static_state.ZstdError,
                 "Error when setting zstd %s parameter \"%s\", it "
                 "should %d <= value <= %d, provided value is %d. "
                 "(zstd v%s, %d-bit build)",
                 type, name,
                 bounds.lowerBound, bounds.upperBound, value_v,
                 ZSTD_versionString(), 8*(int)sizeof(Py_ssize_t));
}

#define ADD_INT_PREFIX_MACRO(module, macro)                  \
    do {                                                     \
        PyObject *o = PyLong_FromLong(macro);                \
        if (PyModule_AddObject(module, "_" #macro, o) < 0) { \
            Py_XDECREF(o);                                   \
            return -1;                                       \
        }                                                    \
    } while(0)

static int
add_parameters(PyObject *module)
{
    /* If add new parameters, please also add to cp_list/dp_list above. */

    /* Compression parameters */
    ADD_INT_PREFIX_MACRO(module, ZSTD_c_compressionLevel);
    ADD_INT_PREFIX_MACRO(module, ZSTD_c_windowLog);
    ADD_INT_PREFIX_MACRO(module, ZSTD_c_hashLog);
    ADD_INT_PREFIX_MACRO(module, ZSTD_c_chainLog);
    ADD_INT_PREFIX_MACRO(module, ZSTD_c_searchLog);
    ADD_INT_PREFIX_MACRO(module, ZSTD_c_minMatch);
    ADD_INT_PREFIX_MACRO(module, ZSTD_c_targetLength);
    ADD_INT_PREFIX_MACRO(module, ZSTD_c_strategy);

    ADD_INT_PREFIX_MACRO(module, ZSTD_c_enableLongDistanceMatching);
    ADD_INT_PREFIX_MACRO(module, ZSTD_c_ldmHashLog);
    ADD_INT_PREFIX_MACRO(module, ZSTD_c_ldmMinMatch);
    ADD_INT_PREFIX_MACRO(module, ZSTD_c_ldmBucketSizeLog);
    ADD_INT_PREFIX_MACRO(module, ZSTD_c_ldmHashRateLog);

    ADD_INT_PREFIX_MACRO(module, ZSTD_c_contentSizeFlag);
    ADD_INT_PREFIX_MACRO(module, ZSTD_c_checksumFlag);
    ADD_INT_PREFIX_MACRO(module, ZSTD_c_dictIDFlag);

    ADD_INT_PREFIX_MACRO(module, ZSTD_c_nbWorkers);
    ADD_INT_PREFIX_MACRO(module, ZSTD_c_jobSize);
    ADD_INT_PREFIX_MACRO(module, ZSTD_c_overlapLog);

    /* Decompression parameters */
    ADD_INT_PREFIX_MACRO(module, ZSTD_d_windowLogMax);

    return 0;
}

/* --------------------------------------
     Global functions/macros
     Set parameters, load dictionary
     ACQUIRE_LOCK, reduce_cannot_pickle
   -------------------------------------- */
#define ACQUIRE_LOCK(obj) do {                    \
    if (!PyThread_acquire_lock((obj)->lock, 0)) { \
        Py_BEGIN_ALLOW_THREADS                    \
        PyThread_acquire_lock((obj)->lock, 1);    \
        Py_END_ALLOW_THREADS                      \
    } } while (0)
#define RELEASE_LOCK(obj) PyThread_release_lock((obj)->lock)

/* Force inlining */
#if defined(__GNUC__) || defined(__ICCARM__)
#  define FORCE_INLINE static inline __attribute__((always_inline))
#elif defined(_MSC_VER)
#  define FORCE_INLINE static inline __forceinline
#else
#  define FORCE_INLINE static inline
#endif

/* Force no inlining */
#ifdef _MSC_VER
#  define FORCE_NO_INLINE static __declspec(noinline)
#else
#  if defined(__GNUC__) || defined(__ICCARM__)
#    define FORCE_NO_INLINE static __attribute__((__noinline__))
#  else
#    define FORCE_NO_INLINE static
#  endif
#endif

static const char init_twice_msg[] = "__init__ method is called twice.";

typedef enum {
    ERR_DECOMPRESS,
    ERR_COMPRESS,

    ERR_LOAD_D_DICT,
    ERR_LOAD_C_DICT,

    ERR_GET_FRAME_SIZE,
    ERR_GET_C_BOUNDS,
    ERR_GET_D_BOUNDS,
    ERR_SET_C_LEVEL,

    ERR_TRAIN_DICT,
    ERR_FINALIZE_DICT
} error_type;

/* The error message of setting parameter is generated by
   get_parameter_error_msg() function. */
FORCE_NO_INLINE void
set_zstd_error(const error_type type, const size_t code)
{
    char buf[128];
    char *type_msg;
    assert(ZSTD_isError(code));

    switch (type)
    {
    case ERR_DECOMPRESS:
        type_msg = "decompress zstd data";
        break;
    case ERR_COMPRESS:
        type_msg = "compress zstd data";
        break;

    case ERR_LOAD_D_DICT:
        type_msg = "load zstd dictionary for decompression";
        break;
    case ERR_LOAD_C_DICT:
        type_msg = "load zstd dictionary for compression";
        break;

    case ERR_GET_FRAME_SIZE:
        type_msg = "get the size of a zstd frame";
        break;
    case ERR_GET_C_BOUNDS:
        type_msg = "get zstd compression parameter bounds";
        break;
    case ERR_GET_D_BOUNDS:
        type_msg = "get zstd decompression parameter bounds";
        break;
    case ERR_SET_C_LEVEL:
        type_msg = "set zstd compression level";
        break;

    case ERR_TRAIN_DICT:
        type_msg = "train zstd dictionary";
        break;
    case ERR_FINALIZE_DICT:
        type_msg = "finalize zstd dictionary";
        break;

    default:
        Py_UNREACHABLE();
    }
    PyOS_snprintf(buf, sizeof(buf), "Unable to %s: %s.",
                  type_msg, ZSTD_getErrorName(code));

    PyErr_SetString(static_state.ZstdError, buf);
}

static void
capsule_free_cdict(PyObject *capsule)
{
    ZSTD_CDict *cdict = PyCapsule_GetPointer(capsule, NULL);
    ZSTD_freeCDict(cdict);
}

static inline ZSTD_CDict *
_get_CDict(ZstdDict *self, int compressionLevel)
{
    PyObject *level = NULL;
    PyObject *capsule;
    ZSTD_CDict *cdict;

    ACQUIRE_LOCK(self);

    /* int level object */
    level = PyLong_FromLong(compressionLevel);
    if (level == NULL) {
        goto error;
    }

    /* Get PyCapsule object from self->c_dicts */
    capsule = PyDict_GetItemWithError(self->c_dicts, level);
    if (capsule == NULL) {
        if (PyErr_Occurred()) {
            goto error;
        }

        /* Create ZSTD_CDict instance */
        Py_BEGIN_ALLOW_THREADS
        cdict = ZSTD_createCDict(PyBytes_AS_STRING(self->dict_content),
                                 Py_SIZE(self->dict_content), compressionLevel);
        Py_END_ALLOW_THREADS

        if (cdict == NULL) {
            PyErr_SetString(static_state.ZstdError,
                            "Failed to get ZSTD_CDict instance from zstd "
                            "dictionary content.");
            goto error;
        }

        /* Put ZSTD_CDict instance into PyCapsule object */
        capsule = PyCapsule_New(cdict, NULL, capsule_free_cdict);
        if (capsule == NULL) {
            ZSTD_freeCDict(cdict);
            goto error;
        }

        /* Add PyCapsule object to self->c_dicts */
        if (PyDict_SetItem(self->c_dicts, level, capsule) < 0) {
            Py_DECREF(capsule);
            goto error;
        }
        Py_DECREF(capsule);
    } else {
        /* ZSTD_CDict instance already exists */
        cdict = PyCapsule_GetPointer(capsule, NULL);
    }
    goto success;

error:
    cdict = NULL;
success:
    Py_XDECREF(level);
    RELEASE_LOCK(self);
    return cdict;
}

static inline ZSTD_DDict *
_get_DDict(ZstdDict *self)
{
    return self->d_dict;
}

static inline void
clamp_compression_level(int *compressionLevel)
{
    /* In zstd v1.4.6-, lower bound is not clamped. */
    if (ZSTD_versionNumber() < 10407) {
        if (*compressionLevel < ZSTD_minCLevel()) {
            *compressionLevel = ZSTD_minCLevel();
        }
    }
}

/* Set compressLevel or compression parameters to compression context. */
static int
set_c_parameters(ZstdCompressor *self,
                 PyObject *level_or_option,
                 int *compress_level)
{
    size_t zstd_ret;

    /* Integer compression level */
    if (PyLong_Check(level_or_option)) {
        int level = _PyLong_AsInt(level_or_option);
        if (level == -1 && PyErr_Occurred()) {
            PyErr_SetString(PyExc_ValueError,
                            "Compression level should be 32-bit signed int value.");
            return -1;
        }

        /* Clamp compression level */
        clamp_compression_level(&level);

        /* Save to *compress_level for generating ZSTD_CDICT */
        *compress_level = level;

        /* Set compressionLevel to compression context */
        zstd_ret = ZSTD_CCtx_setParameter(self->cctx,
                                          ZSTD_c_compressionLevel,
                                          level);

        /* Check error */
        if (ZSTD_isError(zstd_ret)) {
            set_zstd_error(ERR_SET_C_LEVEL, zstd_ret);
            return -1;
        }
        return 0;
    }

    /* Options dict */
    if (PyDict_Check(level_or_option)) {
        PyObject *key, *value;
        Py_ssize_t pos = 0;

        while (PyDict_Next(level_or_option, &pos, &key, &value)) {
            /* Both key & value should be 32-bit signed int */
            const int key_v = _PyLong_AsInt(key);
            if (key_v == -1 && PyErr_Occurred()) {
                PyErr_SetString(PyExc_ValueError,
                                "Key of option dict should be 32-bit signed int value.");
                return -1;
            }

            int value_v = _PyLong_AsInt(value);
            if (value_v == -1 && PyErr_Occurred()) {
                PyErr_SetString(PyExc_ValueError,
                                "Value of option dict should be 32-bit signed int value.");
                return -1;
            }

            if (key_v == ZSTD_c_compressionLevel) {
                /* Clamp compression level */
                clamp_compression_level(&value_v);

                /* Save to *compress_level for generating ZSTD_CDICT */
                *compress_level = value_v;
            } else if (key_v == ZSTD_c_nbWorkers) {
                /* From zstd library doc:
                   1. When nbWorkers >= 1, triggers asynchronous mode when
                      used with ZSTD_compressStream2().
                   2, Default value is `0`, aka "single-threaded mode" : no
                      worker is spawned, compression is performed inside
                      caller's thread, all invocations are blocking*/
                if (value_v > 1) {
                    self->use_multithreaded = 1;
                } else if (value_v == 1) {
                    /* Use single-threaded mode */
                    value_v = 0;
                }
            }

            /* Zstd lib doesn't support MT compression */
            if (!static_state.support_multithreaded &&
                (key_v == ZSTD_c_nbWorkers ||
                 key_v == ZSTD_c_jobSize ||
                 key_v == ZSTD_c_overlapLog) &&
                value_v > 0)
            {
                value_v = 0;

                if (key_v == ZSTD_c_nbWorkers) {
                    self->use_multithreaded = 0;
                    char *msg = "The underlying zstd library doesn't support "
                                "multi-threaded compression, it was built "
                                "without this feature. Pyzstd module will "
                                "perform single-threaded compression instead.";
                    if (PyErr_WarnEx(PyExc_RuntimeWarning, msg, 1) < 0) {
                        return -1;
                    }
                }
            }

            /* Set parameter to compression context */
            zstd_ret = ZSTD_CCtx_setParameter(self->cctx, key_v, value_v);
            if (ZSTD_isError(zstd_ret)) {
                set_parameter_error(1, pos, key_v, value_v);
                return -1;
            }
        }
        return 0;
    }

    /* Wrong type */
    PyErr_SetString(PyExc_TypeError, "level_or_option argument wrong type.");
    return -1;
}

/* Load dictionary (ZSTD_CDict instance) to compression context (ZSTD_CCtx instance). */
static int
load_c_dict(ZstdCompressor *self, PyObject *dict, int compress_level)
{
    size_t zstd_ret;
    ZSTD_CDict *c_dict;
    int ret;

    /* Check dict type */
    ret = PyObject_IsInstance(dict, (PyObject*)static_state.ZstdDict_type);
    if (ret < 0) {
        return -1;
    } else if (ret == 0) {
        PyErr_SetString(PyExc_TypeError,
                        "zstd_dict argument should be ZstdDict object.");
        return -1;
    }

    /* Get ZSTD_CDict */
    c_dict = _get_CDict((ZstdDict*)dict, compress_level);
    if (c_dict == NULL) {
        return -1;
    }

    /* Reference a prepared dictionary */
    zstd_ret = ZSTD_CCtx_refCDict(self->cctx, c_dict);

    /* Check error */
    if (ZSTD_isError(zstd_ret)) {
        set_zstd_error(ERR_LOAD_C_DICT, zstd_ret);
        return -1;
    }
    return 0;
}

/* Set decompression parameters to decompression context. */
static int
set_d_parameters(ZSTD_DCtx *dctx, PyObject *option)
{
    size_t zstd_ret;
    PyObject *key, *value;
    Py_ssize_t pos;

    if (!PyDict_Check(option)) {
        PyErr_SetString(PyExc_TypeError,
                        "option argument should be dict object.");
        return -1;
    }

    pos = 0;
    while (PyDict_Next(option, &pos, &key, &value)) {
        /* Both key & value should be 32-bit signed int */
        const int key_v = _PyLong_AsInt(key);
        if (key_v == -1 && PyErr_Occurred()) {
            PyErr_SetString(PyExc_ValueError,
                            "Key of option dict should be 32-bit signed integer value.");
            return -1;
        }

        const int value_v = _PyLong_AsInt(value);
        if (value_v == -1 && PyErr_Occurred()) {
            PyErr_SetString(PyExc_ValueError,
                            "Value of option dict should be 32-bit signed integer value.");
            return -1;
        }

        /* Set parameter to compression context */
        zstd_ret = ZSTD_DCtx_setParameter(dctx, key_v, value_v);

        /* Check error */
        if (ZSTD_isError(zstd_ret)) {
            set_parameter_error(0, pos, key_v, value_v);
            return -1;
        }
    }
    return 0;
}

/* Load dictionary (ZSTD_DDict instance) to decompression context (ZSTD_DCtx instance). */
static int
load_d_dict(ZSTD_DCtx *dctx, PyObject *dict)
{
    size_t zstd_ret;
    ZSTD_DDict *d_dict;
    int ret;

    /* Check dict type */
    ret = PyObject_IsInstance(dict, (PyObject*)static_state.ZstdDict_type);
    if (ret < 0) {
        return -1;
    } else if (ret == 0) {
        PyErr_SetString(PyExc_TypeError,
                        "zstd_dict argument should be ZstdDict object.");
        return -1;
    }

    /* Get ZSTD_DDict */
    d_dict = _get_DDict((ZstdDict*)dict);
    if (d_dict == NULL) {
        return -1;
    }

    /* Reference a decompress dictionary */
    zstd_ret = ZSTD_DCtx_refDDict(dctx, d_dict);

    /* Check error */
    if (ZSTD_isError(zstd_ret)) {
        set_zstd_error(ERR_LOAD_D_DICT, zstd_ret);
        return -1;
    }
    return 0;
}

PyDoc_STRVAR(reduce_cannot_pickle_doc,
"Intentionally not supporting pickle.");

static PyObject *
reduce_cannot_pickle(PyObject *self)
{
    PyErr_Format(PyExc_TypeError,
                 "Cannot pickle %s object.",
                 Py_TYPE(self)->tp_name);
    return NULL;
}

/* ------------------
     ZstdDict code
   ------------------ */
static PyObject *
ZstdDict_new(PyTypeObject *type, PyObject *args, PyObject *kwds)
{
    ZstdDict *self;
    self = (ZstdDict*)type->tp_alloc(type, 0);
    if (self == NULL) {
        goto error;
    }

    assert(self->dict_content == NULL);
    assert(self->dict_id == 0);
    assert(self->d_dict == NULL);
    assert(self->inited == 0);

    /* ZSTD_CDict dict */
    self->c_dicts = PyDict_New();
    if (self->c_dicts == NULL) {
        goto error;
    }

    /* Thread lock */
    self->lock = PyThread_allocate_lock();
    if (self->lock == NULL) {
        PyErr_NoMemory();
        goto error;
    }
    return (PyObject*)self;

error:
    Py_XDECREF(self);
    return NULL;
}

static void
ZstdDict_dealloc(ZstdDict *self)
{
    /* Free ZSTD_CDict instances */
    Py_XDECREF(self->c_dicts);

    /* Free ZSTD_DDict instance */
    ZSTD_freeDDict(self->d_dict);

    /* Release dict_content after Free ZSTD_CDict/ZSTD_DDict instances */
    Py_XDECREF(self->dict_content);

    /* Free thread lock */
    if (self->lock) {
        PyThread_free_lock(self->lock);
    }

    PyTypeObject *tp = Py_TYPE(self);
    tp->tp_free((PyObject*)self);
    Py_DECREF(tp);
}

static int
ZstdDict_init(ZstdDict *self, PyObject *args, PyObject *kwargs)
{
    static char *kwlist[] = {"dict_content", "is_raw", NULL};
    PyObject *dict_content;
    int is_raw = 0;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs,
                                     "O|p:ZstdDict.__init__", kwlist,
                                     &dict_content, &is_raw)) {
        return -1;
    }

    /* Only called once */
    if (self->inited) {
        PyErr_SetString(PyExc_RuntimeError, init_twice_msg);
        return -1;
    }
    self->inited = 1;

    /* Check dict_content's type */
    self->dict_content = PyBytes_FromObject(dict_content);
    if (self->dict_content == NULL) {
        PyErr_SetString(PyExc_TypeError,
                        "dict_content argument should be bytes-like object.");
        return -1;
    }

    /* Both ordinary dictionary and "raw content" dictionary should
       at least 8 bytes */
    if (Py_SIZE(self->dict_content) < 8) {
        PyErr_SetString(PyExc_ValueError,
                        "Zstd dictionary content should at least 8 bytes.");
        return -1;
    }

    /* Create ZSTD_DDict instance from dictionary content, also check content
       integrity to some degree. */
    Py_BEGIN_ALLOW_THREADS
    self->d_dict = ZSTD_createDDict(PyBytes_AS_STRING(self->dict_content),
                                    Py_SIZE(self->dict_content));
    Py_END_ALLOW_THREADS

    if (self->d_dict == NULL) {
        PyErr_SetString(static_state.ZstdError,
                        "Failed to get ZSTD_DDict instance from zstd "
                        "dictionary content. Maybe the content is corrupted.");
        return -1;
    }

    /* Get dict_id, 0 means "raw content" dictionary. */
    self->dict_id = ZSTD_getDictID_fromDDict(self->d_dict);

    /* Check validity for ordinary dictionary */
    if (!is_raw && self->dict_id == 0) {
        char *msg = "The \"dict_content\" argument is not a valid zstd "
                    "dictionary. The first 4 bytes of a valid zstd dictionary "
                    "should be a magic number: b'\\x37\\xA4\\x30\\xEC'.\n"
                    "If you are an advanced user, and can be sure that "
                    "\"dict_content\" is a \"raw content\" zstd dictionary, "
                    "set \"is_raw\" argument to True.";
        PyErr_SetString(PyExc_ValueError, msg);
        return -1;
    }

    return 0;
}

static PyObject *
ZstdDict_reduce(ZstdDict *self)
{
    /* return Py_BuildValue("O(O)", Py_TYPE(self), self->dict_content); */

    PyErr_SetString(PyExc_TypeError,
                    "Intentionally not supporting pickle. If need to save zstd "
                    "dictionary to disk, please save .dict_content attribute, "
                    "it's a bytes object. So that the zstd dictionary can be "
                    "used with other programs."
                    );
    return NULL;
}

static PyMethodDef ZstdDict_methods[] = {
    {"__reduce__", (PyCFunction)ZstdDict_reduce, METH_NOARGS, reduce_cannot_pickle_doc},
    {NULL, NULL, 0, NULL}
};

PyDoc_STRVAR(ZstdDict_dict_doc,
"Zstd dictionary, used for compression/decompression.\n\n"
"ZstdDict.__init__(self, dict_content, is_raw=False)\n"
"----\n"
"Initialize a ZstdDict object.\n\n"
"Arguments\n"
"dict_content: A bytes-like object, dictionary's content.\n"
"is_raw:       This parameter is for advanced user. True means dict_content\n"
"              argument is a \"raw content\" dictionary, free of any format\n"
"              restriction. False means dict_content argument is an ordinary\n"
"              zstd dictionary, was created by zstd functions, follow a\n"
"              specified format.");

PyDoc_STRVAR(ZstdDict_dictid_doc,
"ID of zstd dictionary, a 32-bit unsigned int value.\n\n"
"Non-zero means ordinary dictionary, was created by zstd functions, follow\n"
"a specified format.\n\n"
"0 means a \"raw content\" dictionary, free of any format restriction, used\n"
"for advanced user.");

PyDoc_STRVAR(ZstdDict_dictcontent_doc,
"The content of zstd dictionary, a bytes object, it's the same as dict_content\n"
"argument in ZstdDict.__init__() method. It can be used with other programs.");

static PyObject *
ZstdDict_str(ZstdDict *dict)
{
    char buf[64];
    PyOS_snprintf(buf, sizeof(buf),
                  "<ZstdDict dict_id=%u dict_size=%zd>",
                  dict->dict_id, Py_SIZE(dict->dict_content));

    return PyUnicode_FromString(buf);
}

static PyMemberDef ZstdDict_members[] = {
    {"dict_id", T_UINT, offsetof(ZstdDict, dict_id), READONLY, ZstdDict_dictid_doc},
    {"dict_content", T_OBJECT_EX, offsetof(ZstdDict, dict_content), READONLY, ZstdDict_dictcontent_doc},
    {NULL}
};

static PyType_Slot zstddict_slots[] = {
    {Py_tp_methods, ZstdDict_methods},
    {Py_tp_members, ZstdDict_members},
    {Py_tp_new, ZstdDict_new},
    {Py_tp_dealloc, ZstdDict_dealloc},
    {Py_tp_init, ZstdDict_init},
    {Py_tp_str, ZstdDict_str},
    {Py_tp_doc, (char*)ZstdDict_dict_doc},
    {0, 0}
};

static PyType_Spec zstddict_type_spec = {
    .name = "_zstd.ZstdDict",
    .basicsize = sizeof(ZstdDict),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    .slots = zstddict_slots,
};

/* --------------------------
     Train dictionary code
   -------------------------- */
PyDoc_STRVAR(_train_dict_doc,
"Internal function, train a zstd dictionary.");

static PyObject *
_train_dict(PyObject *module, PyObject *args)
{
    PyBytesObject *samples_bytes;
    PyObject *samples_size_list;
    Py_ssize_t dict_size;

    Py_ssize_t chunks_number;
    size_t *chunk_sizes = NULL;
    PyObject *dst_dict_bytes = NULL;
    size_t zstd_ret;
    Py_ssize_t i;

    if (!PyArg_ParseTuple(args, "SOn:_train_dict",
                          &samples_bytes, &samples_size_list, &dict_size)) {
        return NULL;
    }

    /* Check dict_size range */
    if (dict_size <= 0) {
        PyErr_SetString(PyExc_ValueError, "dict_size argument should be positive number.");
        return NULL;
    }

    /* Prepare chunk_sizes */
    if (!PyList_Check(samples_size_list)) {
        PyErr_SetString(PyExc_TypeError,
                        "samples_size_list argument should be a list.");
        goto error;
    }

    chunks_number = Py_SIZE(samples_size_list);
    if ((size_t) chunks_number > UINT32_MAX) {
        PyErr_SetString(PyExc_ValueError,
                        "The number of samples is too large.");
        goto error;
    }

    chunk_sizes = PyMem_Malloc(chunks_number * sizeof(size_t));
    if (chunk_sizes == NULL) {
        PyErr_NoMemory();
        goto error;
    }

    for (i = 0; i < chunks_number; i++) {
        PyObject *size = PyList_GET_ITEM(samples_size_list, i);
        chunk_sizes[i] = PyLong_AsSize_t(size);
        if (chunk_sizes[i] == (size_t)-1 && PyErr_Occurred()) {
            PyErr_SetString(PyExc_ValueError,
                            "Items in samples_size_list should be an int "
                            "object, with a size_t value.");
            goto error;
        }
    }

    /* Allocate dict buffer */
    dst_dict_bytes = PyBytes_FromStringAndSize(NULL, dict_size);
    if (dst_dict_bytes == NULL) {
        goto error;
    }

    /* Train the dictionary */
    Py_BEGIN_ALLOW_THREADS
    zstd_ret = ZDICT_trainFromBuffer(PyBytes_AS_STRING(dst_dict_bytes), dict_size,
                                     PyBytes_AS_STRING(samples_bytes),
                                     chunk_sizes, (uint32_t)chunks_number);
    Py_END_ALLOW_THREADS

    /* Check zstd dict error */
    if (ZDICT_isError(zstd_ret)) {
        set_zstd_error(ERR_TRAIN_DICT, zstd_ret);
        goto error;
    }

    /* Resize dict_buffer */
    if (_PyBytes_Resize(&dst_dict_bytes, zstd_ret) < 0) {
        goto error;
    }

    goto success;

error:
    Py_CLEAR(dst_dict_bytes);

success:
    PyMem_Free(chunk_sizes);
    return dst_dict_bytes;
}

PyDoc_STRVAR(_finalize_dict_doc,
"Internal function, finalize a zstd dictionary.");

static PyObject *
_finalize_dict(PyObject *module, PyObject *args)
{
#if ZSTD_VERSION_NUMBER < 10405
    PyErr_Format(PyExc_NotImplementedError,
                 "_finalize_dict function only available when the underlying "
                 "zstd library's version is greater than or equal to v1.4.5. "
                 "At pyzstd module's compile-time, zstd version < v1.4.5. At "
                 "pyzstd module's run-time, zstd version is v%s.",
                 ZSTD_versionString());
    return NULL;
#else
    if (ZSTD_versionNumber() < 10405) {
        /* Must be dynamically linked */
        PyErr_Format(PyExc_NotImplementedError,
                "_finalize_dict function only available when the underlying "
                "zstd library's version is greater than or equal to v1.4.5. "
                "At pyzstd module's compile-time, zstd version >= v1.4.5. At "
                "pyzstd module's run-time, zstd version is v%s.",
                ZSTD_versionString());
        return NULL;
    }

    PyBytesObject *custom_dict_bytes;
    PyBytesObject *samples_bytes;
    PyObject *samples_size_list;
    Py_ssize_t dict_size;
    int compression_level;

    Py_ssize_t chunks_number;
    size_t *chunk_sizes = NULL;
    PyObject *dst_dict_bytes = NULL;
    size_t zstd_ret;
    ZDICT_params_t params;
    Py_ssize_t i;

    if (!PyArg_ParseTuple(args, "SSOni:_finalize_dict",
                          &custom_dict_bytes, &samples_bytes, &samples_size_list,
                          &dict_size, &compression_level)) {
        return NULL;
    }

    /* Check dict_size range */
    if (dict_size <= 0) {
        PyErr_SetString(PyExc_ValueError, "dict_size argument should be positive number.");
        return NULL;
    }

    /* Prepare chunk_sizes */
    if (!PyList_Check(samples_size_list)) {
        PyErr_SetString(PyExc_TypeError,
                        "samples_size_list argument should be a list.");
        goto error;
    }

    chunks_number = Py_SIZE(samples_size_list);
    if ((size_t) chunks_number > UINT32_MAX) {
        PyErr_SetString(PyExc_ValueError,
                        "The number of samples is too large.");
        goto error;
    }

    chunk_sizes = PyMem_Malloc(chunks_number * sizeof(size_t));
    if (chunk_sizes == NULL) {
        PyErr_NoMemory();
        goto error;
    }

    for (i = 0; i < chunks_number; i++) {
        PyObject *size = PyList_GET_ITEM(samples_size_list, i);
        chunk_sizes[i] = PyLong_AsSize_t(size);
        if (chunk_sizes[i] == (size_t)-1 && PyErr_Occurred()) {
            PyErr_SetString(PyExc_ValueError,
                            "Items in samples_size_list should be an int "
                            "object, with a size_t value.");
            goto error;
        }
    }

    /* Allocate dict buffer */
    dst_dict_bytes = PyBytes_FromStringAndSize(NULL, dict_size);
    if (dst_dict_bytes == NULL) {
        goto error;
    }

    /* Parameters */

    /* Optimize for a specific zstd compression level, 0 means default. */
    params.compressionLevel = compression_level;
    /* Write log to stderr, 0 = none. */
    params.notificationLevel = 0;
    /* Force dictID value, 0 means auto mode (32-bits random value). */
    params.dictID = 0;

    /* Finalize the dictionary */
    Py_BEGIN_ALLOW_THREADS
    zstd_ret = ZDICT_finalizeDictionary(
                        PyBytes_AS_STRING(dst_dict_bytes), dict_size,
                        PyBytes_AS_STRING(custom_dict_bytes), Py_SIZE(custom_dict_bytes),
                        PyBytes_AS_STRING(samples_bytes), chunk_sizes,
                        (uint32_t)chunks_number, params);
    Py_END_ALLOW_THREADS

    /* Check zstd dict error */
    if (ZDICT_isError(zstd_ret)) {
        set_zstd_error(ERR_FINALIZE_DICT, zstd_ret);
        goto error;
    }

    /* Resize dict_buffer */
    if (_PyBytes_Resize(&dst_dict_bytes, zstd_ret) < 0) {
        goto error;
    }

    goto success;

error:
    Py_CLEAR(dst_dict_bytes);

success:
    PyMem_Free(chunk_sizes);
    return dst_dict_bytes;
#endif
}

/* -----------------------
     ZstdCompressor code
   ----------------------- */
static PyObject *
ZstdCompressor_new(PyTypeObject *type, PyObject *args, PyObject *kwds)
{
    ZstdCompressor *self;
    self = (ZstdCompressor*)type->tp_alloc(type, 0);
    if (self == NULL) {
        goto error;
    }

    assert(self->dict == NULL);
    assert(self->use_multithreaded == 0);
    assert(self->inited == 0);

    /* Compression context */
    self->cctx = ZSTD_createCCtx();
    if (self->cctx == NULL) {
        PyErr_SetString(static_state.ZstdError,
                        "Unable to create ZSTD_CCtx instance.");
        goto error;
    }

    /* Last mode */
    self->last_mode = ZSTD_e_end;

    /* Thread lock */
    self->lock = PyThread_allocate_lock();
    if (self->lock == NULL) {
        PyErr_NoMemory();
        goto error;
    }
    return (PyObject*)self;

error:
    Py_XDECREF(self);
    return NULL;
}

static void
ZstdCompressor_dealloc(ZstdCompressor *self)
{
    /* Free compression context */
    ZSTD_freeCCtx(self->cctx);

    /* Py_XDECREF the dict after free the compression context */
    Py_XDECREF(self->dict);

    /* Thread lock */
    if (self->lock) {
        PyThread_free_lock(self->lock);
    }

    PyTypeObject *tp = Py_TYPE(self);
    tp->tp_free((PyObject*)self);
    Py_DECREF(tp);
}

PyDoc_STRVAR(ZstdCompressor_doc,
"A streaming compressor. Thread-safe at method level.\n\n"
"ZstdCompressor.__init__(self, level_or_option=None, zstd_dict=None)\n"
"----\n"
"Initialize a ZstdCompressor object.\n\n"
"Arguments\n"
"level_or_option: When it's an int object, it represents the compression level.\n"
"                 When it's a dict object, it contains advanced compression\n"
"                 parameters.\n"
"zstd_dict:       A ZstdDict object, pre-trained zstd dictionary.");

static int
ZstdCompressor_init(ZstdCompressor *self, PyObject *args, PyObject *kwargs)
{
    static char *kwlist[] = {"level_or_option", "zstd_dict", NULL};
    PyObject *level_or_option = Py_None;
    PyObject *zstd_dict = Py_None;

    int compress_level = 0; /* 0 means use zstd's default compression level */

    if (!PyArg_ParseTupleAndKeywords(args, kwargs,
                                     "|OO:ZstdCompressor.__init__", kwlist,
                                     &level_or_option, &zstd_dict)) {
        return -1;
    }

    /* Only called once */
    if (self->inited) {
        PyErr_SetString(PyExc_RuntimeError, init_twice_msg);
        return -1;
    }
    self->inited = 1;

    /* Set compressLevel/option to compression context */
    if (level_or_option != Py_None) {
        if (set_c_parameters(self, level_or_option, &compress_level) < 0) {
            return -1;
        }
    }

    /* Load dictionary to compression context */
    if (zstd_dict != Py_None) {
        if (load_c_dict(self, zstd_dict, compress_level) < 0) {
            return -1;
        }

        /* Py_INCREF the dict */
        Py_INCREF(zstd_dict);
        self->dict = zstd_dict;
    }

    return 0;
}

static inline PyObject *
compress_impl(ZstdCompressor *self, Py_buffer *data,
              ZSTD_EndDirective end_directive, int rich_mem)
{
    ZSTD_inBuffer in;
    ZSTD_outBuffer out;
    BlocksOutputBuffer buffer = {.list = NULL};
    size_t zstd_ret;
    PyObject *ret;

    /* Prepare input & output buffers */
    if (data != NULL) {
        in.src = data->buf;
        in.size = data->len;
        in.pos = 0;
    } else {
        in.src = &in;
        in.size = 0;
        in.pos = 0;
    }

    if (rich_mem) {
        /* Calculate output buffer's size */
        size_t output_buffer_size = ZSTD_compressBound(in.size);
        if (output_buffer_size > (size_t) PY_SSIZE_T_MAX) {
            PyErr_NoMemory();
            goto error;
        }

        if (OutputBuffer_InitWithSize(&buffer, &out, -1,
                                      (Py_ssize_t) output_buffer_size) < 0) {
            goto error;
        }
    } else {
        if (OutputBuffer_InitAndGrow(&buffer, &out, -1) < 0) {
            goto error;
        }
    }

    /* zstd stream compress */
    while (1) {
        Py_BEGIN_ALLOW_THREADS
        zstd_ret = ZSTD_compressStream2(self->cctx, &out, &in, end_directive);
        Py_END_ALLOW_THREADS

        /* Check error */
        if (ZSTD_isError(zstd_ret)) {
            set_zstd_error(ERR_COMPRESS, zstd_ret);
            goto error;
        }

        /* Finished */
        if (zstd_ret == 0) {
            ret = OutputBuffer_Finish(&buffer, &out);
            if (ret != NULL) {
                goto success;
            } else {
                goto error;
            }
        }

        /* Output buffer should be exhausted, grow the buffer. */
        assert(out.pos == out.size);
        if (out.pos == out.size) {
            if (OutputBuffer_Grow(&buffer, &out) < 0) {
                goto error;
            }
        }
    }

success:
    return ret;
error:
    OutputBuffer_OnError(&buffer);
    return NULL;
}

static PyObject *
compress_mt_continue_impl(ZstdCompressor *self, Py_buffer *data)
{
    ZSTD_inBuffer in;
    ZSTD_outBuffer out;
    BlocksOutputBuffer buffer = {.list = NULL};
    size_t zstd_ret;
    PyObject *ret;

    /* Prepare input & output buffers */
    in.src = data->buf;
    in.size = data->len;
    in.pos = 0;

    if (OutputBuffer_InitAndGrow(&buffer, &out, -1) < 0) {
        goto error;
    }

    /* zstd stream compress */
    while (1) {
        Py_BEGIN_ALLOW_THREADS
        do {
            zstd_ret = ZSTD_compressStream2(self->cctx, &out, &in, ZSTD_e_continue);
        } while (out.pos != out.size && in.pos != in.size && !ZSTD_isError(zstd_ret));
        Py_END_ALLOW_THREADS

        /* Check error */
        if (ZSTD_isError(zstd_ret)) {
            set_zstd_error(ERR_COMPRESS, zstd_ret);
            goto error;
        }

        /* Finished */
        if (in.pos == in.size) {
            ret = OutputBuffer_Finish(&buffer, &out);
            if (ret != NULL) {
                goto success;
            } else {
                goto error;
            }
        }

        /* Output buffer should be exhausted, grow the buffer. */
        assert(out.pos == out.size);
        if (out.pos == out.size) {
            if (OutputBuffer_Grow(&buffer, &out) < 0) {
                goto error;
            }
        }
    }

success:
    return ret;
error:
    OutputBuffer_OnError(&buffer);
    return NULL;
}

PyDoc_STRVAR(ZstdCompressor_compress_doc,
"compress(data, mode=ZstdCompressor.CONTINUE)\n"
"----\n"
"Provide data to the compressor object.\n"
"Return a chunk of compressed data if possible, or b'' otherwise.\n\n"
"Arguments\n"
"data: A bytes-like object, data to be compressed.\n"
"mode: Can be these 3 values: .CONTINUE, .FLUSH_BLOCK, .FLUSH_FRAME.");

static PyObject *
ZstdCompressor_compress(ZstdCompressor *self, PyObject *args, PyObject *kwargs)
{
    static char *kwlist[] = {"data", "mode", NULL};
    Py_buffer data;
    int mode = ZSTD_e_continue;

    PyObject *ret;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs,
                                     "y*|i:ZstdCompressor.compress", kwlist,
                                     &data, &mode)) {
        return NULL;
    }

    /* Check mode value */
    if (mode != ZSTD_e_continue &&
        mode != ZSTD_e_flush &&
        mode != ZSTD_e_end)
    {
        PyErr_SetString(PyExc_ValueError,
                        "mode argument wrong value, it should be one of "
                        "ZstdCompressor.CONTINUE, ZstdCompressor.FLUSH_BLOCK, "
                        "ZstdCompressor.FLUSH_FRAME.");
        PyBuffer_Release(&data);
        return NULL;
    }

    /* Thread-safe code */
    ACQUIRE_LOCK(self);

    /* Compress */
    if (self->use_multithreaded && mode == ZSTD_e_continue) {
        ret = compress_mt_continue_impl(self, &data);
    } else {
        ret = compress_impl(self, &data, mode, 0);
    }

    if (ret) {
        self->last_mode = mode;
    } else {
        self->last_mode = ZSTD_e_end;

        /* Resetting cctx's session never fail */
        ZSTD_CCtx_reset(self->cctx, ZSTD_reset_session_only);
    }
    RELEASE_LOCK(self);

    PyBuffer_Release(&data);
    return ret;
}

PyDoc_STRVAR(ZstdCompressor_flush_doc,
"flush(mode=ZstdCompressor.FLUSH_FRAME)\n"
"----\n"
"Flush any remaining data in internal buffer.\n\n"
"Since zstd data consists of one or more independent frames, the compressor\n"
"object can still be used after this method is called.\n\n"
"Arguments\n"
"mode: Can be these 2 values: .FLUSH_FRAME, .FLUSH_BLOCK.");

static PyObject *
ZstdCompressor_flush(ZstdCompressor *self, PyObject *args, PyObject *kwargs)
{
    static char *kwlist[] = {"mode", NULL};
    int mode = ZSTD_e_end;

    PyObject *ret;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs,
                                     "|i:ZstdCompressor.flush", kwlist,
                                     &mode)) {
        return NULL;
    }

    /* Check mode value */
    if (mode != ZSTD_e_end && mode != ZSTD_e_flush) {
        PyErr_SetString(PyExc_ValueError,
                        "mode argument wrong value, it should be "
                        "ZstdCompressor.FLUSH_FRAME or "
                        "ZstdCompressor.FLUSH_BLOCK.");
        return NULL;
    }

    /* Thread-safe code */
    ACQUIRE_LOCK(self);
    ret = compress_impl(self, NULL, mode, 0);

    if (ret) {
        self->last_mode = mode;
    } else {
        self->last_mode = ZSTD_e_end;

        /* Resetting cctx's session never fail */
        ZSTD_CCtx_reset(self->cctx, ZSTD_reset_session_only);
    }
    RELEASE_LOCK(self);

    return ret;
}

static PyMethodDef ZstdCompressor_methods[] = {
    {"compress", (PyCFunction)ZstdCompressor_compress,
     METH_VARARGS|METH_KEYWORDS, ZstdCompressor_compress_doc},

    {"flush", (PyCFunction)ZstdCompressor_flush,
     METH_VARARGS|METH_KEYWORDS, ZstdCompressor_flush_doc},

    {"__reduce__", (PyCFunction)reduce_cannot_pickle,
    METH_NOARGS, reduce_cannot_pickle_doc},

    {NULL, NULL, 0, NULL}
};

PyDoc_STRVAR(ZstdCompressor_last_mode_doc,
"The last mode used to this compressor object, its value can be .CONTINUE,\n"
".FLUSH_BLOCK, .FLUSH_FRAME. Initialized to .FLUSH_FRAME.\n\n"
"It can be used to get the current state of a compressor, such as, a block\n"
"ends, a frame ends.");

static PyMemberDef ZstdCompressor_members[] = {
    {"last_mode", T_INT, offsetof(ZstdCompressor, last_mode),
      READONLY, ZstdCompressor_last_mode_doc},
    {NULL}
};

static PyType_Slot zstdcompressor_slots[] = {
    {Py_tp_new, ZstdCompressor_new},
    {Py_tp_dealloc, ZstdCompressor_dealloc},
    {Py_tp_init, ZstdCompressor_init},
    {Py_tp_methods, ZstdCompressor_methods},
    {Py_tp_members, ZstdCompressor_members},
    {Py_tp_doc, (char*)ZstdCompressor_doc},
    {0, 0}
};

static PyType_Spec zstdcompressor_type_spec = {
    .name = "_zstd.ZstdCompressor",
    .basicsize = sizeof(ZstdCompressor),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    .slots = zstdcompressor_slots,
};

/* ------------------------------
     RichMemZstdCompressor code
   ------------------------------ */
static int
RichMemZstdCompressor_init(ZstdCompressor *self, PyObject *args, PyObject *kwargs)
{
    static char *kwlist[] = {"level_or_option", "zstd_dict", NULL};
    PyObject *level_or_option = Py_None;
    PyObject *zstd_dict = Py_None;

    int compress_level = 0; /* 0 means use zstd's default compression level */

    if (!PyArg_ParseTupleAndKeywords(args, kwargs,
                                     "|OO:RichMemZstdCompressor.__init__", kwlist,
                                     &level_or_option, &zstd_dict)) {
        return -1;
    }

    /* Only called once */
    if (self->inited) {
        PyErr_SetString(PyExc_RuntimeError, init_twice_msg);
        return -1;
    }
    self->inited = 1;

    /* Set compressLevel/option to compression context */
    if (level_or_option != Py_None) {
        if (set_c_parameters(self, level_or_option, &compress_level) < 0) {
            return -1;
        }
    }

    /* Check effective condition */
    if (self->use_multithreaded) {
        char *msg = "Currently \"rich memory mode\" has no effect on "
                    "zstd multi-threaded compression (set "
                    "\"CParameter.nbWorkers\" > 1), it will allocate "
                    "unnecessary memory.";
        if (PyErr_WarnEx(PyExc_ResourceWarning, msg, 1) < 0) {
            return -1;
        }
    }

    /* Load dictionary to compression context */
    if (zstd_dict != Py_None) {
        if (load_c_dict(self, zstd_dict, compress_level) < 0) {
            return -1;
        }

        /* Py_INCREF the dict */
        Py_INCREF(zstd_dict);
        self->dict = zstd_dict;
    }

    return 0;
}

PyDoc_STRVAR(RichMemZstdCompressor_compress_doc,
"compress(data)\n"
"----\n"
"Compress data using rich memory mode, return a single zstd frame.\n\n"
"Compressing b'' will get an empty content frame (9 bytes or more).\n\n"
"Arguments\n"
"data: A bytes-like object, data to be compressed.");

static PyObject *
RichMemZstdCompressor_compress(ZstdCompressor *self, PyObject *args, PyObject *kwargs)
{
    static char *kwlist[] = {"data", NULL};
    Py_buffer data;

    PyObject *ret;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs,
                                     "y*:RichMemZstdCompressor.compress", kwlist,
                                     &data)) {
        return NULL;
    }

    /* Thread-safe code */
    ACQUIRE_LOCK(self);

    ret = compress_impl(self, &data, ZSTD_e_end, 1);
    if (ret == NULL) {
        /* Resetting cctx's session never fail */
        ZSTD_CCtx_reset(self->cctx, ZSTD_reset_session_only);
    }

    RELEASE_LOCK(self);

    PyBuffer_Release(&data);
    return ret;
}

static PyMethodDef RichMem_ZstdCompressor_methods[] = {
    {"compress", (PyCFunction)RichMemZstdCompressor_compress,
     METH_VARARGS|METH_KEYWORDS, RichMemZstdCompressor_compress_doc},

    {"__reduce__", (PyCFunction)reduce_cannot_pickle,
    METH_NOARGS, reduce_cannot_pickle_doc},

    {NULL, NULL, 0, NULL}
};

PyDoc_STRVAR(RichMemZstdCompressor_doc,
"A compressor use rich memory mode. It is designed to allocate more memory,\n"
"but faster in some cases.\n\n"
"RichMemZstdCompressor.__init__(self, level_or_option=None, zstd_dict=None)\n"
"----\n"
"Initialize a RichMemZstdCompressor object.\n\n"
"Arguments\n"
"level_or_option: When it's an int object, it represents the compression level.\n"
"                 When it's a dict object, it contains advanced compression\n"
"                 parameters.\n"
"zstd_dict:       A ZstdDict object, pre-trained zstd dictionary.");

static PyType_Slot richmem_zstdcompressor_slots[] = {
    {Py_tp_new, ZstdCompressor_new},
    {Py_tp_dealloc, ZstdCompressor_dealloc},
    {Py_tp_init, RichMemZstdCompressor_init},
    {Py_tp_methods, RichMem_ZstdCompressor_methods},
    {Py_tp_doc, (char*)RichMemZstdCompressor_doc},
    {0, 0}
};

static PyType_Spec richmem_zstdcompressor_type_spec = {
    .name = "_zstd.RichMemZstdCompressor",
    .basicsize = sizeof(ZstdCompressor),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    .slots = richmem_zstdcompressor_slots,
};

/* -------------------------
   Decompress implementation
   ------------------------- */

typedef enum {
    TYPE_DECOMPRESSOR,          /* <D>, ZstdDecompressor class */
    TYPE_ENDLESS_DECOMPRESSOR,  /* <E>, EndlessZstdDecompressor class */
} decompress_type;

/* Decompress implementation for <D>, <E>, pseudo code:

        initialize_output_buffer
        while True:
            decompress_data
            set_object_flag   # .eof for <D>, .at_frame_edge for <E>.

            if output_buffer_exhausted:
                if output_buffer_reached_max_length:
                    finish
                grow_output_buffer
            elif input_buffer_exhausted:
                finish

    ZSTD_decompressStream()'s size_t return value:
      - 0 when a frame is completely decoded and fully flushed, zstd's internal
        buffer has no data.
      - An error code, which can be tested using ZSTD_isError().
      - Or any other value > 0, which means there is still some decoding or
        flushing to do to complete current frame.

      Note, decompressing "an empty input" in any case will make it > 0.

    <E> supports multiple frames, has an .at_frame_edge flag, it means both the
    input and output streams are at a frame edge. The flag can be set by this
    statement:

        .at_frame_edge = (zstd_ret == 0) ? 1 : 0

    But if decompressing "an empty input" at "a frame edge", zstd_ret will be
    non-zero, then .at_frame_edge will be wrongly set to false. To solve this
    problem, two AFE checks are needed to ensure that: when at "a frame edge",
    empty input will not be decompressed.

        // AFE check
        if (self->at_frame_edge && in->pos == in->size) {
            finish
        }

    In <E>, if .at_frame_edge is eventually set to true, but input stream has
    unconsumed data (in->pos < in->size), then the outer function
    stream_decompress() will set .at_frame_edge to false. In this case,
    although the output stream is at a frame edge, for the caller, the input
    stream is not at a frame edge, see below diagram. This behavior does not
    affect the next AFE check, since (in->pos < in->size).

    input stream:  --------------|---
                                    ^
    output stream: ====================|
                                       ^
*/
FORCE_INLINE PyObject *
decompress_impl(ZstdDecompressor *self, ZSTD_inBuffer *in,
                const Py_ssize_t max_length,
                const Py_ssize_t initial_size,
                const decompress_type type)
{
    size_t zstd_ret;
    ZSTD_outBuffer out;
    BlocksOutputBuffer buffer = {.list = NULL};
    PyObject *ret;

    /* The first AFE check for setting .at_frame_edge flag */
    if (type == TYPE_ENDLESS_DECOMPRESSOR) {
        if (self->at_frame_edge && in->pos == in->size) {
            ret = static_state.empty_bytes;
            Py_INCREF(ret);
            return ret;
        }
    }

    /* Initialize the output buffer */
    if (initial_size >= 0) {
        if (OutputBuffer_InitWithSize(&buffer, &out, max_length, initial_size) < 0) {
            goto error;
        }
    } else {
        if (OutputBuffer_InitAndGrow(&buffer, &out, max_length) < 0) {
            goto error;
        }
    }
    assert(out.pos == 0);

    while (1) {
        /* Decompress */
        Py_BEGIN_ALLOW_THREADS
        zstd_ret = ZSTD_decompressStream(self->dctx, &out, in);
        Py_END_ALLOW_THREADS

        /* Check error */
        if (ZSTD_isError(zstd_ret)) {
            set_zstd_error(ERR_DECOMPRESS, zstd_ret);
            goto error;
        }

        /* Set .eof/.af_frame_edge flag */
        if (type == TYPE_DECOMPRESSOR) {
            /* ZstdDecompressor class stops when a frame is decompressed */
            if (zstd_ret == 0) {
                self->eof = 1;
                break;
            }
        } else if (type == TYPE_ENDLESS_DECOMPRESSOR) {
            /* EndlessZstdDecompressor class supports multiple frames */
            self->at_frame_edge = (zstd_ret == 0) ? 1 : 0;

            /* The second AFE check for setting .at_frame_edge flag */
            if (self->at_frame_edge && in->pos == in->size) {
                break;
            }
        }

        /* Need to check out before in. Maybe zstd's internal buffer still has
           a few bytes can be output, grow the buffer and continue. */
        if (out.pos == out.size) {
            /* Output buffer exhausted */

            /* Output buffer reached max_length */
            if (OutputBuffer_ReachedMaxLength(&buffer, &out)) {
                break;
            }

            /* Grow output buffer */
            if (OutputBuffer_Grow(&buffer, &out) < 0) {
                goto error;
            }
            assert(out.pos == 0);

        } else if (in->pos == in->size) {
            /* Finished */
            break;
        }
    }

    /* Return a bytes object */
    ret = OutputBuffer_Finish(&buffer, &out);
    if (ret != NULL) {
        return ret;
    }

error:
    OutputBuffer_OnError(&buffer);
    return NULL;
}

/* For ZstdDecompressor, EndlessZstdDecompressor. */
FORCE_INLINE PyObject *
stream_decompress(ZstdDecompressor *self, PyObject *args, PyObject *kwargs,
                  const decompress_type type)
{
    static char *kwlist[] = {"data", "max_length", NULL};
    Py_buffer data;
    Py_ssize_t max_length = -1;

    Py_ssize_t initial_buffer_size = -1;
    ZSTD_inBuffer in;
    PyObject *ret = NULL;
    char use_input_buffer;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs,
                                     "y*|n:ZstdDecompressor.decompress", kwlist,
                                     &data, &max_length)) {
        return NULL;
    }

    /* Thread-safe code */
    ACQUIRE_LOCK(self);

    if (type == TYPE_DECOMPRESSOR) {
        /* Check .eof flag */
        if (self->eof) {
            PyErr_SetString(PyExc_EOFError, "Already at the end of a zstd frame.");
            assert(ret == NULL);
            goto success;
        }
    } else if (type == TYPE_ENDLESS_DECOMPRESSOR) {
        /* Fast path for the first frame */
        if (self->at_frame_edge && self->in_begin == self->in_end) {
            /* Read decompressed size */
            uint64_t decompressed_size = ZSTD_getFrameContentSize(data.buf, data.len);

            /* These two zstd constants always > PY_SSIZE_T_MAX:
                  ZSTD_CONTENTSIZE_UNKNOWN is (0ULL - 1)
                  ZSTD_CONTENTSIZE_ERROR   is (0ULL - 2)

               Use ZSTD_findFrameCompressedSize() to check complete frame,
               prevent allocating too much memory for small input chunk. */

            if (decompressed_size <= (uint64_t) PY_SSIZE_T_MAX &&
                !ZSTD_isError(ZSTD_findFrameCompressedSize(data.buf, data.len)) )
            {
                initial_buffer_size = (Py_ssize_t) decompressed_size;
            }
        }
    }

    /* Prepare input buffer w/wo unconsumed data */
    if (self->in_begin == self->in_end) {
        /* No unconsumed data */
        use_input_buffer = 0;

        in.src = data.buf;
        in.size = data.len;
        in.pos = 0;
    } else if (data.len == 0) {
        /* Has unconsumed data, fast path for b'' */
        assert(self->in_begin < self->in_end);

        use_input_buffer = 1;

        in.src = self->input_buffer + self->in_begin;
        in.size = self->in_end - self->in_begin;
        in.pos = 0;
    } else {
        /* Has unconsumed data */
        use_input_buffer = 1;

        /* Unconsumed data size in input_buffer */
        const size_t used_now = self->in_end - self->in_begin;
        assert(self->in_end > self->in_begin);

        /* Number of bytes we can append to input buffer */
        const size_t avail_now = self->input_buffer_size - self->in_end;
        assert(self->input_buffer_size >= self->in_end);

        /* Number of bytes we can append if we move existing contents to
           beginning of buffer */
        const size_t avail_total = self->input_buffer_size - used_now;
        assert(self->input_buffer_size >= used_now);

        if (avail_total < (size_t) data.len) {
            char *tmp;
            const size_t new_size = used_now + data.len;

            /* Allocate with new size */
            tmp = PyMem_Malloc(new_size);
            if (tmp == NULL) {
                PyErr_NoMemory();
                goto error;
            }

            /* Copy unconsumed data to the beginning of new buffer */
            memcpy(tmp,
                   self->input_buffer + self->in_begin,
                   used_now);

            /* Switch to new buffer */
            PyMem_Free(self->input_buffer);
            self->input_buffer = tmp;
            self->input_buffer_size = new_size;

            /* Set begin & end position */
            self->in_begin = 0;
            self->in_end = used_now;
        } else if (avail_now < (size_t) data.len) {
            /* Move unconsumed data to the beginning.
               dst < src, so using memcpy() is safe. */
            memcpy(self->input_buffer,
                   self->input_buffer + self->in_begin,
                   used_now);

            /* Set begin & end position */
            self->in_begin = 0;
            self->in_end = used_now;
        }

        /* Copy data to input buffer */
        memcpy(self->input_buffer + self->in_end, data.buf, data.len);
        self->in_end += data.len;

        in.src = self->input_buffer + self->in_begin;
        in.size = used_now + data.len;
        in.pos = 0;
    }
    assert(in.pos == 0);

    /* Decompress */
    ret = decompress_impl(self, &in,
                          max_length, initial_buffer_size,
                          type);
    if (ret == NULL) {
        goto error;
    }

    /* Unconsumed input data */
    if (in.pos == in.size) {
        if (type == TYPE_DECOMPRESSOR) {
            if (Py_SIZE(ret) == max_length || self->eof) {
                self->needs_input = 0;
            } else {
                self->needs_input = 1;
            }
        } else if (type == TYPE_ENDLESS_DECOMPRESSOR) {
            if (Py_SIZE(ret) == max_length && !self->at_frame_edge) {
                self->needs_input = 0;
            } else {
                self->needs_input = 1;
            }
        }

        if (use_input_buffer) {
            /* Clear input_buffer */
            self->in_begin = 0;
            self->in_end = 0;
        }
    } else {
        const size_t data_size = in.size - in.pos;

        /*if (type == DECOMPRESSOR) {
            if (self->eof) {
                self->needs_input = 0;
            } else {
                self->needs_input = 0;
            }
        } else if (type == ENDLESS_DECOMPRESSOR) {
            self->needs_input = 0;
        }*/
        self->needs_input = 0;

        if (type == TYPE_ENDLESS_DECOMPRESSOR) {
            /*if (self->at_frame_edge) {
                self->at_frame_edge = 0;
            }*/
            self->at_frame_edge = 0;
        }

        if (!use_input_buffer) {
            /* Discard buffer if it's too small
               (resizing it may needlessly copy the current contents) */
            if (self->input_buffer != NULL &&
                self->input_buffer_size < data_size)
            {
                PyMem_Free(self->input_buffer);
                self->input_buffer = NULL;
                self->input_buffer_size = 0;
            }

            /* Allocate if necessary */
            if (self->input_buffer == NULL) {
                self->input_buffer = PyMem_Malloc(data_size);
                if (self->input_buffer == NULL) {
                    PyErr_NoMemory();
                    goto error;
                }
                self->input_buffer_size = data_size;
            }

            /* Copy unconsumed data */
            memcpy(self->input_buffer, (char*)in.src + in.pos, data_size);
            self->in_begin = 0;
            self->in_end = data_size;
        } else {
            /* Use input buffer */
            self->in_begin += in.pos;
        }
    }

    goto success;

error:
    /* Reset variables */
    self->in_begin = 0;
    self->in_end = 0;

    self->needs_input = 1;
    if (type == TYPE_DECOMPRESSOR) {
        self->eof = 0;
    } else if (type == TYPE_ENDLESS_DECOMPRESSOR) {
        self->at_frame_edge = 1;
    }

    /* Resetting session never fail */
    ZSTD_DCtx_reset(self->dctx, ZSTD_reset_session_only);

    Py_CLEAR(ret);
success:
    RELEASE_LOCK(self);

    PyBuffer_Release(&data);
    return ret;
}

/* -------------------------
     ZstdDecompressor code
   ------------------------- */
static PyObject *
ZstdDecompressor_new(PyTypeObject *type, PyObject *args, PyObject *kwds)
{
    ZstdDecompressor *self;
    self = (ZstdDecompressor*)type->tp_alloc(type, 0);
    if (self == NULL) {
        goto error;
    }

    assert(self->dict == NULL);
    assert(self->input_buffer == NULL);
    assert(self->input_buffer_size == 0);
    assert(self->in_begin == 0);
    assert(self->in_end == 0);
    assert(self->unused_data == NULL);
    assert(self->eof == 0);
    assert(self->inited == 0);

    /* at_frame_edge flag */
    self->at_frame_edge = 1;

    /* needs_input flag */
    self->needs_input = 1;

    /* Decompression context */
    self->dctx = ZSTD_createDCtx();
    if (self->dctx == NULL) {
        PyErr_SetString(static_state.ZstdError,
                        "Unable to create ZSTD_DCtx instance.");
        goto error;
    }

    /* Thread lock */
    self->lock = PyThread_allocate_lock();
    if (self->lock == NULL) {
        PyErr_NoMemory();
        goto error;
    }
    return (PyObject*)self;

error:
    Py_XDECREF(self);
    return NULL;
}

static void
ZstdDecompressor_dealloc(ZstdDecompressor *self)
{
    /* Free decompression context */
    ZSTD_freeDCtx(self->dctx);

    /* Py_XDECREF the dict after free decompression context */
    Py_XDECREF(self->dict);

    /* Free unconsumed input data buffer */
    PyMem_Free(self->input_buffer);

    /* Free unused data */
    Py_XDECREF(self->unused_data);

    /* Free thread lock */
    if (self->lock) {
        PyThread_free_lock(self->lock);
    }

    PyTypeObject *tp = Py_TYPE(self);
    tp->tp_free((PyObject*)self);
    Py_DECREF(tp);
}

PyDoc_STRVAR(ZstdDecompressor_doc,
"A streaming decompressor, it stops after a frame is decompressed.\n"
"Thread-safe at method level.\n\n"
"ZstdDecompressor.__init__(self, zstd_dict=None, option=None)\n"
"----\n"
"Initialize a ZstdDecompressor object.\n\n"
"Arguments\n"
"zstd_dict: A ZstdDict object, pre-trained zstd dictionary.\n"
"option:    A dict object that contains advanced decompression parameters.");

static int
ZstdDecompressor_init(ZstdDecompressor *self, PyObject *args, PyObject *kwargs)
{
    static char *kwlist[] = {"zstd_dict", "option", NULL};
    PyObject *zstd_dict = Py_None;
    PyObject *option = Py_None;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs,
                                     "|OO:ZstdDecompressor.__init__", kwlist,
                                     &zstd_dict, &option)) {
        return -1;
    }

    /* Only called once */
    if (self->inited) {
        PyErr_SetString(PyExc_RuntimeError, init_twice_msg);
        return -1;
    }
    self->inited = 1;

    /* Load dictionary to decompression context */
    if (zstd_dict != Py_None) {
        if (load_d_dict(self->dctx, zstd_dict) < 0) {
            return -1;
        }

        /* Py_INCREF the dict */
        Py_INCREF(zstd_dict);
        self->dict = zstd_dict;
    }

    /* Set option to decompression context */
    if (option != Py_None) {
        if (set_d_parameters(self->dctx, option) < 0) {
            return -1;
        }
    }

    return 0;
}

static PyObject *
unused_data_get(ZstdDecompressor *self, void *Py_UNUSED(ignored))
{
    PyObject *ret;

    /* Thread-safe code */
    ACQUIRE_LOCK(self);

    if (!self->eof) {
        ret = static_state.empty_bytes;
        Py_INCREF(ret);
    } else {
        if (self->unused_data == NULL) {
            self->unused_data = PyBytes_FromStringAndSize(
                                    self->input_buffer + self->in_begin,
                                    self->in_end - self->in_begin);
            ret = self->unused_data;
            Py_XINCREF(ret);
        } else {
            ret = self->unused_data;
            Py_INCREF(ret);
        }
    }

    RELEASE_LOCK(self);

    return ret;
}

PyDoc_STRVAR(ZstdDecompressor_decompress_doc,
"decompress(data, max_length=-1)\n"
"----\n"
"Decompress data, return a chunk of decompressed data if possible, or b''\n"
"otherwise.\n\n"
"It stops after a frame is decompressed.\n\n"
"Arguments\n"
"data:       A bytes-like object, zstd data to be decompressed.\n"
"max_length: Maximum size of returned data. When it is negative, the size of\n"
"            output buffer is unlimited. When it is nonnegative, returns at\n"
"            most max_length bytes of decompressed data.");

static PyObject *
ZstdDecompressor_decompress(ZstdDecompressor *self, PyObject *args, PyObject *kwargs)
{
    return stream_decompress(self, args, kwargs, TYPE_DECOMPRESSOR);
}

static PyMethodDef ZstdDecompressor_methods[] = {
    {"decompress", (PyCFunction)ZstdDecompressor_decompress,
     METH_VARARGS|METH_KEYWORDS, ZstdDecompressor_decompress_doc},

    {"__reduce__", (PyCFunction)reduce_cannot_pickle,
    METH_NOARGS, reduce_cannot_pickle_doc},

    {NULL, NULL, 0, NULL}
};

PyDoc_STRVAR(ZstdDecompressor_eof_doc,
"True means the end of the first frame has been reached. If decompress data\n"
"after that, an EOFError exception will be raised.");

PyDoc_STRVAR(ZstdDecompressor_needs_input_doc,
"If the max_length output limit in .decompress() method has been reached, and\n"
"the decompressor has (or may has) unconsumed input data, it will be set to\n"
"False. In this case, pass b'' to .decompress() method may output further data.");

PyDoc_STRVAR(ZstdDecompressor_unused_data_doc,
"A bytes object. When ZstdDecompressor object stops after a frame is\n"
"decompressed, unused input data after the frame. Otherwise this will be b''.");

static PyMemberDef ZstdDecompressor_members[] = {
    {"eof", T_BOOL, offsetof(ZstdDecompressor, eof),
     READONLY, ZstdDecompressor_eof_doc},

    {"needs_input", T_BOOL, offsetof(ZstdDecompressor, needs_input),
     READONLY, ZstdDecompressor_needs_input_doc},

    {NULL}
};

static PyGetSetDef ZstdDecompressor_getset[] = {
    {"unused_data", (getter)unused_data_get, NULL,
     ZstdDecompressor_unused_data_doc},

    {NULL},
};

static PyType_Slot ZstdDecompressor_slots[] = {
    {Py_tp_new, ZstdDecompressor_new},
    {Py_tp_dealloc, ZstdDecompressor_dealloc},
    {Py_tp_init, ZstdDecompressor_init},
    {Py_tp_methods, ZstdDecompressor_methods},
    {Py_tp_members, ZstdDecompressor_members},
    {Py_tp_getset, ZstdDecompressor_getset},
    {Py_tp_doc, (char*)ZstdDecompressor_doc},
    {0, 0}
};

static PyType_Spec ZstdDecompressor_type_spec = {
    .name = "_zstd.ZstdDecompressor",
    .basicsize = sizeof(ZstdDecompressor),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    .slots = ZstdDecompressor_slots,
};

/* -------------------------------
     EndlessZstdDecompressor code
   ------------------------------- */
PyDoc_STRVAR(EndlessZstdDecompressor_doc,
"A streaming decompressor, accepts multiple concatenated frames.\n"
"Thread-safe at method level.\n\n"
"EndlessZstdDecompressor.__init__(self, zstd_dict=None, option=None)\n"
"----\n"
"Initialize an EndlessZstdDecompressor object.\n\n"
"Arguments\n"
"zstd_dict: A ZstdDict object, pre-trained zstd dictionary.\n"
"option:    A dict object that contains advanced decompression parameters.");

PyDoc_STRVAR(EndlessZstdDecompressor_decompress_doc,
"decompress(data, max_length=-1)\n"
"----\n"
"Decompress data, return a chunk of decompressed data if possible, or b''\n"
"otherwise.\n\n"
"Arguments\n"
"data:       A bytes-like object, zstd data to be decompressed.\n"
"max_length: Maximum size of returned data. When it is negative, the size of\n"
"            output buffer is unlimited. When it is nonnegative, returns at\n"
"            most max_length bytes of decompressed data.");

static PyObject *
EndlessZstdDecompressor_decompress(ZstdDecompressor *self, PyObject *args, PyObject *kwargs)
{
    return stream_decompress(self, args, kwargs, TYPE_ENDLESS_DECOMPRESSOR);
}

static PyMethodDef EndlessZstdDecompressor_methods[] = {
    {"decompress", (PyCFunction)EndlessZstdDecompressor_decompress,
     METH_VARARGS|METH_KEYWORDS, EndlessZstdDecompressor_decompress_doc},

    {"__reduce__", (PyCFunction)reduce_cannot_pickle,
    METH_NOARGS, reduce_cannot_pickle_doc},

    {NULL, NULL, 0, NULL}
};

PyDoc_STRVAR(EndlessZstdDecompressor_at_frame_edge_doc,
"True when both input and output streams are at a frame edge, means a frame is\n"
"completely decoded and fully flushed, or the decompressor just be initialized.\n\n"
"This flag could be used to check data integrity in some cases.");

static PyMemberDef EndlessZstdDecompressor_members[] = {
    {"at_frame_edge", T_BOOL, offsetof(ZstdDecompressor, at_frame_edge),
     READONLY, EndlessZstdDecompressor_at_frame_edge_doc},

    {"needs_input", T_BOOL, offsetof(ZstdDecompressor, needs_input),
     READONLY, ZstdDecompressor_needs_input_doc},

    {NULL}
};

static PyType_Slot EndlessZstdDecompressor_slots[] = {
    {Py_tp_new, ZstdDecompressor_new},
    {Py_tp_dealloc, ZstdDecompressor_dealloc},
    {Py_tp_init, ZstdDecompressor_init},
    {Py_tp_methods, EndlessZstdDecompressor_methods},
    {Py_tp_members, EndlessZstdDecompressor_members},
    {Py_tp_doc, (char*)EndlessZstdDecompressor_doc},
    {0, 0}
};

static PyType_Spec EndlessZstdDecompressor_type_spec = {
    .name = "_zstd.EndlessZstdDecompressor",
    .basicsize = sizeof(ZstdDecompressor),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    .slots = EndlessZstdDecompressor_slots,
};

/* --------------------------
     Module level functions
   -------------------------- */

PyDoc_STRVAR(decompress_doc,
"decompress(data, zstd_dict=None, option=None)\n"
"----\n"
"Decompress a zstd data, return a bytes object.\n\n"
"Support multiple concatenated frames.\n\n"
"Arguments\n"
"data:      A bytes-like object, compressed zstd data.\n"
"zstd_dict: A ZstdDict object, pre-trained zstd dictionary.\n"
"option:    A dict object, contains advanced decompression parameters.");

static PyObject *
decompress(PyObject *module, PyObject *args, PyObject *kwargs)
{
    static char *kwlist[] = {"data", "zstd_dict", "option", NULL};
    Py_buffer data;
    PyObject *zstd_dict = Py_None;
    PyObject *option = Py_None;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs,
                                     "y*|OO:decompress", kwlist,
                                     &data, &zstd_dict, &option)) {
        return NULL;
    }

    uint64_t decompressed_size;
    Py_ssize_t initial_size;
    ZstdDecompressor self = {0};
    ZSTD_inBuffer in;
    PyObject *ret = NULL;

    /* Initialize & set ZstdDecompressor */
    self.dctx = ZSTD_createDCtx();
    if (self.dctx == NULL) {
        PyErr_SetString(static_state.ZstdError,
                        "Unable to create ZSTD_DCtx instance.");
        goto error;
    }
    self.at_frame_edge = 1;

    /* Load dictionary to decompression context */
    if (zstd_dict != Py_None) {
        if (load_d_dict(self.dctx, zstd_dict) < 0) {
            goto error;
        }
    }

    /* Set option to decompression context */
    if (option != Py_None) {
        if (set_d_parameters(self.dctx, option) < 0) {
            goto error;
        }
    }

    /* Prepare input data */
    in.src = data.buf;
    in.size = data.len;
    in.pos = 0;

    /* Get decompressed size */
    decompressed_size = ZSTD_getFrameContentSize(data.buf, data.len);
    /* These two zstd constants always > PY_SSIZE_T_MAX:
         ZSTD_CONTENTSIZE_UNKNOWN is (0ULL - 1)
         ZSTD_CONTENTSIZE_ERROR   is (0ULL - 2) */
    if (decompressed_size <= (uint64_t) PY_SSIZE_T_MAX) {
        initial_size = (Py_ssize_t) decompressed_size;
    } else {
        initial_size = -1;
    }

    /* Decompress */
    ret = decompress_impl(&self, &in, -1, initial_size,
                          TYPE_ENDLESS_DECOMPRESSOR);
    if (ret == NULL) {
        goto error;
    }

    /* Check data integrity. at_frame_edge flag is 1 when both the input and
       output streams are at a frame edge. */
    if (self.at_frame_edge == 0) {
        char *extra_msg = (Py_SIZE(ret) == 0) ? "." :
                          ", if want to output these decompressed data, use "
                          "an EndlessZstdDecompressor object to decompress.";
        PyErr_Format(static_state.ZstdError,
                     "Decompression failed: zstd data ends in an incomplete "
                     "frame, maybe the input data was truncated. Decompressed "
                     "data is %zd bytes%s",
                     Py_SIZE(ret), extra_msg);
        goto error;
    }

    goto success;

error:
    Py_CLEAR(ret);
success:
    /* Free decompression context */
    ZSTD_freeDCtx(self.dctx);
    /* Release data */
    PyBuffer_Release(&data);
    return ret;
}

PyDoc_STRVAR(_get_param_bounds_doc,
"Internal funciton, get CParameter/DParameter bounds.");

static PyObject *
_get_param_bounds(PyObject *module, PyObject *args)
{
    int is_compress;
    int parameter;

    PyObject *ret;
    PyObject *temp;
    ZSTD_bounds bound;

    if (!PyArg_ParseTuple(args, "ii:_get_param_bounds", &is_compress, &parameter)) {
        return NULL;
    }

    if (is_compress) {
        bound = ZSTD_cParam_getBounds(parameter);
        if (ZSTD_isError(bound.error)) {
            set_zstd_error(ERR_GET_C_BOUNDS, bound.error);
            return NULL;
        }
    } else {
        bound = ZSTD_dParam_getBounds(parameter);
        if (ZSTD_isError(bound.error)) {
            set_zstd_error(ERR_GET_D_BOUNDS, bound.error);
            return NULL;
        }
    }

    ret = PyTuple_New(2);
    if (ret == NULL) {
        return NULL;
    }

    temp = PyLong_FromLong(bound.lowerBound);
    if (temp == NULL) {
        Py_DECREF(ret);
        return NULL;
    }
    PyTuple_SET_ITEM(ret, 0, temp);

    temp = PyLong_FromLong(bound.upperBound);
    if (temp == NULL) {
        Py_DECREF(ret);
        return NULL;
    }
    PyTuple_SET_ITEM(ret, 1, temp);

    return ret;
}

PyDoc_STRVAR(get_frame_size_doc,
"get_frame_size(frame_buffer)\n"
"----\n"
"Get the size of a zstd frame, including frame header and 4-byte checksum if it\n"
"has.\n\n"
"It will iterate all blocks' header within a frame, to accumulate the frame\n"
"size.\n\n"
"Arguments\n"
"frame_buffer: A bytes-like object, it should starts from the beginning of a\n"
"              frame, and contains at least one complete frame.");

static PyObject *
get_frame_size(PyObject *module, PyObject *args)
{
    Py_buffer frame_buffer;

    size_t frame_size;
    PyObject *ret;

    if (!PyArg_ParseTuple(args, "y*:get_frame_size", &frame_buffer)) {
        return NULL;
    }

    frame_size = ZSTD_findFrameCompressedSize(frame_buffer.buf, frame_buffer.len);
    if (ZSTD_isError(frame_size)) {
        set_zstd_error(ERR_GET_FRAME_SIZE, frame_size);
        goto error;
    }

    ret = PyLong_FromSize_t(frame_size);
    if (ret == NULL) {
        goto error;
    }
    goto success;

error:
    ret = NULL;
success:
    PyBuffer_Release(&frame_buffer);
    return ret;
}

PyDoc_STRVAR(_get_frame_info_doc,
"Internal function, get zstd frame infomation from a frame header.");

static PyObject *
_get_frame_info(PyObject *module, PyObject *args)
{
    Py_buffer frame_buffer;

    uint64_t content_size;
    char unknown_content_size;
    uint32_t dict_id;
    PyObject *temp;
    PyObject *ret = NULL;

    if (!PyArg_ParseTuple(args, "y*:_get_frame_info", &frame_buffer)) {
        return NULL;
    }

    /* ZSTD_getFrameContentSize */
    content_size = ZSTD_getFrameContentSize(frame_buffer.buf,
                                            frame_buffer.len);
    if (content_size == ZSTD_CONTENTSIZE_UNKNOWN) {
        unknown_content_size = 1;
    } else if (content_size == ZSTD_CONTENTSIZE_ERROR) {
        PyErr_SetString(static_state.ZstdError,
                        "Error when getting a zstd frame's decompressed size, "
                        "make sure the frame_buffer argument starts from the "
                        "beginning of a frame and its size larger than the "
                        "frame header (6~18 bytes).");
        goto error;
    } else {
        unknown_content_size = 0;
    }

    /* ZSTD_getDictID_fromFrame */
    dict_id = ZSTD_getDictID_fromFrame(frame_buffer.buf, frame_buffer.len);

    /* Build tuple */
    ret = PyTuple_New(2);
    if (ret == NULL) {
        goto error;
    }

    /* 0, content_size */
    if (unknown_content_size) {
        temp = Py_None;
        Py_INCREF(temp);
    } else {
        temp = PyLong_FromUnsignedLongLong(content_size);
        if (temp == NULL) {
            goto error;
        }
    }
    PyTuple_SET_ITEM(ret, 0, temp);

    /* 1, dict_id */
    temp = PyLong_FromUnsignedLong(dict_id);
    if (temp == NULL) {
        goto error;
    }
    PyTuple_SET_ITEM(ret, 1, temp);
    goto success;

error:
    Py_CLEAR(ret);
success:
    PyBuffer_Release(&frame_buffer);
    return ret;
}

/* Goto `error` label if (RET_VALUE < 0 || RET_VALUE > UPPER_BOUND),
   RET_VALUE/UPPER_BOUND should be Py_ssize_t. Using macro rather than inlined
   function, the performance of string concatenation is better. */
#define CHECK_STREAM_RETURN_VALUE(FUN_NAME, RET_VALUE, UPPER_BOUND) \
    do {                                                 \
        if (RET_VALUE < 0 || RET_VALUE > UPPER_BOUND) {  \
            /* Check PyLong_AsSsize_t() failed */        \
            if (RET_VALUE == -1 && PyErr_Occurred()) {   \
                PyErr_SetString(                         \
                    PyExc_TypeError,                     \
                    FUN_NAME " returned wrong type.");   \
                goto error;                              \
            }                                            \
                                                         \
            PyErr_Format(                                \
                PyExc_ValueError,                        \
                FUN_NAME " returned invalid length %zd " \
                "(should be 0 <= value <= %zd)",         \
                RET_VALUE, UPPER_BOUND);                 \
            goto error;                                  \
        }                                                \
    } while(0)

/* Write all output data to output_stream */
FORCE_INLINE int
write_to_output(PyObject *output_stream, ZSTD_outBuffer *out)
{
    PyObject *memoryview;
    PyObject *write_ret;
    size_t write_pos = 0;

    while (write_pos < out->pos) {
        const Py_ssize_t left_bytes = out->pos - write_pos;

        /* Invoke .write() method */
        memoryview = PyMemoryView_FromMemory((char*) out->dst + write_pos,
                                             left_bytes, PyBUF_READ);
        if (memoryview == NULL) {
            goto error;
        }

        write_ret = PyObject_CallMethodObjArgs(output_stream,
                                               static_state.str_write,
                                               memoryview, NULL);
        Py_DECREF(memoryview);

        if (write_ret == NULL) {
            goto error;
        } else if (write_ret == Py_None) {
            /* The raw stream is set not to block and no single
               byte could be readily written to it */
            Py_DECREF(write_ret);

            /* Check signal, prevent loop infinitely. */
            if (PyErr_CheckSignals()) {
                goto error;
            }
            continue;
        } else {
            Py_ssize_t write_bytes = PyLong_AsSsize_t(write_ret);
            Py_DECREF(write_ret);

            /* Check wrong value, `goto error` if
               (write_bytes < 0 || write_bytes > left_bytes) */
            CHECK_STREAM_RETURN_VALUE("output_stream.write()",
                                      write_bytes, left_bytes);

            write_pos += (size_t) write_bytes;
        }
    }

    return 0;
error:
    return -1;
}

/* Invoke callback function */
FORCE_INLINE int
invoke_callback(PyObject *callback,
                ZSTD_inBuffer *in, size_t *callback_read_pos,
                ZSTD_outBuffer *out,
                const uint64_t total_input_size,
                const uint64_t total_output_size)
{
    PyObject *in_memoryview;
    PyObject *out_memoryview;
    PyObject *cb_args;
    PyObject *cb_ret;
    PyObject * const empty_memoryview = static_state.empty_readonly_memoryview;
    char cb_referenced;

    /* Input memoryview */
    const size_t in_size = in->size - *callback_read_pos;
    /* Only yield read data once */
    *callback_read_pos = in->size;

    if (in_size != 0) {
        in_memoryview = PyMemoryView_FromMemory((char*) in->src, in_size, PyBUF_READ);
        if (in_memoryview == NULL) {
            goto error;
        }
    } else {
        in_memoryview = empty_memoryview;
        Py_INCREF(in_memoryview);
    }

    /* Output memoryview */
    if (out->pos != 0) {
        out_memoryview = PyMemoryView_FromMemory(out->dst, out->pos, PyBUF_READ);
        if (out_memoryview == NULL) {
            Py_DECREF(in_memoryview);
            goto error;
        }
    } else {
        out_memoryview = empty_memoryview;
        Py_INCREF(out_memoryview);
    }

    /* callback function arguments */
    cb_args = Py_BuildValue("(KKOO)",
                            total_input_size, total_output_size,
                            in_memoryview, out_memoryview);
    if (cb_args == NULL) {
        Py_DECREF(in_memoryview);
        Py_DECREF(out_memoryview);
        goto error;
    }

    /* Callback */
    cb_ret = PyObject_CallObject(callback, cb_args);

    cb_referenced = (in_memoryview != empty_memoryview && Py_REFCNT(in_memoryview) > 2) ||
                    (out_memoryview != empty_memoryview && Py_REFCNT(out_memoryview) > 2);
    Py_DECREF(cb_args);
    Py_DECREF(in_memoryview);
    Py_DECREF(out_memoryview);

    if (cb_ret == NULL) {
        goto error;
    }
    Py_DECREF(cb_ret);

    /* memoryview object was referenced in callback function */
    if (cb_referenced) {
        PyErr_SetString(PyExc_RuntimeError,
                        "The third and fourth parameters of callback function "
                        "are memoryview objects. If want to reference them "
                        "outside the callback function, convert them to bytes "
                        "object using bytes() function.");
        goto error;
    }

    return 0;
error:
    return -1;
}

/* Return NULL on failure */
FORCE_INLINE PyObject *
build_return_tuple(uint64_t total_input_size, uint64_t total_output_size)
{
    PyObject *ret, *temp;

    ret = PyTuple_New(2);
    if (ret == NULL) {
        return NULL;
    }

    temp = PyLong_FromUnsignedLongLong(total_input_size);
    if (temp == NULL) {
        Py_DECREF(ret);
        return NULL;
    }
    PyTuple_SET_ITEM(ret, 0, temp);

    temp = PyLong_FromUnsignedLongLong(total_output_size);
    if (temp == NULL) {
        Py_DECREF(ret);
        return NULL;
    }
    PyTuple_SET_ITEM(ret, 1, temp);

    return ret;
}

PyDoc_STRVAR(compress_stream_doc,
"compress_stream(input_stream, output_stream, *,\n"
"                level_or_option=None, zstd_dict=None,\n"
"                pledged_input_size=None,\n"
"                read_size=131072, write_size=131591,\n"
"                callback=None)\n"
"----\n"
"Compresses input_stream and writes the compressed data to output_stream, it\n"
"doesn't close the streams.\n\n"
"If input stream is b'', nothing will be written to output stream.\n\n"
"Return a tuple, (total_input, total_output), the items are int objects.\n\n"
"Arguments\n"
"input_stream: Input stream that has a .readinto(b) method.\n"
"output_stream: Output stream that has a .write(b) method. If use callback\n"
"    function, this argument can be None.\n"
"level_or_option: When it's an int object, it represents the compression\n"
"    level. When it's a dict object, it contains advanced compression\n"
"    parameters.\n"
"zstd_dict: A ZstdDict object, pre-trained zstd dictionary.\n"
"pledged_input_size: If set this argument to the size of input data, the size\n"
"    will be written into frame header. If the actual input data doesn't match\n"
"    it, a ZstdError will be raised.\n"
"read_size: Input buffer size, in bytes.\n"
"write_size: Output buffer size, in bytes.\n"
"callback: A callback function that accepts four parameters:\n"
"    (total_input, total_output, read_data, write_data), the first two are\n"
"    int objects, the last two are readonly memoryview objects."
);

static PyObject *
compress_stream(PyObject *module, PyObject *args, PyObject *kwargs)
{
    static char *kwlist[] = {"input_stream", "output_stream",
                             "level_or_option", "zstd_dict",
                             "pledged_input_size", "read_size", "write_size",
                             "callback", NULL};
    PyObject *input_stream;
    PyObject *output_stream;
    PyObject *level_or_option = Py_None;
    PyObject *zstd_dict = Py_None;
    PyObject *pledged_input_size = Py_None;
    Py_ssize_t read_size = ZSTD_CStreamInSize();
    Py_ssize_t write_size = ZSTD_CStreamOutSize();
    PyObject *callback = Py_None;

    /* If fails, modify value in __init__.pyi and doc. */
    assert(read_size == 131072);
    assert(write_size == 131591);

    size_t zstd_ret;
    PyObject *temp;
    ZstdCompressor self = {0};
    int compress_level = 0; /* 0 means use zstd's default compression level */
    uint64_t pledged_size_value = ZSTD_CONTENTSIZE_UNKNOWN;
    ZSTD_inBuffer in = {.src = NULL};
    ZSTD_outBuffer out = {.dst = NULL};
    PyObject *in_memoryview = NULL;
    uint64_t total_input_size = 0;
    uint64_t total_output_size = 0;
    PyObject *ret = NULL;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs,
                                     "OO|$OOOnnO:compress_stream", kwlist,
                                     &input_stream, &output_stream,
                                     &level_or_option, &zstd_dict,
                                     &pledged_input_size, &read_size, &write_size,
                                     &callback)) {
        return NULL;
    }

    /* Check parameters */
    if (!PyObject_HasAttr(input_stream, static_state.str_readinto)) {
        PyErr_SetString(PyExc_TypeError,
                        "input_stream argument should have a .readinto(b) method.");
        return NULL;
    }

    if (output_stream != Py_None) {
        if (!PyObject_HasAttr(output_stream, static_state.str_write)) {
            PyErr_SetString(PyExc_TypeError,
                            "output_stream argument should have a .write(b) method.");
            return NULL;
        }
    } else {
        if (callback == Py_None) {
            PyErr_SetString(PyExc_TypeError,
                            "At least one of output_stream argument and "
                            "callback argument should be non-None.");
            return NULL;
        }
    }

    if (pledged_input_size != Py_None) {
        pledged_size_value = PyLong_AsUnsignedLongLong(pledged_input_size);
        if (pledged_size_value == (uint64_t)-1 && PyErr_Occurred()) {
            PyErr_SetString(PyExc_ValueError,
                            "pledged_input_size argument should be 64-bit "
                            "unsigned integer value.");
            return NULL;
        }
    }

    if (read_size <= 0 || write_size <= 0) {
        PyErr_SetString(PyExc_ValueError,
                        "read_size argument and write_size argument should "
                        "be positive numbers.");
        return NULL;
    }

    /* Initialize & set ZstdCompressor */
    self.cctx = ZSTD_createCCtx();
    if (self.cctx == NULL) {
        PyErr_SetString(static_state.ZstdError,
                        "Unable to create ZSTD_CCtx instance.");
        goto error;
    }

    if (level_or_option != Py_None) {
        if (set_c_parameters(&self, level_or_option, &compress_level) < 0) {
            goto error;
        }
    }

    if (zstd_dict != Py_None) {
        if (load_c_dict(&self, zstd_dict, compress_level) < 0) {
            goto error;
        }
    }

    if (pledged_size_value != ZSTD_CONTENTSIZE_UNKNOWN) {
        zstd_ret = ZSTD_CCtx_setPledgedSrcSize(self.cctx, pledged_size_value);
        if (ZSTD_isError(zstd_ret)) {
            set_zstd_error(ERR_COMPRESS, zstd_ret);
            goto error;
        }
    }

    /* Input buffer, in.size and in.pos will be set later. */
    in.src = PyMem_Malloc(read_size);
    if (in.src == NULL) {
        PyErr_NoMemory();
        goto error;
    }
    in_memoryview = PyMemoryView_FromMemory((char*) in.src, read_size, PyBUF_WRITE);
    if (in_memoryview == NULL) {
        goto error;
    }

    /* Output buffer, out.pos will be set later. */
    out.dst = PyMem_Malloc(write_size);
    if (out.dst == NULL) {
        PyErr_NoMemory();
        goto error;
    }
    out.size = write_size;

    /* Read */
    while (1) {
        Py_ssize_t read_bytes;
        size_t callback_read_pos;
        ZSTD_EndDirective end_directive;

        /* Interrupted by a signal, put here for .readinto() returning None. */
        if (PyErr_CheckSignals()) {
            goto error;
        }

        /* Invoke .readinto() method */
        temp = PyObject_CallMethodObjArgs(input_stream,
                                          static_state.str_readinto,
                                          in_memoryview, NULL);
        if (temp == NULL) {
            goto error;
        } else if (temp == Py_None) {
            /* Non-blocking mode and no bytes are available */
            Py_DECREF(temp);
            continue;
        } else {
            read_bytes = PyLong_AsSsize_t(temp);
            Py_DECREF(temp);

            /* Check wrong value, `goto error` if
               (read_bytes < 0 || read_bytes > read_size) */
            CHECK_STREAM_RETURN_VALUE("input_stream.readinto()",
                                      read_bytes, read_size);

            /* Don't generate empty frame */
            if (read_bytes == 0 && total_input_size == 0) {
                break;
            }
            total_input_size += (size_t) read_bytes;
        }

        in.size = (size_t) read_bytes;
        in.pos = 0;
        callback_read_pos = 0;
        end_directive = (read_bytes == 0) ? ZSTD_e_end : ZSTD_e_continue;

        /* Compress & write */
        while (1) {
            /* Output position */
            out.pos = 0;

            /* Compress */
            Py_BEGIN_ALLOW_THREADS
            if (self.use_multithreaded && end_directive == ZSTD_e_continue) {
                do {
                    zstd_ret = ZSTD_compressStream2(self.cctx, &out, &in, ZSTD_e_continue);
                } while (out.pos != out.size && in.pos != in.size && !ZSTD_isError(zstd_ret));
            } else {
                zstd_ret = ZSTD_compressStream2(self.cctx, &out, &in, end_directive);
            }
            Py_END_ALLOW_THREADS

            if (ZSTD_isError(zstd_ret)) {
                set_zstd_error(ERR_COMPRESS, zstd_ret);
                goto error;
            }

            /* Accumulate output bytes */
            total_output_size += out.pos;

            /* Write all output to output_stream */
            if (output_stream != Py_None) {
                if (write_to_output(output_stream, &out) < 0) {
                    goto error;
                }
            }

            /* Invoke callback */
            if (callback != Py_None) {
                if (invoke_callback(callback, &in, &callback_read_pos,
                                    &out, total_input_size, total_output_size) < 0) {
                    goto error;
                }
            }

            /* Finished */
            if (end_directive == ZSTD_e_continue) {
                if (in.pos == in.size) {
                    break;
                }
            } else {
                if (zstd_ret == 0) {
                    break;
                }
            }
        } /* Compress & write loop */

        /* Input stream ended */
        if (read_bytes == 0) {
            break;
        }
    } /* Read loop */

    /* Return value */
    ret = build_return_tuple(total_input_size, total_output_size);
    if (ret == NULL) {
        goto error;
    }

    goto success;

error:
    Py_CLEAR(ret);

success:
    ZSTD_freeCCtx(self.cctx);

    Py_XDECREF(in_memoryview);
    PyMem_Free((char*) in.src);
    PyMem_Free(out.dst);

    return ret;
}

PyDoc_STRVAR(decompress_stream_doc,
"decompress_stream(input_stream, output_stream, *,\n"
"                  zstd_dict=None, option=None,\n"
"                  read_size=131075, write_size=131072,\n"
"                  callback=None)\n"
"----\n"
"Decompresses input_stream and writes the decompressed data to output_stream,\n"
"it doesn't close the streams.\n\n"
"Supports multiple concatenated frames.\n\n"
"Return a tuple, (total_input, total_output), the items are int objects.\n\n"
"Arguments\n"
"input_stream: Input stream that has a .readinto(b) method.\n"
"output_stream: Output stream that has a .write(b) method. If use callback\n"
"    function, this argument can be None.\n"
"zstd_dict: A ZstdDict object, pre-trained zstd dictionary.\n"
"option: A dict object, contains advanced decompression parameters.\n"
"read_size: Input buffer size, in bytes.\n"
"write_size: Output buffer size, in bytes.\n"
"callback: A callback function that accepts four parameters:\n"
"    (total_input, total_output, read_data, write_data), the first two are\n"
"    int objects, the last two are readonly memoryview objects."
);

static PyObject *
decompress_stream(PyObject *module, PyObject *args, PyObject *kwargs)
{
    static char *kwlist[] = {"input_stream", "output_stream",
                             "zstd_dict", "option",
                             "read_size", "write_size",
                             "callback", NULL};
    PyObject *input_stream;
    PyObject *output_stream;
    PyObject *zstd_dict = Py_None;
    PyObject *option = Py_None;
    Py_ssize_t read_size = ZSTD_DStreamInSize();
    Py_ssize_t write_size = ZSTD_DStreamOutSize();
    PyObject *callback = Py_None;

    /* If fails, modify value in __init__.pyi and doc. */
    assert(read_size == 131075);
    assert(write_size == 131072);

    size_t zstd_ret;
    PyObject *temp;
    ZstdDecompressor self = {0};
    ZSTD_inBuffer in = {.src = NULL};
    ZSTD_outBuffer out = {.dst = NULL};
    PyObject *in_memoryview = NULL;
    uint64_t total_input_size = 0;
    uint64_t total_output_size = 0;
    PyObject *ret = NULL;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs,
                                     "OO|$OOnnO:decompress_stream", kwlist,
                                     &input_stream, &output_stream,
                                     &zstd_dict, &option,
                                     &read_size, &write_size,
                                     &callback)) {
        return NULL;
    }

    /* Check parameters */
    if (!PyObject_HasAttr(input_stream, static_state.str_readinto)) {
        PyErr_SetString(PyExc_TypeError,
                        "input_stream argument should have a .readinto(b) method.");
        return NULL;
    }

    if (output_stream != Py_None) {
        if (!PyObject_HasAttr(output_stream, static_state.str_write)) {
            PyErr_SetString(PyExc_TypeError,
                            "output_stream argument should have a .write(b) method.");
            return NULL;
        }
    } else {
        if (callback == Py_None) {
            PyErr_SetString(PyExc_TypeError,
                            "At least one of output_stream argument and "
                            "callback argument should be non-None.");
            return NULL;
        }
    }

    if (read_size <= 0 || write_size <= 0) {
        PyErr_SetString(PyExc_ValueError,
                        "read_size argument and write_size argument should "
                        "be positive numbers.");
        return NULL;
    }

    /* Initialize & set ZstdDecompressor */
    self.dctx = ZSTD_createDCtx();
    if (self.dctx == NULL) {
        PyErr_SetString(static_state.ZstdError,
                        "Unable to create ZSTD_DCtx instance.");
        goto error;
    }
    self.at_frame_edge = 1;

    if (zstd_dict != Py_None) {
        if (load_d_dict(self.dctx, zstd_dict) < 0) {
            goto error;
        }
    }

    if (option != Py_None) {
        if (set_d_parameters(self.dctx, option) < 0) {
            goto error;
        }
    }

    /* Input buffer, in.size and in.pos will be set later. */
    in.src = PyMem_Malloc(read_size);
    if (in.src == NULL) {
        PyErr_NoMemory();
        goto error;
    }
    in_memoryview = PyMemoryView_FromMemory((char*) in.src, read_size, PyBUF_WRITE);
    if (in_memoryview == NULL) {
        goto error;
    }

    /* Output buffer, out.pos will be set later. */
    out.dst = PyMem_Malloc(write_size);
    if (out.dst == NULL) {
        PyErr_NoMemory();
        goto error;
    }
    out.size = write_size;

    /* Read */
    while (1) {
        Py_ssize_t read_bytes;
        size_t callback_read_pos;

        /* Interrupted by a signal, put here for .readinto() returning None. */
        if (PyErr_CheckSignals()) {
            goto error;
        }

        /* Invoke .readinto() method */
        temp = PyObject_CallMethodObjArgs(input_stream,
                                          static_state.str_readinto,
                                          in_memoryview, NULL);
        if (temp == NULL) {
            goto error;
        } else if (temp == Py_None) {
            /* Non-blocking mode and no bytes are available */
            Py_DECREF(temp);
            continue;
        } else {
            read_bytes = PyLong_AsSsize_t(temp);
            Py_DECREF(temp);

            /* Check wrong value, `goto error` if
               (read_bytes < 0 || read_bytes > read_size) */
            CHECK_STREAM_RETURN_VALUE("input_stream.readinto()",
                                      read_bytes, read_size);

            total_input_size += (size_t) read_bytes;
        }

        in.size = (size_t) read_bytes;
        in.pos = 0;
        callback_read_pos = 0;

        /* Decompress & write */
        while (1) {
            /* AFE check for setting .at_frame_edge flag, search "AFE check" in
               this file to see details. */
            if (self.at_frame_edge && in.pos == in.size) {
                break;
            }

            /* Output position */
            out.pos = 0;

            /* Decompress */
            Py_BEGIN_ALLOW_THREADS
            zstd_ret = ZSTD_decompressStream(self.dctx, &out, &in);
            Py_END_ALLOW_THREADS

            if (ZSTD_isError(zstd_ret)) {
                set_zstd_error(ERR_DECOMPRESS, zstd_ret);
                goto error;
            }

            /* Set .af_frame_edge flag */
            self.at_frame_edge = (zstd_ret == 0) ? 1 : 0;

            /* Accumulate output bytes */
            total_output_size += out.pos;

            /* Write all output to output_stream */
            if (output_stream != Py_None) {
                if (write_to_output(output_stream, &out) < 0) {
                    goto error;
                }
            }

            /* Invoke callback */
            if (callback != Py_None) {
                if (invoke_callback(callback, &in, &callback_read_pos,
                                    &out, total_input_size, total_output_size) < 0) {
                    goto error;
                }
            }

            /* Finished. When a frame is fully decoded, but not fully flushed,
               the last byte is kept as hostage, it will be released when all
               output is flushed. */
            if (in.pos == in.size) {
                /* If input stream ends in an incomplete frame, output as much
                   as possible. */
                if (read_bytes == 0 &&
                    self.at_frame_edge == 0 &&
                    out.pos == out.size)
                {
                    continue;
                }

                break;
            }
        } /* Decompress & write loop */

        /* Input stream ended */
        if (read_bytes == 0) {
            /* Check data integrity. at_frame_edge flag is 1 when both input
               and output streams are at a frame edge. */
            if (self.at_frame_edge == 0) {
                PyErr_Format(static_state.ZstdError,
                             "Decompression failed: zstd data ends in an "
                             "incomplete frame, maybe the input data was "
                             "truncated. Total input %llu bytes, total output "
                             "%llu bytes.",
                             total_input_size, total_output_size);
                goto error;
            }
            break;
        }
    } /* Read loop */

    /* Return value */
    ret = build_return_tuple(total_input_size, total_output_size);
    if (ret == NULL) {
        goto error;
    }

    goto success;

error:
    Py_CLEAR(ret);

success:
    ZSTD_freeDCtx(self.dctx);

    Py_XDECREF(in_memoryview);
    PyMem_Free((char*) in.src);
    PyMem_Free(out.dst);

    return ret;
}

static PyMethodDef _zstd_methods[] = {
    {"decompress", (PyCFunction)decompress, METH_VARARGS|METH_KEYWORDS, decompress_doc},
    {"_train_dict", (PyCFunction)_train_dict, METH_VARARGS, _train_dict_doc},
    {"_finalize_dict", (PyCFunction)_finalize_dict, METH_VARARGS, _finalize_dict_doc},
    {"_get_param_bounds", (PyCFunction)_get_param_bounds, METH_VARARGS, _get_param_bounds_doc},
    {"get_frame_size", (PyCFunction)get_frame_size, METH_VARARGS, get_frame_size_doc},
    {"_get_frame_info", (PyCFunction)_get_frame_info, METH_VARARGS, _get_frame_info_doc},
    {"compress_stream", (PyCFunction)compress_stream, METH_VARARGS|METH_KEYWORDS, compress_stream_doc},
    {"decompress_stream", (PyCFunction)decompress_stream, METH_VARARGS|METH_KEYWORDS, decompress_stream_doc},
    {NULL}
};

/* --------------------
     Initialize code
   -------------------- */
static int
_zstd_traverse(PyObject *module, visitproc visit, void *arg)
{
    Py_VISIT(static_state.ZstdError);
    Py_VISIT(static_state.ZstdDict_type);
    Py_VISIT(static_state.ZstdCompressor_type);
    Py_VISIT(static_state.RichMemZstdCompressor_type);
    Py_VISIT(static_state.ZstdDecompressor_type);
    Py_VISIT(static_state.EndlessZstdDecompressor_type);
    Py_VISIT(static_state.empty_bytes);
    Py_VISIT(static_state.empty_readonly_memoryview);
    Py_VISIT(static_state.str_readinto);
    Py_VISIT(static_state.str_write);
    return 0;
}

static int
_zstd_clear(PyObject *module)
{
    Py_CLEAR(static_state.ZstdError);
    Py_CLEAR(static_state.ZstdDict_type);
    Py_CLEAR(static_state.ZstdCompressor_type);
    Py_CLEAR(static_state.RichMemZstdCompressor_type);
    Py_CLEAR(static_state.ZstdDecompressor_type);
    Py_CLEAR(static_state.EndlessZstdDecompressor_type);
    Py_CLEAR(static_state.empty_bytes);
    Py_CLEAR(static_state.empty_readonly_memoryview);
    Py_CLEAR(static_state.str_readinto);
    Py_CLEAR(static_state.str_write);
    return 0;
}

static void
_zstd_free(void *module)
{
    _zstd_clear((PyObject *)module);
}

static PyModuleDef _zstdmodule = {
    PyModuleDef_HEAD_INIT,
    .m_name = "_zstd",
    .m_size = -1,
    .m_methods = _zstd_methods,
    .m_traverse = _zstd_traverse,
    .m_clear = _zstd_clear,
    .m_free = _zstd_free
};

static inline int
add_constants(PyObject *module)
{
    PyObject *temp;

    /* Add zstd parameters */
    if (add_parameters(module) < 0) {
        return -1;
    }

    /* ZSTD_strategy enum */
    ADD_INT_PREFIX_MACRO(module, ZSTD_fast);
    ADD_INT_PREFIX_MACRO(module, ZSTD_dfast);
    ADD_INT_PREFIX_MACRO(module, ZSTD_greedy);
    ADD_INT_PREFIX_MACRO(module, ZSTD_lazy);
    ADD_INT_PREFIX_MACRO(module, ZSTD_lazy2);
    ADD_INT_PREFIX_MACRO(module, ZSTD_btlazy2);
    ADD_INT_PREFIX_MACRO(module, ZSTD_btopt);
    ADD_INT_PREFIX_MACRO(module, ZSTD_btultra);
    ADD_INT_PREFIX_MACRO(module, ZSTD_btultra2);

    /* _ZSTD_defaultCLevel, ZSTD_defaultCLevel() was added in zstd v1.5.0. */
#if ZSTD_VERSION_NUMBER < 10500
    temp = PyLong_FromLong(ZSTD_CLEVEL_DEFAULT);
#else
    temp = PyLong_FromLong(ZSTD_defaultCLevel());
#endif

    if (PyModule_AddObject(module, "_ZSTD_defaultCLevel", temp) < 0) {
        Py_XDECREF(temp);
        return -1;
    }

    /* _ZSTD_minCLevel */
    temp = PyLong_FromLong(ZSTD_minCLevel());
    if (PyModule_AddObject(module, "_ZSTD_minCLevel", temp) < 0) {
        Py_XDECREF(temp);
        return -1;
    }

    /* _ZSTD_maxCLevel */
    temp = PyLong_FromLong(ZSTD_maxCLevel());
    if (PyModule_AddObject(module, "_ZSTD_maxCLevel", temp) < 0) {
        Py_XDECREF(temp);
        return -1;
    }

    /* _ZSTD_DStreamInSize */
    temp = PyLong_FromSize_t(ZSTD_DStreamInSize());
    if (PyModule_AddObject(module, "_ZSTD_DStreamInSize", temp) < 0) {
        Py_XDECREF(temp);
        return -1;
    }

    return 0;
}

static inline int
add_type_to_module(PyObject *module, const char *name,
                   PyType_Spec *type_spec, PyTypeObject **dest)
{
    PyObject *temp;

    temp = PyType_FromSpec(type_spec);
    if (PyModule_AddObject(module, name, temp) < 0) {
        Py_XDECREF(temp);
        return -1;
    }

    Py_INCREF(temp);
    *dest = (PyTypeObject*) temp;

    return 0;
}

static inline int
add_constant_to_type(PyTypeObject *type, const char *name, const long value)
{
    PyObject *temp;

    temp = PyLong_FromLong(value);
    if (temp == NULL) {
        return -1;
    }

    if (PyObject_SetAttrString((PyObject*) type, name, temp) < 0) {
        Py_DECREF(temp);
        return -1;
    }
    Py_DECREF(temp);

    return 0;
}

static PyObject *
get_zstd_version_info(void)
{
    const uint32_t ver = ZSTD_versionNumber();
    uint32_t major, minor, release;
    PyObject *ret;

    major = ver / 10000;
    minor = (ver / 100) % 100;
    release = ver % 100;

    ret = PyTuple_New(3);
    if (ret == NULL) {
        return NULL;
    }

    PyTuple_SET_ITEM(ret, 0, PyLong_FromUnsignedLong(major));
    PyTuple_SET_ITEM(ret, 1, PyLong_FromUnsignedLong(minor));
    PyTuple_SET_ITEM(ret, 2, PyLong_FromUnsignedLong(release));
    return ret;
}

PyMODINIT_FUNC
PyInit__zstd(void)
{
    PyObject *module;
    PyObject *temp;
    ZSTD_bounds param_bounds;

    /* Keep this first, for error label. */
    module = PyModule_Create(&_zstdmodule);
    if (!module) {
        goto error;
    }

    /* Reusable objects & variables */
    static_state.empty_bytes = PyBytes_FromStringAndSize(NULL, 0);
    if (static_state.empty_bytes == NULL) {
        goto error;
    }

    static_state.empty_readonly_memoryview =
                PyMemoryView_FromMemory((char*) &static_state, 0, PyBUF_READ);
    if (static_state.empty_readonly_memoryview == NULL) {
        goto error;
    }

    static_state.str_readinto = PyUnicode_FromString("readinto");
    if (static_state.str_readinto == NULL) {
        goto error;
    }

    static_state.str_write = PyUnicode_FromString("write");
    if (static_state.str_write == NULL) {
        goto error;
    }

    param_bounds = ZSTD_cParam_getBounds(ZSTD_c_nbWorkers);
    if (ZSTD_isError(param_bounds.error)) {
        goto error;
    }
    static_state.support_multithreaded = (param_bounds.upperBound != 0 ||
                                          param_bounds.lowerBound != 0);

    /* Constants */
    if (add_constants(module) < 0) {
        goto error;
    }

    /* ZstdError */
    static_state.ZstdError = PyErr_NewExceptionWithDoc(
                                  "_zstd.ZstdError",
                                  "Call to the underlying zstd library failed.",
                                  NULL, NULL);
    if (static_state.ZstdError == NULL) {
        goto error;
    }

    Py_INCREF(static_state.ZstdError);
    if (PyModule_AddObject(module, "ZstdError", static_state.ZstdError) < 0) {
        Py_DECREF(static_state.ZstdError);
        goto error;
    }

    /* ZstdDict */
    if (add_type_to_module(module,
                           "ZstdDict",
                           &zstddict_type_spec,
                           &static_state.ZstdDict_type) < 0) {
        goto error;
    }

    /* ZstdCompressor */
    if (add_type_to_module(module,
                           "ZstdCompressor",
                           &zstdcompressor_type_spec,
                           &static_state.ZstdCompressor_type) < 0) {
        goto error;
    }

    /* Add EndDirective enum to ZstdCompressor */
    if (add_constant_to_type(static_state.ZstdCompressor_type,
                             "CONTINUE",
                             ZSTD_e_continue) < 0) {
        goto error;
    }

    if (add_constant_to_type(static_state.ZstdCompressor_type,
                             "FLUSH_BLOCK",
                             ZSTD_e_flush) < 0) {
        goto error;
    }

    if (add_constant_to_type(static_state.ZstdCompressor_type,
                             "FLUSH_FRAME",
                             ZSTD_e_end) < 0) {
        goto error;
    }

    /* RichMemZstdCompressor */
    if (add_type_to_module(module,
                           "RichMemZstdCompressor",
                           &richmem_zstdcompressor_type_spec,
                           &static_state.RichMemZstdCompressor_type) < 0) {
        goto error;
    }

    /* ZstdDecompressor */
    if (add_type_to_module(module,
                           "ZstdDecompressor",
                           &ZstdDecompressor_type_spec,
                           &static_state.ZstdDecompressor_type) < 0) {
        goto error;
    }

    /* EndlessZstdDecompressor */
    if (add_type_to_module(module,
                           "EndlessZstdDecompressor",
                           &EndlessZstdDecompressor_type_spec,
                           &static_state.EndlessZstdDecompressor_type) < 0) {
        goto error;
    }

    /* zstd_version, ZSTD_versionString() requires zstd v1.3.0+ */
    temp = PyUnicode_FromString(ZSTD_versionString());
    if (PyModule_AddObject(module, "zstd_version", temp) < 0) {
        Py_XDECREF(temp);
        goto error;
    }

    /* zstd_version_info, a tuple. */
    temp = get_zstd_version_info();
    if (PyModule_AddObject(module, "zstd_version_info", temp) < 0) {
        Py_XDECREF(temp);
        goto error;
    }

    return module;

error:
    _zstd_clear(NULL);
    Py_XDECREF(module);

    return NULL;
}
