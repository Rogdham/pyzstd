/* pyzstd module for Python 3.5+
   https://github.com/animalize/pyzstd */

#ifndef PYZSTD_H_INCLUDED
#define PYZSTD_H_INCLUDED

#include "stdint.h"     /* For MSVC + Python 3.5 */

#include "Python.h"
#include "pythread.h"   /* For Python 3.5 */
#include "structmember.h"

#include "zstd.h"
#include "zdict.h"

#if ZSTD_VERSION_NUMBER < 10400
    #error "pyzstd module requires zstd v1.4.0+"
#endif

/* Added in Python 3.7 */
#ifndef Py_UNREACHABLE
    #define Py_UNREACHABLE() assert(0)
#endif

/* PyType_GetModuleByDef() function was added in Python 3.11.
   0x030B00B1 is CPython 3.11 Beta1. */
#if defined(USE_MULTI_PHASE_INIT) && PY_VERSION_HEX < 0x030B00B1
    #undef USE_MULTI_PHASE_INIT
#endif

/* Forward declaration */
typedef struct _zstd_state _zstd_state;

typedef struct {
    PyObject_HEAD

    /* Thread lock for generating ZSTD_CDict/ZSTD_DDict */
    PyThread_type_lock lock;

    /* Reuseable compress/decompress dictionary, they are created once and
       can be shared by multiple threads concurrently, since its usage is
       read-only.
       c_dicts is a dict, int(compressionLevel):PyCapsule(ZSTD_CDict*) */
    ZSTD_DDict *d_dict;
    PyObject *c_dicts;

    /* Content of the dictionary, bytes object. */
    PyObject *dict_content;
    /* Dictionary id */
    uint32_t dict_id;

    /* __init__ has been called, 0 or 1. */
    int inited;

#ifdef USE_MULTI_PHASE_INIT
    _zstd_state *module_state;
#endif
} ZstdDict;

typedef struct {
    PyObject_HEAD

    /* Thread lock for compressing */
    PyThread_type_lock lock;

    /* Compression context */
    ZSTD_CCtx *cctx;

    /* ZstdDict object in use */
    PyObject *dict;

    /* Last mode, initialized to ZSTD_e_end */
    int last_mode;

    /* (nbWorker >= 1) ? 1 : 0 */
    int use_multithread;

    /* Compression level */
    int compression_level;

    /* __init__ has been called, 0 or 1. */
    int inited;

#ifdef USE_MULTI_PHASE_INIT
    _zstd_state *module_state;
#endif
} ZstdCompressor;

typedef struct {
    PyObject_HEAD

    /* Thread lock for compressing */
    PyThread_type_lock lock;

    /* Decompression context */
    ZSTD_DCtx *dctx;

    /* ZstdDict object in use */
    PyObject *dict;

    /* Unconsumed input data */
    char *input_buffer;
    size_t input_buffer_size;
    size_t in_begin, in_end;

    /* Unused data */
    PyObject *unused_data;

    /* 0 if decompressor has (or may has) unconsumed input data, 0 or 1. */
    char needs_input;

    /* For EndlessZstdDecompressor, 0 or 1.
       1 when both input and output streams are at a frame edge, means a
       frame is completely decoded and fully flushed, or the decompressor
       just be initialized. */
    char at_frame_edge;

    /* For ZstdDecompressor, 0 or 1.
       1 means the end of the first frame has been reached. */
    char eof;

    /* Used for fast reset above three variables */
    char _unused_char_for_align;

    /* __init__ has been called, 0 or 1. */
    int inited;

#ifdef USE_MULTI_PHASE_INIT
    _zstd_state *module_state;
#endif
} ZstdDecompressor;

struct _zstd_state {
    PyObject *empty_bytes;
    PyObject *empty_readonly_memoryview;
    PyObject *str_readinto;
    PyObject *str_write;

    PyTypeObject *ZstdDict_type;
    PyTypeObject *ZstdCompressor_type;
    PyTypeObject *RichMemZstdCompressor_type;
    PyTypeObject *ZstdDecompressor_type;
    PyTypeObject *EndlessZstdDecompressor_type;
    PyObject *ZstdError;

    PyTypeObject *CParameter_type;
    PyTypeObject *DParameter_type;
};

