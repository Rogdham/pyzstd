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

#include "output_buffer.h"

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
    PyObject *str_read;
    PyObject *str_readinto;
    PyObject *str_write;
    PyObject *str_flush;

    PyTypeObject *ZstdDict_type;
    PyTypeObject *ZstdCompressor_type;
    PyTypeObject *RichMemZstdCompressor_type;
    PyTypeObject *ZstdDecompressor_type;
    PyTypeObject *EndlessZstdDecompressor_type;
    PyTypeObject *ZstdFileReader_type;
    PyTypeObject *ZstdFileWriter_type;
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

/* Force inlining. Same as zstd library. */
#if defined(__GNUC__) || defined(__ICCARM__)
#  define FORCE_INLINE static inline __attribute__((always_inline))
#elif defined(_MSC_VER)
#  define FORCE_INLINE static inline __forceinline
#else
#  define FORCE_INLINE static inline
#endif

/* Force no inlining. Same as zstd library. */
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

FORCE_INLINE PyObject *
invoke_method_no_arg(PyObject *obj, PyObject *meth)
{
#if PY_VERSION_HEX < 0x030900B1
    return PyObject_CallMethodObjArgs(obj, meth, NULL);
#else
    return PyObject_CallMethodNoArgs(obj, meth);
#endif
}

FORCE_INLINE PyObject *
invoke_method_one_arg(PyObject *obj, PyObject *meth, PyObject *arg)
{
#if PY_VERSION_HEX < 0x030900B1
    return PyObject_CallMethodObjArgs(obj, meth, arg, NULL);
#else
    return PyObject_CallMethodOneArg(obj, meth, arg);
#endif
}

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

/* Generate 4 functions using macro:
    1, set_c_parameters(ZstdCompressor *self, PyObject *level_or_option)
    2, load_c_dict(ZstdCompressor *self, PyObject *dict)
    3, set_d_parameters(ZstdDecompressor *self, PyObject *option)
    4, load_d_dict(ZstdDecompressor *self, PyObject *dict) */
#undef  PYZSTD_C_CLASS
#define PYZSTD_C_CLASS       ZstdCompressor
#undef  PYZSTD_D_CLASS
#define PYZSTD_D_CLASS       ZstdDecompressor
#undef  PYZSTD_FUN_PREFIX
#define PYZSTD_FUN_PREFIX(F) F
#include "macro_functions.h"

/* In multi-thread compression + .CONTINUE mode: If input buffer exhausted,
   there may be a lot of data in internal buffer that can be outputted.
   This conditional expression output as much as possible. */
FORCE_INLINE int
mt_continue_should_break(ZSTD_inBuffer *in, ZSTD_outBuffer *out) {
    return in->size == in->pos && out->size != out->pos;
}

/* Get Py_ssize_t value from the returned object of .readinto()/.write()
   methods, and Py_DECREF() the object.
   If fp_ret is NULL, or not an integer, or (v < lower || v > upper), set
   an error and return -1. */
FORCE_INLINE Py_ssize_t
check_and_get_fp_ret(char *func_name, PyObject *fp_ret,
                     Py_ssize_t lower, Py_ssize_t upper)
{
    Py_ssize_t ret_value;

    /* .readinto()/.write() return value should >= 0.
       This function returns -1 for failure. */
    assert(lower >= 0);

    /* .readinto()/.write() failed */
    if (fp_ret == NULL) {
        return -1;
    }

    /* Get Py_ssize_t value */
    ret_value = PyLong_AsSsize_t(fp_ret);
    Py_DECREF(fp_ret);

    /* Check bounds */
    assert(lower >= 0);
    if (ret_value < lower || ret_value > upper) {
        /* Check PyLong_AsSsize_t() failed */
        if (ret_value == -1 && PyErr_Occurred()) {
            PyErr_Format(PyExc_TypeError,
                         "%s return value should be int type",
                         func_name);
            return -1;
        }

        PyErr_Format(PyExc_ValueError,
                     "%s returned invalid length %zd "
                     "(should be %zd <= value <= %zd)",
                     func_name, ret_value,
                     lower, upper);
        return -1;
    }
    return ret_value;
}

/* Write output data to fp.
   If (out->pos == 0), do nothing. */
FORCE_INLINE int
write_to_fp(const _zstd_state* const state,
            char *func_name,
            PyObject *fp, ZSTD_outBuffer *out)
{
    PyObject *mv;
    PyObject *write_ret;

    /* Data length is 0 */
    if (out->pos == 0) {
        return 0;
    }

    /* memoryview object */
    mv = PyMemoryView_FromMemory((char*)out->dst, out->pos, PyBUF_READ);
    if (mv == NULL) {
        goto error;
    }

    /* Write */
    write_ret = invoke_method_one_arg(fp, state->str_write, mv);
    Py_DECREF(mv);

    /* Check .write() return value */
    if (check_and_get_fp_ret(func_name, write_ret,
                             out->pos, out->pos) < 0) {
        goto error;
    }

    return 0;
error:
    return -1;
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