#ifdef USE_MULTI_PHASE_INIT
    /* For forward declaration of _zstdmodule */
    static inline PyModuleDef* _get_zstd_PyModuleDef();

    /* Get module state from a class type, and set it to supported object.
       Used in Py_tp_new or Py_tp_init. */
    #define SET_STATE_TO_OBJ(type, obj) \
        do {                                                              \
            PyModuleDef* const module_def = _get_zstd_PyModuleDef();      \
            PyObject *module = PyType_GetModuleByDef(type, module_def);   \
            if (module == NULL) {                                         \
                goto error;                                               \
            }                                                             \
            (obj)->module_state = (_zstd_state*)PyModule_GetState(module);\
            if ((obj)->module_state == NULL) {                            \
                goto error;                                               \
            }                                                             \
        } while (0)
    /* Get module state from module object */
    #define STATE_FROM_MODULE(module) \
        _zstd_state* const _module_state = (_zstd_state*)PyModule_GetState(module); \
        assert(_module_state != NULL);
    /* Get module state from supported object */
    #define STATE_FROM_OBJ(obj) \
        _zstd_state* const _module_state = (obj)->module_state; \
        assert(_module_state != NULL);
    /* Place as module state. Only as r-value. */
    #define MODULE_STATE (1 ? _module_state : NULL)
    /* Access a member of module state. Can be l-value or r-value. */
    #define MS_MEMBER(member) (_module_state->member)
#else  /* Don't use multi-phase init */
    static _zstd_state static_state;

    /* Get module state from a class type, and set it to supported object.
       Used in Py_tp_new or Py_tp_init. */
    #define SET_STATE_TO_OBJ(type, obj) ;
    /* Get module state from module object */
    #define STATE_FROM_MODULE(module) ;
    /* Get module state from supported object */
    #define STATE_FROM_OBJ(obj) ;
    /* Place as module state. Only as r-value. */
    #define MODULE_STATE (1 ? &static_state : NULL)
    /* Access a member of module state. Can be l-value or r-value. */
    #define MS_MEMBER(member) (static_state.member)
#endif

/* ----------------------------
     BlocksOutputBuffer code
   ---------------------------- */
typedef struct {
    /* List of blocks */
    PyObject *list;
    /* Number of whole allocated size */
    Py_ssize_t allocated;
    /* Max length of the buffer, negative number for unlimited length. */
    Py_ssize_t max_length;
} BlocksOutputBuffer;

static const char unable_allocate_msg[] = "Unable to allocate output buffer.";

/* Block size sequence */
#define KB (1024)
#define MB (1024*1024)
static const Py_ssize_t BUFFER_BLOCK_SIZE[] =
    /* If change this list, also change:
         The CFFI implementation
         OutputBufferTestCase unittest
       If change the first blocks's size, also change:
         _32_KiB in ZstdFile/SeekableZstdFile
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
    ... */

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
    if (0 <= max_length && max_length < init_size) {
        block_size = max_length;
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
    int append_ret;

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

    /* Append to list */
    append_ret = PyList_Append(buffer->list, b);
    Py_DECREF(b);
    if (append_ret < 0) {
        return -1;
    }

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

/* ------------------
     Global macros
   ------------------ */
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
#if defined(__GNUC__) || defined(__ICCARM__)
#  define FORCE_NO_INLINE static __attribute__((__noinline__))
#elif defined(_MSC_VER)
#  define FORCE_NO_INLINE static __declspec(noinline)
#else
#  define FORCE_NO_INLINE static
#endif

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
FORCE_NO_INLINE void
set_parameter_error(const _zstd_state* const state, int is_compress,
                    int key_v, int value_v)
{
    ParameterInfo const *list;
    int list_size;
    char const *name;
    char *type;
    ZSTD_bounds bounds;
    int i;
    char pos_msg[128];

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

    /* Unknown parameter */
    if (name == NULL) {
        PyOS_snprintf(pos_msg, sizeof(pos_msg),
                      "unknown parameter (key %d)", key_v);
        name = pos_msg;
    }

    /* Get parameter bounds */
    if (is_compress) {
        bounds = ZSTD_cParam_getBounds(key_v);
    } else {
        bounds = ZSTD_dParam_getBounds(key_v);
    }
    if (ZSTD_isError(bounds.error)) {
        PyErr_Format(state->ZstdError,
                     "Zstd %s parameter \"%s\" is invalid. (zstd v%s)",
                     type, name, ZSTD_versionString());
        return;
    }

    /* Error message */
    PyErr_Format(state->ZstdError,
                 "Error when setting zstd %s parameter \"%s\", it "
                 "should %d <= value <= %d, provided value is %d. "
                 "(zstd v%s, %d-bit build)",
                 type, name,
                 bounds.lowerBound, bounds.upperBound, value_v,
                 ZSTD_versionString(), 8*(int)sizeof(Py_ssize_t));
}

/* --------------------------------------
     Global functions
      - set parameters
      - load dictionary
      - reduce_cannot_pickle
   -------------------------------------- */
static const char init_twice_msg[] = "__init__ method is called twice.";

typedef enum {
    ERR_DECOMPRESS,
    ERR_COMPRESS,
    ERR_SET_PLEDGED_INPUT_SIZE,

    ERR_LOAD_D_DICT,
    ERR_LOAD_C_DICT,

    ERR_GET_C_BOUNDS,
    ERR_GET_D_BOUNDS,
    ERR_SET_C_LEVEL,

    ERR_TRAIN_DICT,
    ERR_FINALIZE_DICT
} error_type;

typedef enum {
    DICT_TYPE_DIGESTED = 0,
    DICT_TYPE_UNDIGESTED = 1,
    DICT_TYPE_PREFIX = 2
} dictionary_type;

/* Format error message and set ZstdError. */
FORCE_NO_INLINE void
set_zstd_error(const _zstd_state* const state,
               const error_type type, const size_t zstd_ret)
{
    char buf[128];
    char *msg;
    assert(ZSTD_isError(zstd_ret));

    switch (type)
    {
    case ERR_DECOMPRESS:
        msg = "Unable to decompress zstd data: %s";
        break;
    case ERR_COMPRESS:
        msg = "Unable to compress zstd data: %s";
        break;
    case ERR_SET_PLEDGED_INPUT_SIZE:
        msg = "Unable to set pledged uncompressed content size: %s";
        break;

    case ERR_LOAD_D_DICT:
        msg = "Unable to load zstd dictionary or prefix for decompression: %s";
        break;
    case ERR_LOAD_C_DICT:
        msg = "Unable to load zstd dictionary or prefix for compression: %s";
        break;

    case ERR_GET_C_BOUNDS:
        msg = "Unable to get zstd compression parameter bounds: %s";
        break;
    case ERR_GET_D_BOUNDS:
        msg = "Unable to get zstd decompression parameter bounds: %s";
        break;
    case ERR_SET_C_LEVEL:
        msg = "Unable to set zstd compression level: %s";
        break;

    case ERR_TRAIN_DICT:
        msg = "Unable to train zstd dictionary: %s";
        break;
    case ERR_FINALIZE_DICT:
        msg = "Unable to finalize zstd dictionary: %s";
        break;

    default:
        Py_UNREACHABLE();
    }
    PyOS_snprintf(buf, sizeof(buf), msg, ZSTD_getErrorName(zstd_ret));
    PyErr_SetString(state->ZstdError, buf);
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
            STATE_FROM_OBJ(self);
            PyErr_SetString(MS_MEMBER(ZstdError),
                            "Failed to create ZSTD_CDict instance from zstd "
                            "dictionary content. Maybe the content is corrupted.");
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
    ZSTD_DDict *ret;

    /* Already created */
    if (self->d_dict != NULL) {
        return self->d_dict;
    }

    ACQUIRE_LOCK(self);
    if (self->d_dict == NULL) {
        /* Create ZSTD_DDict instance from dictionary content */
        Py_BEGIN_ALLOW_THREADS
        self->d_dict = ZSTD_createDDict(PyBytes_AS_STRING(self->dict_content),
                                        Py_SIZE(self->dict_content));
        Py_END_ALLOW_THREADS

        if (self->d_dict == NULL) {
            STATE_FROM_OBJ(self);
            PyErr_SetString(MS_MEMBER(ZstdError),
                            "Failed to create ZSTD_DDict instance from zstd "
                            "dictionary content. Maybe the content is corrupted.");
        }
    }

    /* Don't lose any exception */
    ret = self->d_dict;
    RELEASE_LOCK(self);

    return ret;
}

/* Set compressLevel or compression parameters to compression context */
static int
set_c_parameters(ZstdCompressor *self, PyObject *level_or_option)
{
    size_t zstd_ret;
    STATE_FROM_OBJ(self);

    /* Integer compression level */
    if (PyLong_Check(level_or_option)) {
        const int level = _PyLong_AsInt(level_or_option);
        if (level == -1 && PyErr_Occurred()) {
            PyErr_SetString(PyExc_ValueError,
                            "Compression level should be 32-bit signed int value.");
            return -1;
        }

        /* Save for generating ZSTD_CDICT */
        self->compression_level = level;

        /* Set compressionLevel to compression context */
        zstd_ret = ZSTD_CCtx_setParameter(self->cctx,
                                          ZSTD_c_compressionLevel,
                                          level);

        /* Check error */
        if (ZSTD_isError(zstd_ret)) {
            set_zstd_error(MODULE_STATE, ERR_SET_C_LEVEL, zstd_ret);
            return -1;
        }
        return 0;
    }

    /* Options dict */
    if (PyDict_Check(level_or_option)) {
        PyObject *key, *value;
        Py_ssize_t pos = 0;

        while (PyDict_Next(level_or_option, &pos, &key, &value)) {
            /* Check key type */
            if (Py_TYPE(key) == MS_MEMBER(DParameter_type)) {
                PyErr_SetString(PyExc_TypeError,
                                "Key of compression option dict should "
                                "NOT be DParameter.");
                return -1;
            }

            /* Both key & value should be 32-bit signed int */
            const int key_v = _PyLong_AsInt(key);
            if (key_v == -1 && PyErr_Occurred()) {
                PyErr_SetString(PyExc_ValueError,
                                "Key of option dict should be 32-bit signed int value.");
                return -1;
            }

            const int value_v = _PyLong_AsInt(value);
            if (value_v == -1 && PyErr_Occurred()) {
                PyErr_SetString(PyExc_ValueError,
                                "Value of option dict should be 32-bit signed int value.");
                return -1;
            }

            if (key_v == ZSTD_c_compressionLevel) {
                /* Save for generating ZSTD_CDICT */
                self->compression_level = value_v;
            } else if (key_v == ZSTD_c_nbWorkers) {
                /* From zstd library doc:
                   1. When nbWorkers >= 1, triggers asynchronous mode when
                      used with ZSTD_compressStream2().
                   2, Default value is `0`, aka "single-threaded mode" : no
                      worker is spawned, compression is performed inside
                      caller's thread, all invocations are blocking. */
                if (value_v != 0) {
                    self->use_multithread = 1;
                }
            }

            /* Set parameter to compression context */
            zstd_ret = ZSTD_CCtx_setParameter(self->cctx, key_v, value_v);
            if (ZSTD_isError(zstd_ret)) {
                set_parameter_error(MODULE_STATE, 1, key_v, value_v);
                return -1;
            }
        }
        return 0;
    }

    /* Wrong type */
    PyErr_SetString(PyExc_TypeError, "level_or_option argument wrong type.");
    return -1;
}

/* Load dictionary or prefix to compression context */
static int
load_c_dict(ZstdCompressor *self, PyObject *dict)
{
    size_t zstd_ret;
    STATE_FROM_OBJ(self);
    ZstdDict *zd;
    int type, ret;

    /* Check ZstdDict */
    ret = PyObject_IsInstance(dict, (PyObject*)MS_MEMBER(ZstdDict_type));
    if (ret < 0) {
        return -1;
    } else if (ret > 0) {
        /* When compressing, use undigested dictionary by default. */
        zd = (ZstdDict*)dict;
        type = DICT_TYPE_UNDIGESTED;
        goto load;
    }

    /* Check (ZstdDict, type) */
    if (PyTuple_CheckExact(dict) && PyTuple_GET_SIZE(dict) == 2) {
        /* Check ZstdDict */
        ret = PyObject_IsInstance(PyTuple_GET_ITEM(dict, 0),
                                  (PyObject*)MS_MEMBER(ZstdDict_type));
        if (ret < 0) {
            return -1;
        } else if (ret > 0) {
            /* type == -1 may indicate an error. */
            type = _PyLong_AsInt(PyTuple_GET_ITEM(dict, 1));
            if (type == DICT_TYPE_DIGESTED ||
                type == DICT_TYPE_UNDIGESTED ||
                type == DICT_TYPE_PREFIX)
            {
                assert(type >= 0);
                zd = (ZstdDict*)PyTuple_GET_ITEM(dict, 0);
                goto load;
            }
        }
    }

    /* Wrong type */
    PyErr_SetString(PyExc_TypeError,
                    "zstd_dict argument should be ZstdDict object.");
    return -1;

load:
    if (type == DICT_TYPE_DIGESTED) {
        /* Get ZSTD_CDict */
        ZSTD_CDict *c_dict = _get_CDict(zd, self->compression_level);
        if (c_dict == NULL) {
            return -1;
        }
        /* Reference a prepared dictionary.
           It overrides some compression context's parameters. */
        zstd_ret = ZSTD_CCtx_refCDict(self->cctx, c_dict);
    } else if (type == DICT_TYPE_UNDIGESTED) {
        /* Load a dictionary.
           It doesn't override compression context's parameters. */
        zstd_ret = ZSTD_CCtx_loadDictionary(
                            self->cctx,
                            PyBytes_AS_STRING(zd->dict_content),
                            Py_SIZE(zd->dict_content));
    } else if (type == DICT_TYPE_PREFIX) {
        /* Load a prefix */
        zstd_ret = ZSTD_CCtx_refPrefix(
                            self->cctx,
                            PyBytes_AS_STRING(zd->dict_content),
                            Py_SIZE(zd->dict_content));
    } else {
        /* Impossible code path */
        PyErr_SetString(PyExc_SystemError,
                        "load_c_dict() impossible code path");
        return -1;
    }

    /* Check error */
    if (ZSTD_isError(zstd_ret)) {
        set_zstd_error(MODULE_STATE, ERR_LOAD_C_DICT, zstd_ret);
        return -1;
    }
    return 0;
}

/* Set decompression parameters to decompression context */
static int
set_d_parameters(ZstdDecompressor *self, PyObject *option)
{
    size_t zstd_ret;
    PyObject *key, *value;
    Py_ssize_t pos;
    STATE_FROM_OBJ(self);

    if (!PyDict_Check(option)) {
        PyErr_SetString(PyExc_TypeError,
                        "option argument should be dict object.");
        return -1;
    }

    pos = 0;
    while (PyDict_Next(option, &pos, &key, &value)) {
        /* Check key type */
        if (Py_TYPE(key) == MS_MEMBER(CParameter_type)) {
            PyErr_SetString(PyExc_TypeError,
                            "Key of decompression option dict should "
                            "NOT be CParameter.");
            return -1;
        }

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
        zstd_ret = ZSTD_DCtx_setParameter(self->dctx, key_v, value_v);

        /* Check error */
        if (ZSTD_isError(zstd_ret)) {
            set_parameter_error(MODULE_STATE, 0, key_v, value_v);
            return -1;
        }
    }
    return 0;
}

/* Load dictionary or prefix to decompression context */
static int
load_d_dict(ZstdDecompressor *self, PyObject *dict)
{
    size_t zstd_ret;
    STATE_FROM_OBJ(self);
    ZstdDict *zd;
    int type, ret;

    /* Check ZstdDict */
    ret = PyObject_IsInstance(dict, (PyObject*)MS_MEMBER(ZstdDict_type));
    if (ret < 0) {
        return -1;
    } else if (ret > 0) {
        /* When decompressing, use digested dictionary by default. */
        zd = (ZstdDict*)dict;
        type = DICT_TYPE_DIGESTED;
        goto load;
    }

    /* Check (ZstdDict, type) */
    if (PyTuple_CheckExact(dict) && PyTuple_GET_SIZE(dict) == 2) {
        /* Check ZstdDict */
        ret = PyObject_IsInstance(PyTuple_GET_ITEM(dict, 0),
                                  (PyObject*)MS_MEMBER(ZstdDict_type));
        if (ret < 0) {
            return -1;
        } else if (ret > 0) {
            /* type == -1 may indicate an error. */
            type = _PyLong_AsInt(PyTuple_GET_ITEM(dict, 1));
            if (type == DICT_TYPE_DIGESTED ||
                type == DICT_TYPE_UNDIGESTED ||
                type == DICT_TYPE_PREFIX)
            {
                assert(type >= 0);
                zd = (ZstdDict*)PyTuple_GET_ITEM(dict, 0);
                goto load;
            }
        }
    }

    /* Wrong type */
    PyErr_SetString(PyExc_TypeError,
                    "zstd_dict argument should be ZstdDict object.");
    return -1;

load:
    if (type == DICT_TYPE_DIGESTED) {
        /* Get ZSTD_DDict */
        ZSTD_DDict *d_dict = _get_DDict(zd);
        if (d_dict == NULL) {
            return -1;
        }
        /* Reference a prepared dictionary */
        zstd_ret = ZSTD_DCtx_refDDict(self->dctx, d_dict);
    } else if (type == DICT_TYPE_UNDIGESTED) {
        /* Load a dictionary */
        zstd_ret = ZSTD_DCtx_loadDictionary(
                            self->dctx,
                            PyBytes_AS_STRING(zd->dict_content),
                            Py_SIZE(zd->dict_content));
    } else if (type == DICT_TYPE_PREFIX) {
        /* Load a prefix */
        zstd_ret = ZSTD_DCtx_refPrefix(
                            self->dctx,
                            PyBytes_AS_STRING(zd->dict_content),
                            Py_SIZE(zd->dict_content));
    } else {
        /* Impossible code path */
        PyErr_SetString(PyExc_SystemError,
                        "load_d_dict() impossible code path");
        return -1;
    }

    /* Check error */
    if (ZSTD_isError(zstd_ret)) {
        set_zstd_error(MODULE_STATE, ERR_LOAD_D_DICT, zstd_ret);
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

#endif /* PYZSTD_H_INCLUDED */