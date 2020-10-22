#include "stdint.h"

#include "Python.h"
#include "pythread.h"
#include "structmember.h"      // PyMemberDef

#include "../lib/zstd.h"
#include "../lib/dictBuilder/zdict.h"


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

    /* Thread lock for generating ZSTD_CDict/ZSTD_DDict */
    PyThread_type_lock lock;

    /* __init__ has been called, 0 or 1. */
    char inited;
} ZstdDict;

typedef struct {
    PyObject_HEAD

    /* Compress context */
    ZSTD_CCtx *cctx;

    /* ZstdDict object in use */
    PyObject *dict;

    /* Last mode, initialized to ZSTD_e_end */
    int last_mode;

    /* Thread lock for compressing */
    PyThread_type_lock lock;

    /* Enabled zstd multi-threaded compression, 0 or 1. */
    char zstd_multi_threaded;

    /* __init__ has been called, 0 or 1. */
    char inited;
} ZstdCompressor;

typedef struct {
    PyObject_HEAD

    /* Decompress context */
    ZSTD_DCtx *dctx;

    /* ZstdDict object in use */
    PyObject *dict;

    /* True when the output is at a frame edge, means a frame is completely
       decoded and fully flushed, or the decompressor just be initialized.
       The input stream is not necessarily at a frame edge. 0 or 1. */
    char at_frame_edge;

    /* False if input_buffer has unconsumed data, 0 or 1. */
    char needs_input;

    /* Unconsumed input data */
    uint8_t *input_buffer;
    size_t input_buffer_size;
    size_t in_begin, in_end;

    /* Thread lock for compressing */
    PyThread_type_lock lock;

    /* __init__ has been called, 0 or 1. */
    char inited;
} EndlessZstdDecompressor;

typedef struct {
    PyTypeObject *ZstdDict_type;
    PyTypeObject *ZstdCompressor_type;
    PyTypeObject *RichMemZstdCompressor_type;
    PyTypeObject *EndlessZstdDecompressor_type;
    PyObject *ZstdError;
} _zstd_state;

static _zstd_state static_state;

#define ACQUIRE_LOCK(obj) do {                    \
    if (!PyThread_acquire_lock((obj)->lock, 0)) { \
        Py_BEGIN_ALLOW_THREADS                    \
        PyThread_acquire_lock((obj)->lock, 1);    \
        Py_END_ALLOW_THREADS                      \
    } } while (0)
#define RELEASE_LOCK(obj) PyThread_release_lock((obj)->lock)


/* -----------------------------------
     BlocksOutputBuffer code
   ----------------------------------- */

typedef struct {
    /* List of blocks */
    PyObject *list;
    /* Number of whole allocated size. */
    Py_ssize_t allocated;

    /* Max length of the buffer, negative number for unlimited length. */
    Py_ssize_t max_length;
} BlocksOutputBuffer;


/* Block size sequence. Below functions assume the type is int. */
#define KB (1024)
#define MB (1024*1024)
static const int BUFFER_BLOCK_SIZE[] =
    { 32*KB, 64*KB, 256*KB, 1*MB, 4*MB, 8*MB, 16*MB, 16*MB,
      32*MB, 32*MB, 32*MB, 32*MB, 64*MB, 64*MB, 128*MB, 128*MB,
      256*MB };
#undef KB
#undef MB

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
                         Py_ssize_t max_length)
{
    PyObject *b;
    int block_size;

    /* Set & check max_length */
    buffer->max_length = max_length;
    if (max_length >= 0 && BUFFER_BLOCK_SIZE[0] > max_length) {
        block_size = (int) max_length;
    } else {
        block_size = BUFFER_BLOCK_SIZE[0];
    }

    /* The first block */
    b = PyBytes_FromStringAndSize(NULL, block_size);
    if (b == NULL) {
        buffer->list = NULL; /* For OutputBuffer_OnError() */
        return -1;
    }

    /* Create list */
    buffer->list = PyList_New(1);
    if (buffer->list == NULL) {
        Py_DECREF(b);
        return -1;
    }
    PyList_SET_ITEM(buffer->list, 0, b);

    /* Set variables */
    buffer->allocated = block_size;

    ob->dst = PyBytes_AS_STRING(b);
    ob->size = block_size;
    ob->pos = 0;
    return 0;
}


/* Initialize the buffer, with an initial size. Only used for zstd compression.
   init_size: the initial size.
   Return 0 on success
   Return -1 on failure
*/
static inline int
OutputBuffer_InitWithSize(BlocksOutputBuffer *buffer, ZSTD_outBuffer *ob,
                          Py_ssize_t init_size)
{
    PyObject *b;

    /* The first block */
    b = PyBytes_FromStringAndSize(NULL, init_size);
    if (b == NULL) {
        buffer->list = NULL; /* For OutputBuffer_OnError() */
        PyErr_SetString(PyExc_MemoryError, "Unable to allocate output buffer.");
        return -1;
    }

    /* Create list */
    buffer->list = PyList_New(1);
    if (buffer->list == NULL) {
        Py_DECREF(b);
        return -1;
    }
    PyList_SET_ITEM(buffer->list, 0, b);

    /* Set variables */
    buffer->allocated = init_size;
    buffer->max_length = -1;

    ob->dst = PyBytes_AS_STRING(b);
    ob->size = (size_t) init_size;
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
    int block_size;

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
        /* Prevent adding unlimited number of empty bytes to the list */
        if (buffer->max_length == 0) {
            assert(ob->pos == ob->size);
            return 0;
        }
        /* block_size of the last block */
        if (block_size > buffer->max_length - buffer->allocated) {
            block_size = (int) (buffer->max_length - buffer->allocated);
        }
    }

    /* Create the block */
    b = PyBytes_FromStringAndSize(NULL, block_size);
    if (b == NULL) {
        PyErr_SetString(PyExc_MemoryError, "Unable to allocate output buffer.");
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
    ob->size = block_size;
    ob->pos = 0;
    return 0;
}


/* Return the current outputted data size. */
static inline Py_ssize_t
OutputBuffer_GetDataSize(BlocksOutputBuffer *buffer, ZSTD_outBuffer *ob)
{
    return buffer->allocated - (ob->size - ob->pos);
}


/* Finish the buffer.
   Return a bytes object on success
   Return NULL on failure
*/
static PyObject *
OutputBuffer_Finish(BlocksOutputBuffer *buffer, ZSTD_outBuffer *ob)
{
    PyObject *result, *block;
    int8_t *offset;
    const Py_ssize_t list_len = Py_SIZE(buffer->list);

    /* Fast path for single block */
    if ((ob->pos == 0 && list_len == 2) ||
        (ob->pos == ob->size && list_len == 1)) {
        block = PyList_GET_ITEM(buffer->list, 0);
        Py_INCREF(block);

        Py_DECREF(buffer->list);
        return block;
    }

    /* Final bytes object */
    result = PyBytes_FromStringAndSize(NULL, buffer->allocated - (ob->size - ob->pos));
    if (result == NULL) {
        PyErr_SetString(PyExc_MemoryError, "Unable to allocate output buffer.");
        return NULL;
    }

    /* Memory copy */
    if (list_len > 0) {
        offset = (int8_t*) PyBytes_AS_STRING(result);

        /* Blocks except the last one */
        Py_ssize_t i = 0;
        for (; i < list_len-1; i++) {
            block = PyList_GET_ITEM(buffer->list, i);
            memcpy(offset, PyBytes_AS_STRING(block), Py_SIZE(block));
            offset += Py_SIZE(block);
        }
        /* The last block */
        block = PyList_GET_ITEM(buffer->list, i);
        memcpy(offset, PyBytes_AS_STRING(block), Py_SIZE(block) - (ob->size - ob->pos));
    } else {
        assert(Py_SIZE(result) == 0);
    }

    Py_DECREF(buffer->list);
    return result;
}


/* clean up the buffer. */
static inline void
OutputBuffer_OnError(BlocksOutputBuffer *buffer)
{
    Py_XDECREF(buffer->list);
}


/* -----------------------------------
     Parameters from zstd
   ----------------------------------- */

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
get_parameter_error_msg(char *buf, int buf_size, Py_ssize_t pos,
                        int key_v, int value_v, char is_compress)
{
    ParameterInfo const *list;
    int list_size;
    char const *name;
    char *type;
    ZSTD_bounds bounds;

    assert(buf_size >= 200);

    if (is_compress) {
        list = cp_list;
        list_size = Py_ARRAY_LENGTH(cp_list);
        type = "compress";
    } else {
        list = dp_list;
        list_size = Py_ARRAY_LENGTH(dp_list);
        type = "decompress";
    }

    /* Find parameter's name */
    name = NULL;
    for (int i = 0; i < list_size; i++) {
        if (key_v == (list+i)->parameter) {
            name = (list+i)->parameter_name;
            break;
        }
    }

    /* Not a valid parameter */
    if (name == NULL) {
        PyOS_snprintf(buf, buf_size,
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
        PyOS_snprintf(buf, buf_size,
                      "Error when getting bounds of zstd %s parameter \"%s\".",
                      type, name);
        return;
    }

    /* Error message */
    PyOS_snprintf(buf, buf_size,
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
        if (o == NULL) {                                     \
            return -1;                                       \
        }                                                    \
        if (PyModule_AddObject(module, "_" #macro, o) < 0) { \
            Py_DECREF(o);                                    \
            return -1;                                       \
        }                                                    \
    } while(0)


static int
add_parameters(PyObject *module)
{
    /* If add new parameters, please also add to cp_list/dp_list above. */

    /* Compress parameters */
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

    /* Decompress parameters */
    ADD_INT_PREFIX_MACRO(module, ZSTD_d_windowLogMax);

    return 0;
}


/* -----------------------------------
     ZstdDict code
   ----------------------------------- */
static void
capsule_free_cdict(PyObject *capsule)
{
    ZSTD_CDict *cdict = PyCapsule_GetPointer(capsule, NULL);
    ZSTD_freeCDict(cdict);
}

static ZSTD_CDict *
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
    ACQUIRE_LOCK(self);
    if (self->d_dict == NULL) {
        Py_BEGIN_ALLOW_THREADS
        self->d_dict = ZSTD_createDDict(PyBytes_AS_STRING(self->dict_content),
                                        Py_SIZE(self->dict_content));
        Py_END_ALLOW_THREADS
    }
    RELEASE_LOCK(self);

    return self->d_dict;
}

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
    if (self->d_dict) {
        ZSTD_freeDDict(self->d_dict);
    }

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

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "O|p:ZstdDict.__init__", kwlist,
                                     &dict_content, &is_raw)) {
        return -1;
    }

    /* Only called once */
    if (self->inited) {
        PyErr_SetString(PyExc_RuntimeError, "ZstdDict.__init__ function was called twice.");
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
                        "dictionary content should at least 8 bytes.");
        return -1;
    }

    /* Get dict_id, 0 means "raw content" dictionary. */
    self->dict_id = ZDICT_getDictID(PyBytes_AS_STRING(dict_content),
                                    Py_SIZE(dict_content));

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

PyDoc_STRVAR(ZstdDict_reduce_doc, "Intentionally not supporting pickle.");

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


static PyMethodDef _ZstdDict_methods[] = {
    {"__reduce__", (PyCFunction)ZstdDict_reduce, METH_NOARGS, ZstdDict_reduce_doc},
    {NULL, NULL, 0, NULL}
};

PyDoc_STRVAR(_ZstdDict_dict_doc,
    "Zstd dictionary, used for compress/decompress.");

PyDoc_STRVAR(ZstdDict_dictid_doc,
    "ID of zstd dictionary, a 32-bit unsigned int value.");

PyDoc_STRVAR(ZstdDict_dictcontent_doc,
    "The content of zstd dictionary, a bytes object. Can be used with other programs.");

static PyObject *
ZstdDict_str(ZstdDict *dict)
{
    char buf[64];
    PyOS_snprintf(buf, sizeof(buf),
                  "<ZstdDict dict_id=%u dict_size=%zd>",
                  dict->dict_id, Py_SIZE(dict->dict_content));

    return PyUnicode_FromString(buf);
}

static PyMemberDef _ZstdDict_members[] = {
    {"dict_id", T_UINT, offsetof(ZstdDict, dict_id), READONLY, ZstdDict_dictid_doc},
    {"dict_content", T_OBJECT_EX, offsetof(ZstdDict, dict_content), READONLY, ZstdDict_dictcontent_doc},
    {NULL}
};

static PyType_Slot zstddict_slots[] = {
    {Py_tp_methods, _ZstdDict_methods},
    {Py_tp_members, _ZstdDict_members},
    {Py_tp_new, ZstdDict_new},
    {Py_tp_dealloc, ZstdDict_dealloc},
    {Py_tp_init, ZstdDict_init},
    {Py_tp_str, ZstdDict_str},
    {Py_tp_doc, (char*)_ZstdDict_dict_doc},
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
"_zstd._train_dict\n"
"--\n"
"\n"
"Internal function, train a zstd dictionary.");

static PyObject *
_train_dict(PyObject *module, PyObject *args)
{
    PyBytesObject *dst_data;
    PyObject *dst_data_sizes;
    Py_ssize_t dict_size;

    size_t *chunk_sizes = NULL;
    PyObject *dict_buffer = NULL;
    size_t zstd_ret;

    if (!PyArg_ParseTuple(args, "SOn:_train_dict",
                          &dst_data, &dst_data_sizes, &dict_size)) {
        return NULL;
    }

    /* Check dict_size range */
    if (dict_size <= 0) {
        PyErr_SetString(PyExc_ValueError, "dict_size argument should be positive number.");
        return NULL;
    }

    /* Prepare chunk_sizes */
    if (!PyList_Check(dst_data_sizes)) {
        PyErr_SetString(PyExc_TypeError,
                        "dst_data_sizes argument should be a list.");
        goto error;
    }

    const Py_ssize_t chunks_number = Py_SIZE(dst_data_sizes);
    if (chunks_number > UINT32_MAX) {
        PyErr_SetString(PyExc_ValueError,
                        "The number of samples is too large.");
        goto error;
    }

    chunk_sizes = PyMem_Malloc(chunks_number * sizeof(size_t));
    if (chunk_sizes == NULL) {
        PyErr_NoMemory();
        goto error;
    }

    for (Py_ssize_t i = 0; i < chunks_number; i++) {
        PyObject *size = PyList_GET_ITEM(dst_data_sizes, i);
        chunk_sizes[i] = PyLong_AsSize_t(size);
        if (chunk_sizes[i] == (size_t)-1 && PyErr_Occurred()) {
            PyErr_SetString(PyExc_ValueError,
                            "Items in dst_data_sizes list should be int "
                            "object, with a size_t value.");
            goto error;
        }
    }

    /* Allocate dict buffer */
    dict_buffer = PyBytes_FromStringAndSize(NULL, dict_size);
    if (dict_buffer == NULL) {
        goto error;
    }

    /* Train the dictionary. */
    Py_BEGIN_ALLOW_THREADS
    zstd_ret = ZDICT_trainFromBuffer(PyBytes_AS_STRING(dict_buffer), dict_size,
                                     PyBytes_AS_STRING(dst_data),
                                     chunk_sizes, (uint32_t)chunks_number);
    Py_END_ALLOW_THREADS

    /* Check zstd dict error. */
    if (ZDICT_isError(zstd_ret)) {
        PyErr_SetString(static_state.ZstdError, ZDICT_getErrorName(zstd_ret));
        goto error;
    }

    /* Resize dict_buffer */
    if (_PyBytes_Resize(&dict_buffer, zstd_ret) < 0) {
        goto error;
    }

    PyMem_Free(chunk_sizes);
    return dict_buffer;

error:
    if (chunk_sizes != NULL) {
        PyMem_Free(chunk_sizes);
    }
    Py_XDECREF(dict_buffer);
    return NULL;
}


PyDoc_STRVAR(_finalize_dict_doc,
"_zstd._finalize_dict\n"
"--\n"
"\n"
"Internal function, finalize a zstd dictionary.");

static PyObject *
_finalize_dict(PyObject *module, PyObject *args)
{
#if ZSTD_VERSION_NUMBER < 10405
    PyErr_Format(PyExc_NotImplementedError,
                 "This function only available when the underlying zstd "
                 "library's version is greater than or equal to v1.4.5, "
                 "the current underlying zstd library's version is v%s.",
                 ZSTD_versionString());
    return NULL;
#else
    PyBytesObject *custom_dict;
    PyBytesObject *dst_data;
    PyObject *dst_data_sizes;
    Py_ssize_t dict_size;
    int compression_level;

    size_t *chunk_sizes = NULL;
    PyObject *dict_buffer = NULL;
    size_t zstd_ret;
    ZDICT_params_t params;

    if (!PyArg_ParseTuple(args, "SSOni:_finalize_dict",
                          &custom_dict, &dst_data, &dst_data_sizes,
                          &dict_size, &compression_level)) {
        return NULL;
    }

    /* Check dict_size range */
    if (dict_size <= 0) {
        PyErr_SetString(PyExc_ValueError, "dict_size argument should be positive number.");
        return NULL;
    }

    /* Prepare chunk_sizes */
    if (!PyList_Check(dst_data_sizes)) {
        PyErr_SetString(PyExc_TypeError,
                        "dst_data_sizes argument should be a list.");
        goto error;
    }

    const Py_ssize_t chunks_number = Py_SIZE(dst_data_sizes);
    if (chunks_number > UINT32_MAX) {
        PyErr_SetString(PyExc_ValueError,
                        "The number of samples is too large.");
        goto error;
    }

    chunk_sizes = PyMem_Malloc(chunks_number * sizeof(size_t));
    if (chunk_sizes == NULL) {
        PyErr_NoMemory();
        goto error;
    }

    for (Py_ssize_t i = 0; i < chunks_number; i++) {
        PyObject *size = PyList_GET_ITEM(dst_data_sizes, i);
        chunk_sizes[i] = PyLong_AsSize_t(size);
        if (chunk_sizes[i] == (size_t)-1 && PyErr_Occurred()) {
            PyErr_SetString(PyExc_ValueError,
                            "Items in dst_data_sizes list should be int "
                            "object, with a size_t value.");
            goto error;
        }
    }

    /* Allocate dict buffer */
    dict_buffer = PyBytes_FromStringAndSize(NULL, dict_size);
    if (dict_buffer == NULL) {
        goto error;
    }

    /* Parameters */

    /* Optimize for a specific zstd compression level, 0 means default. */
    params.compressionLevel = compression_level;
    /* Write log to stderr, 0 = none. */
    params.notificationLevel = 0;
    /* Force dictID value, 0 means auto mode (32-bits random value). */
    params.dictID = 0;

    /* Finalize the dictionary. */
    Py_BEGIN_ALLOW_THREADS
    zstd_ret = ZDICT_finalizeDictionary(PyBytes_AS_STRING(dict_buffer), dict_size,
                                        PyBytes_AS_STRING(custom_dict), Py_SIZE(custom_dict),
                                        PyBytes_AS_STRING(dst_data), chunk_sizes,
                                        (uint32_t)chunks_number, params);
    Py_END_ALLOW_THREADS

    /* Check zstd dict error. */
    if (ZDICT_isError(zstd_ret)) {
        PyErr_SetString(static_state.ZstdError, ZDICT_getErrorName(zstd_ret));
        goto error;
    }

    /* Resize dict_buffer */
    if (_PyBytes_Resize(&dict_buffer, zstd_ret) < 0) {
        goto error;
    }

    PyMem_Free(chunk_sizes);
    return dict_buffer;

error:
    if (chunk_sizes != NULL) {
        PyMem_Free(chunk_sizes);
    }
    Py_XDECREF(dict_buffer);
    return NULL;
#endif
}


/* -----------------------------------
     Set parameters / load dictionary
   ----------------------------------- */

static int
check_level_bounds(int compressionLevel, char* buf, int buf_size)
{
    assert(buf_size >= 120);

    if (compressionLevel > ZSTD_maxCLevel() || compressionLevel < ZSTD_minCLevel()) {
        PyOS_snprintf(buf, buf_size,
                      "In zstd v%s, the compression level parameter should %d "
                      "<= value <= %d, provided value is %d, it will be clamped.",
                      ZSTD_versionString(), ZSTD_minCLevel(),
                      ZSTD_maxCLevel(), compressionLevel);

        if (PyErr_WarnEx(PyExc_RuntimeWarning, buf, 1) < 0) {
            return -1;
        }
    }
    return 0;
}

/* Set compressLevel or compress parameters to compress context. */
static int
set_c_parameters(ZstdCompressor *self,
                 PyObject *level_or_option,
                 int *compress_level)
{
    size_t zstd_ret;
    char msg_buf[200];

    /* Integer compression level */
    if (PyLong_Check(level_or_option)) {
        const int level = _PyLong_AsInt(level_or_option);
        if (level == -1 && PyErr_Occurred()) {
            PyErr_SetString(PyExc_ValueError,
                            "Compress level should be 32-bit signed int value.");
            return -1;
        }

        /* Save to *compress_level for generating ZSTD_CDICT */
        *compress_level = level;

        /* Check compressionLevel bounds */
        if (check_level_bounds(level, msg_buf, sizeof(msg_buf)) < 0) {
            return -1;
        }

        /* Set compressionLevel to compress context */
        zstd_ret = ZSTD_CCtx_setParameter(self->cctx,
                                          ZSTD_c_compressionLevel,
                                          level);

        /* Check error */
        if (ZSTD_isError(zstd_ret)) {
            PyErr_Format(static_state.ZstdError,
                         "Error when setting compression level: %s",
                         ZSTD_getErrorName(zstd_ret));
            return -1;
        }
        return 0;
    }

    /* Options dict */
    if (PyDict_Check(level_or_option)) {
        PyObject *key, * value;
        Py_ssize_t pos = 0;

        while (PyDict_Next(level_or_option, &pos, &key, &value)) {
            /* Both key & value should be 32-bit signed int */
            int key_v = _PyLong_AsInt(key);
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
                /* Save to *compress_level for generating ZSTD_CDICT */
                *compress_level = value_v;

                /* Check compressionLevel bounds */
                if (check_level_bounds(value_v, msg_buf, sizeof(msg_buf)) < 0) {
                    return -1;
                }
            } else if (key_v == ZSTD_c_nbWorkers) {
                /* From zstd library doc:
                   1. When nbWorkers >= 1, triggers asynchronous mode when
                      used with ZSTD_compressStream2().
                   2, Default value is `0`, aka "single-threaded mode" : no
                      worker is spawned, compression is performed inside
                      caller's thread, all invocations are blocking*/
                if (value_v > 1) {
                    self->zstd_multi_threaded = 1;
                } else if (value_v == 1) {
                    /* Use single-threaded mode */
                    value_v = 0;
                }
            }

            /* Set parameter to compress context */
            zstd_ret = ZSTD_CCtx_setParameter(self->cctx, key_v, value_v);
            if (ZSTD_isError(zstd_ret)) {
                get_parameter_error_msg(msg_buf, sizeof(msg_buf),
                                        pos, key_v, value_v, 1);
                PyErr_SetString(static_state.ZstdError, msg_buf);
                return -1;
            }
        }
        return 0;
    }

    /* Wrong type */
    PyErr_SetString(PyExc_TypeError, "level_or_option argument wrong type.");
    return -1;
}

/* Load dictionary (ZSTD_CDict instance) to compress context (ZSTD_CCtx instance). */
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
        PyErr_SetString(PyExc_SystemError,
                        "Failed to get ZSTD_CDict from zstd dictionary content.");
        return -1;
    }

    /* Reference a prepared dictionary */
    zstd_ret = ZSTD_CCtx_refCDict(self->cctx, c_dict);

    /* Check error */
    if (ZSTD_isError(zstd_ret)) {
        PyErr_SetString(static_state.ZstdError, ZSTD_getErrorName(zstd_ret));
        return -1;
    }
    return 0;
}


/* Set decompress parameters to decompress context. */
static int
set_d_parameters(EndlessZstdDecompressor *self, PyObject *option)
{
    size_t zstd_ret;
    PyObject *key, *value;
    Py_ssize_t pos;
    char msg_buf[200];

    if (!PyDict_Check(option)) {
        PyErr_SetString(PyExc_TypeError,
                        "option argument should be dict object.");
        return -1;
    }

    pos = 0;
    while (PyDict_Next(option, &pos, &key, &value)) {
        /* Both key & value should be 32-bit signed int */
        int key_v = _PyLong_AsInt(key);
        if (key_v == -1 && PyErr_Occurred()) {
            PyErr_SetString(PyExc_ValueError,
                            "Key of option dict should be 32-bit signed integer value.");
            return -1;
        }

        int value_v = _PyLong_AsInt(value);
        if (value_v == -1 && PyErr_Occurred()) {
            PyErr_SetString(PyExc_ValueError,
                            "Value of option dict should be 32-bit signed integer value.");
            return -1;
        }

        /* Set parameter to compress context */
        zstd_ret = ZSTD_DCtx_setParameter(self->dctx, key_v, value_v);

        /* Check error */
        if (ZSTD_isError(zstd_ret)) {
            get_parameter_error_msg(msg_buf, sizeof(msg_buf),
                                    pos, key_v, value_v, 0);
            PyErr_SetString(static_state.ZstdError, msg_buf);
            return -1;
        }
    }
    return 0;
}

/* Load dictionary (ZSTD_DDict instance) to decompress context (ZSTD_DCtx instance). */
static int
load_d_dict(EndlessZstdDecompressor *self, PyObject *dict)
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
        PyErr_SetString(PyExc_SystemError,
                        "Failed to get ZSTD_DDict from zstd dictionary content.");
        return -1;
    }

    /* Reference a decompress dictionary */
    zstd_ret = ZSTD_DCtx_refDDict(self->dctx, d_dict);

    /* Check error */
    if (ZSTD_isError(zstd_ret)) {
        PyErr_SetString(static_state.ZstdError, ZSTD_getErrorName(zstd_ret));
        return -1;
    }
    return 0;
}


/* -----------------------
     ZstdCompressor code
   ----------------------- */

static PyObject *
_ZstdCompressor_new(PyTypeObject *type, PyObject *args, PyObject *kwds)
{
    ZstdCompressor *self;
    self = (ZstdCompressor*)type->tp_alloc(type, 0);
    if (self == NULL) {
        goto error;
    }

    assert(self->dict == NULL);
    assert(self->zstd_multi_threaded == 0);
    assert(self->inited == 0);

    /* Compress context */
    self->cctx = ZSTD_createCCtx();
    if (self->cctx == NULL) {
        PyErr_SetString(PyExc_RuntimeError, "Unable to create ZSTD_CCtx instance.");
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
_ZstdCompressor_dealloc(ZstdCompressor *self)
{
    /* Compress context */
    if (self->cctx) {
        ZSTD_freeCCtx(self->cctx);
    }

    /* Py_XDECREF the dict after free the compress context */
    Py_XDECREF(self->dict);

    /* Thread lock */
    if (self->lock) {
        PyThread_free_lock(self->lock);
    }

    PyTypeObject *tp = Py_TYPE(self);
    tp->tp_free((PyObject*)self);
    Py_DECREF(tp);
}

PyDoc_STRVAR(ZstdCompressor_doc, "Zstd stream compressor.");

static int
ZstdCompressor_init(ZstdCompressor *self, PyObject *args, PyObject *kwargs)
{
    static char *kwlist[] = {"level_or_option", "zstd_dict", NULL};
    PyObject *level_or_option = Py_None;
    PyObject *zstd_dict = Py_None;

    int compress_level = 0; /* 0 means use zstd's default compression level */

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|OO:ZstdCompressor.__init__", kwlist,
                                     &level_or_option, &zstd_dict)) {
        return -1;
    }

    /* Only called once */
    if (self->inited) {
        PyErr_SetString(PyExc_RuntimeError,
                        "ZstdCompressor.__init__ function was called twice.");
        return -1;
    }
    self->inited = 1;

    /* Set compressLevel/option to compress context */
    if (level_or_option != Py_None) {
        if (set_c_parameters(self, level_or_option, &compress_level) < 0) {
            return -1;
        }
    }

    /* Load dictionary to compress context */
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
    BlocksOutputBuffer buffer;
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
            return NULL;
        }

        /* OutputBuffer(OnError)(&buffer) is after `error` label,
           so initialize the buffer before any `goto error` statement. */
        if (OutputBuffer_InitWithSize(&buffer, &out,
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
            PyErr_SetString(static_state.ZstdError, ZSTD_getErrorName(zstd_ret));
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
    BlocksOutputBuffer buffer;
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
            PyErr_SetString(static_state.ZstdError, ZSTD_getErrorName(zstd_ret));
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
"_zstd.ZstdCompressor.compress\n"
"--\n"
"\n"
"Provide data to the compressor object.\n"
"Returns a chunk of compressed data if possible, or b'' otherwise.");

static PyObject *
ZstdCompressor_compress(ZstdCompressor *self, PyObject *args, PyObject *kwargs)
{
    static char *kwlist[] = {"data", "mode", NULL};
    Py_buffer data;
    int mode = ZSTD_e_continue;

    PyObject *ret;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "y*|i:ZstdCompressor.compress", kwlist,
                                     &data, &mode)) {
        return NULL;
    }

    /* Check mode value */
    if ((uint32_t) mode > ZSTD_e_end) {
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
    if (self->zstd_multi_threaded && mode == ZSTD_e_continue) {
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
"_zstd.ZstdCompressor.flush\n"
"--\n"
"\n"
"Flush any remaining data in internal buffer.\n"
"Since zstd data consists of one or more independent frames, the compressor "
"object can still be used after this method is called.");

static PyObject *
ZstdCompressor_flush(ZstdCompressor *self, PyObject *args, PyObject *kwargs)
{
    static char *kwlist[] = {"mode", NULL};
    int mode = ZSTD_e_end;

    PyObject *ret;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|i:ZstdCompressor.flush", kwlist,
                                     &mode)) {
        return NULL;
    }

    /* Check mode value */
    if (mode != ZSTD_e_end && mode != ZSTD_e_flush) {
        PyErr_SetString(PyExc_ValueError,
                        "mode argument wrong value, it should be "
                        "ZstdCompressor.FLUSH_BLOCK or "
                        "ZstdCompressor.FLUSH_FRAME.");
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


PyDoc_STRVAR(ZstdCompressor_reduce_doc, "Intentionally not supporting pickle.");

static PyObject *
ZstdCompressor_reduce(ZstdCompressor *self)
{
    PyErr_Format(PyExc_TypeError,
                 "Cannot pickle %s object.",
                 Py_TYPE(self)->tp_name);
    return NULL;
}


static PyMethodDef _ZstdCompressor_methods[] = {
    {"compress", (PyCFunction)ZstdCompressor_compress,
     METH_VARARGS|METH_KEYWORDS, ZstdCompressor_compress_doc},
    {"flush", (PyCFunction)ZstdCompressor_flush,
     METH_VARARGS|METH_KEYWORDS, ZstdCompressor_flush_doc},
    {"__reduce__", (PyCFunction)ZstdCompressor_reduce, METH_NOARGS, ZstdCompressor_reduce_doc},
    {NULL, NULL, 0, NULL}
};

PyDoc_STRVAR(ZstdCompressor_last_mode_doc,
"The last mode used to this compressor, its value can be CONTINUE, "
"FLUSH_BLOCK, FLUSH_FRAME. Initialized to FLUSH_FRAME.\n"
"It can be used to get the current state of a compressor, such as, a block "
"ends, a frame ends.");

static PyMemberDef _ZstdCompressor_members[] = {
    {"last_mode", T_INT, offsetof(ZstdCompressor, last_mode),
      READONLY, ZstdCompressor_last_mode_doc},
    {NULL}
};

static PyType_Slot zstdcompressor_slots[] = {
    {Py_tp_new, _ZstdCompressor_new},
    {Py_tp_dealloc, _ZstdCompressor_dealloc},
    {Py_tp_init, ZstdCompressor_init},
    {Py_tp_methods, _ZstdCompressor_methods},
    {Py_tp_members, _ZstdCompressor_members},
    {Py_tp_doc, (char*)ZstdCompressor_doc},
    {0, 0}
};

static PyType_Spec zstdcompressor_type_spec = {
    .name = "_zstd.ZstdCompressor",
    .basicsize = sizeof(ZstdCompressor),
    /* Calling PyType_GetModuleState() on a subclass is not safe.
       zstdcompressor_type_spec does not have Py_TPFLAGS_BASETYPE flag
       which prevents to create a subclass.
       So calling PyType_GetModuleState() in this file is always safe. */
    .flags = Py_TPFLAGS_DEFAULT,
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

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|OO:RichMemZstdCompressor.__init__", kwlist,
                                     &level_or_option, &zstd_dict)) {
        return -1;
    }

    /* Only called once */
    if (self->inited) {
        PyErr_SetString(PyExc_RuntimeError,
                        "ZstdCompressor.__init__ function was called twice.");
        return -1;
    }
    self->inited = 1;

    /* Set compressLevel/option to compress context */
    if (level_or_option != Py_None) {
        if (set_c_parameters(self, level_or_option, &compress_level) < 0) {
            return -1;
        }
    }

    /* Check effective condition */
    if (self->zstd_multi_threaded) {
        char *msg = "Currently \"rich memory mode\" has no effect on "
                    "zstd multi-threaded compression (set "
                    "\"CParameter.nbWorkers\" > 1), it will allocate "
                    "unnecessary memory.";
        if (PyErr_WarnEx(PyExc_ResourceWarning, msg, 1) < 0) {
            return -1;
        }
    }

    /* Load dictionary to compress context */
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
"_zstd.RichMemZstdCompressor.compress\n"
"--\n"
"\n"
"Compress data use rich memory mode, return a single zstd frame.");

static PyObject *
RichMemZstdCompressor_compress(ZstdCompressor *self, PyObject *args, PyObject *kwargs)
{
    static char *kwlist[] = {"data", NULL};
    Py_buffer data;

    PyObject *ret;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "y*:RichMemZstdCompressor.compress", kwlist,
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

static PyMethodDef _RichMem_ZstdCompressor_methods[] = {
    {"compress", (PyCFunction)RichMemZstdCompressor_compress,
     METH_VARARGS|METH_KEYWORDS, RichMemZstdCompressor_compress_doc},
    {"__reduce__", (PyCFunction)ZstdCompressor_reduce, METH_NOARGS, ZstdCompressor_reduce_doc},
    {NULL, NULL, 0, NULL}
};

PyDoc_STRVAR(RichMemZstdCompressor_doc,
"A compressor use rich memory mode. It is designed to allocate more memory, "
"but faster in some cases.");

static PyType_Slot richmem_zstdcompressor_slots[] = {
    {Py_tp_new, _ZstdCompressor_new},
    {Py_tp_dealloc, _ZstdCompressor_dealloc},
    {Py_tp_init, RichMemZstdCompressor_init},
    {Py_tp_methods, _RichMem_ZstdCompressor_methods},
    {Py_tp_doc, (char*)RichMemZstdCompressor_doc},
    {0, 0}
};

static PyType_Spec richmem_zstdcompressor_type_spec = {
    .name = "_zstd.RichMemZstdCompressor",
    .basicsize = sizeof(ZstdCompressor),
    /* Calling PyType_GetModuleState() on a subclass is not safe.
       zstdcompressor_type_spec does not have Py_TPFLAGS_BASETYPE flag
       which prevents to create a subclass.
       So calling PyType_GetModuleState() in this file is always safe. */
    .flags = Py_TPFLAGS_DEFAULT,
    .slots = richmem_zstdcompressor_slots,
};


/* -------------------------
     EndlessZstdDecompressor code
   ------------------------- */

static PyObject *
_EndlessZstdDecompressor_new(PyTypeObject *type, PyObject *args, PyObject *kwds)
{
    EndlessZstdDecompressor *self;
    self = (EndlessZstdDecompressor*)type->tp_alloc(type, 0);
    if (self == NULL) {
        goto error;
    }

    assert(self->dict == NULL);
    assert(self->input_buffer == NULL);
    assert(self->input_buffer_size == 0);
    assert(self->in_begin == 0);
    assert(self->in_end == 0);
    assert(self->inited == 0);

    /* at_frame_edge flag */
    self->at_frame_edge = 1;

    /* needs_input flag */
    self->needs_input = 1;

    /* Decompress context */
    self->dctx = ZSTD_createDCtx();
    if (self->dctx == NULL) {
        PyErr_SetString(PyExc_RuntimeError, "Unable to create ZSTD_DCtx instance.");
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
_EndlessZstdDecompressor_dealloc(EndlessZstdDecompressor *self)
{
    /* Free decompress context */
    if (self->dctx) {
        ZSTD_freeDCtx(self->dctx);
    }

    /* Free Unconsumed input data buffer */
    if (self->input_buffer != NULL) {
        PyMem_Free(self->input_buffer);
    }

    /* Py_XDECREF the dict after free decompress context */
    Py_XDECREF(self->dict);

    /* Free thread lock */
    if (self->lock) {
        PyThread_free_lock(self->lock);
    }

    PyTypeObject *tp = Py_TYPE(self);
    tp->tp_free((PyObject*)self);
    Py_DECREF(tp);
}

PyDoc_STRVAR(EndlessZstdDecompressor_doc, "Zstd stream decompressor.");

static int
EndlessZstdDecompressor_init(EndlessZstdDecompressor *self, PyObject *args, PyObject *kwargs)
{
    static char *kwlist[] = {"zstd_dict", "option", NULL};
    PyObject *zstd_dict = Py_None;
    PyObject *option = Py_None;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|OO:EndlessZstdDecompressor.__init__", kwlist,
                                     &zstd_dict, &option)) {
        return -1;
    }

    /* Only called once */
    if (self->inited) {
        PyErr_SetString(PyExc_RuntimeError,
                        "EndlessZstdDecompressor.__init__ function was called twice.");
        return -1;
    }
    self->inited = 1;

    /* Load dictionary to decompress context */
    if (zstd_dict != Py_None) {
        if (load_d_dict(self, zstd_dict) < 0) {
            return -1;
        }

        /* Py_INCREF the dict */
        Py_INCREF(zstd_dict);
        self->dict = zstd_dict;
    }

    /* Set option to decompress context */
    if (option != Py_None) {
        if (set_d_parameters(self, option) < 0) {
            return -1;
        }
    }

    return 0;
}

static inline PyObject *
decompress_stream_impl(EndlessZstdDecompressor *self, ZSTD_inBuffer *in,
                       Py_ssize_t max_length)
{
    size_t zstd_ret;
    ZSTD_outBuffer out;
    BlocksOutputBuffer buffer;
    PyObject *ret;

    /* OutputBuffer(OnError)(&buffer) is after `error` label,
       so initialize the buffer before any `goto error` statement. */
    if (OutputBuffer_InitAndGrow(&buffer, &out, max_length) < 0) {
        goto error;
    }
    assert(out.pos == 0);

    while (1) {
        Py_BEGIN_ALLOW_THREADS
        zstd_ret = ZSTD_decompressStream(self->dctx, &out, in);
        Py_END_ALLOW_THREADS

        /* Check error */
        if (ZSTD_isError(zstd_ret)) {
            PyErr_SetString(static_state.ZstdError, ZSTD_getErrorName(zstd_ret));
            goto error;
        }

        /* Set at_frame_edge flag.
           Since EndlessZstdDecompressor may hold some input data in input_buffer,
           at_frame_edge flag can only indicate that if the output is at a
           frame edge, it's not possible to indicate that if both the input and
           output streams are at a frame edge. */
        if (out.pos == 0 && zstd_ret != 0) {
            /* Skip when the last round has no output.
               Check these because after decoding a frame, decompress an empty
               input will cause zstd_ret becomes non-zero. */
        } else {
            self->at_frame_edge = (zstd_ret == 0) ? 1 : 0;
        }

        if (out.pos == out.size) {
            /* Output buffer exhausted.
               Need to check `out` before `in`. Maybe zstd's internal buffer still
               have a few bytes can be output, grow the output buffer and continue
               the loop if max_lengh < 0. */

            /* Output buffer reached max_length */
            if (OutputBuffer_GetDataSize(&buffer, &out) == max_length) {
                ret = OutputBuffer_Finish(&buffer, &out);
                if (ret != NULL) {
                    goto success;
                } else {
                    goto error;
                }
            }

            /* Grow output buffer */
            if (OutputBuffer_Grow(&buffer, &out) < 0) {
                goto error;
            }
            assert(out.pos == 0);

        } else if (in->pos == in->size) {
            /* Finished */
            ret = OutputBuffer_Finish(&buffer, &out);
            if (ret != NULL) {
                goto success;
            } else {
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

static inline PyObject *
decompress_func_impl(EndlessZstdDecompressor *self, ZSTD_inBuffer *in,
                     Py_ssize_t decompressed_size)
{
    size_t zstd_ret;
    ZSTD_outBuffer out;
    BlocksOutputBuffer buffer;
    PyObject *ret;

    /* OutputBuffer(OnError)(&buffer) is after `error` label,
       so initialize the buffer before any `goto error` statement. */
    if (decompressed_size > 0) {
        if (OutputBuffer_InitWithSize(&buffer, &out, decompressed_size) < 0) {
            goto error;
        }
    } else {
        if (OutputBuffer_InitAndGrow(&buffer, &out, -1) < 0) {
            goto error;
        }
    }
    assert(out.pos == 0);

    while (1) {
        const size_t in_pos_bak = in->pos;

        Py_BEGIN_ALLOW_THREADS
        zstd_ret = ZSTD_decompressStream(self->dctx, &out, in);
        Py_END_ALLOW_THREADS

        /* Check error */
        if (ZSTD_isError(zstd_ret)) {
            PyErr_SetString(static_state.ZstdError, ZSTD_getErrorName(zstd_ret));
            goto error;
        }

        /* Set at_frame_edge flag.
           In this function, set to 1 when both the input and output data are
           at a frame edge. */
        if (out.pos == 0 && in->pos == in_pos_bak) {
            /* Skip when nothing advanced */
        } else {
            self->at_frame_edge = (zstd_ret == 0) ? 1 : 0;
        }

        if (out.pos == out.size) {
            /* Output buffer exhausted.
               Need to check `out` before `in`. Maybe zstd's internal buffer still
               have a few bytes can be output, grow the output buffer and continue
               the loop if max_lengh < 0. */
            if (OutputBuffer_Grow(&buffer, &out) < 0) {
                goto error;
            }
            assert(out.pos == 0);

        } else if (in->pos == in->size) {
            /* Finished */
            ret = OutputBuffer_Finish(&buffer, &out);
            if (ret != NULL) {
                goto success;
            } else {
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

PyDoc_STRVAR(EndlessZstdDecompressor_decompress_doc,
"_zstd.EndlessZstdDecompressor.decompress\n"
"--\n"
"\n"
"Decompress *data*, returning uncompressed data as bytes.\n"
"If all of the input data was decompressed and returned (either because this "
"was less than *max_length* bytes, or because *max_length* was negative), "
"*self.needs_input* will be set to True.");

static PyObject *
EndlessZstdDecompressor_decompress(EndlessZstdDecompressor *self, PyObject *args, PyObject *kwargs)
{
    static char *kwlist[] = {"data", "max_length", NULL};
    Py_buffer data;
    Py_ssize_t max_length = -1;

    ZSTD_inBuffer in;
    PyObject *ret = NULL;
    char at_frame_edge_backup;
    char use_input_buffer;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "y*|n:EndlessZstdDecompressor.decompress", kwlist,
                                     &data, &max_length)) {
        return NULL;
    }

    ACQUIRE_LOCK(self);

    /* Backup at_frame_edge flag before any goto error statement */
    at_frame_edge_backup = self->at_frame_edge;

    /* Prepare input buffer w/wo unconsumed data */
    if (self->in_begin == self->in_end) {
        /* No unconsumed data */
        use_input_buffer = 0;

        in.src = data.buf;
        in.size = data.len;
        in.pos = 0;
    } else if (data.len == 0) {
        /* Has unconsumed data, fast path for b'' */
        use_input_buffer = 1;

        in.src = self->input_buffer + self->in_begin;
        in.size = self->in_end - self->in_begin;
        in.pos = 0;
    } else {
        /* Has unconsumed data */
        use_input_buffer = 1;

        /* Unconsumed data size in input_buffer */
        const Py_ssize_t used_now = self->in_end - self->in_begin;
        /* Number of bytes we can append to input buffer */
        const Py_ssize_t avail_now = self->input_buffer_size - self->in_end;
        /* Number of bytes we can append if we move existing contents to
           beginning of buffer */
        const Py_ssize_t avail_total = self->input_buffer_size - used_now;

        if (avail_total < data.len) {
            uint8_t *tmp;
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
        } else if (avail_now < data.len) {
            /* Move unconsumed data to the beginning */
            memmove(self->input_buffer,
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
    ret = decompress_stream_impl(self, &in, max_length);
    if (ret == NULL) {
        goto error;
    }

    if (in.pos == in.size) {
        /* Input buffer exhausted */

        if (Py_SIZE(ret) == max_length) {
            /* Both input and output buffer exhausted, try to output
               internal buffer's data next time. */
            self->needs_input = 0;
        } else {
            self->needs_input = 1;
        }

        /* Clear input_buffer */
        self->in_begin = 0;
        self->in_end = 0;
    } else {
        /* Has unconsumed data */
        assert(in.pos < in.size);
        const size_t data_size = in.size - in.pos;

        self->needs_input = 0;

        if (!use_input_buffer) {
            /* Discard buffer if it's too small
               (resizing it may needlessly copy the current contents) */
            if (self->input_buffer != NULL &&
                self->input_buffer_size < data_size) {
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
    /* Restore at_frame_edge flag */
    self->at_frame_edge = at_frame_edge_backup;

    /* Reset needs_input */
    self->needs_input = 1;

    /* Discard input buffer */
    self->in_begin = 0;
    self->in_end = 0;

    /* Resetting dctx's session never fail */
    ZSTD_DCtx_reset(self->dctx, ZSTD_reset_session_only);

    Py_CLEAR(ret);
success:
    RELEASE_LOCK(self);

    PyBuffer_Release(&data);
    return ret;
}


PyDoc_STRVAR(EndlessZstdDecompressor_reduce_doc, "Intentionally not supporting pickle.");

static PyObject *
EndlessZstdDecompressor_reduce(EndlessZstdDecompressor *self)
{
    PyErr_Format(PyExc_TypeError,
                 "Cannot pickle %s object.",
                 Py_TYPE(self)->tp_name);
    return NULL;
}

static PyMethodDef _EndlessZstdDecompressor_methods[] = {
    {"decompress", (PyCFunction)EndlessZstdDecompressor_decompress,
     METH_VARARGS|METH_KEYWORDS, EndlessZstdDecompressor_decompress_doc},
    {"__reduce__", (PyCFunction)EndlessZstdDecompressor_reduce, METH_NOARGS, EndlessZstdDecompressor_reduce_doc},
    {NULL, NULL, 0, NULL}
};

PyDoc_STRVAR(EndlessZstdDecompressor_needs_input_doc,
"If max_length argument is nonnegative, and decompressor has (or may has) "
"unconsumed input data, needs_input will be set to False. In this case, pass "
"empty bytes b'' to decompress() method can output unconsumed data.");

PyDoc_STRVAR(EndlessZstdDecompressor_at_frame_edge_doc,
"True when the output is at a frame edge, means a frame is completely decoded "
"and fully flushed, or the decompressor just be initialized.\n"
"Since zstd data doesn't have an end marker, it could be used to check data "
"integrity.\n"
"Note that the input stream is not necessarily at a frame edge.");

static PyMemberDef _EndlessZstdDecompressor_members[] = {
    {"needs_input", T_BOOL, offsetof(EndlessZstdDecompressor, needs_input),
      READONLY, EndlessZstdDecompressor_needs_input_doc},
    {"at_frame_edge", T_BOOL, offsetof(EndlessZstdDecompressor, at_frame_edge),
      READONLY, EndlessZstdDecompressor_at_frame_edge_doc},
    {NULL}
};

static PyType_Slot EndlessZstdDecompressor_slots[] = {
    {Py_tp_new, _EndlessZstdDecompressor_new},
    {Py_tp_dealloc, _EndlessZstdDecompressor_dealloc},
    {Py_tp_init, EndlessZstdDecompressor_init},
    {Py_tp_methods, _EndlessZstdDecompressor_methods},
    {Py_tp_members, _EndlessZstdDecompressor_members},
    {Py_tp_doc, (char*)EndlessZstdDecompressor_doc},
    {0, 0}
};

static PyType_Spec EndlessZstdDecompressor_type_spec = {
    .name = "_zstd.EndlessZstdDecompressor",
    .basicsize = sizeof(EndlessZstdDecompressor),
    /* Calling PyType_GetModuleState() on a subclass is not safe.
       EndlessZstdDecompressor_type_spec does not have Py_TPFLAGS_BASETYPE flag
       which prevents to create a subclass.
       So calling PyType_GetModuleState() in this file is always safe. */
    .flags = Py_TPFLAGS_DEFAULT,
    .slots = EndlessZstdDecompressor_slots,
};

PyDoc_STRVAR(decompress_doc,
"decompress(data, zstd_dict=None, option=None)\n"
"--\n"
"\n"
"Decompress a block of data.\n"
"zstd_dict: A ZstdDict object, pre-trained zstd dictionary.\n"
"option: A dict object, contains advanced decompress parameters.\n\n"
"For incremental decompression, use EndlessZstdDecompressor instead.");

static PyObject *
decompress(PyObject *module, PyObject *args, PyObject *kwargs)
{
    static char *kwlist[] = {"data", "zstd_dict", "option", NULL};
    Py_buffer data;
    PyObject *zstd_dict = Py_None;
    PyObject *option = Py_None;

    unsigned long long decompressed_size;
    PyObject *ret = NULL;
    ZSTD_inBuffer in;
    EndlessZstdDecompressor self;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "y*|OO:decompress", kwlist,
                                     &data, &zstd_dict, &option)) {
        return NULL;
    }

    /* Initialize self */
    memset(&self, 0, sizeof(self));
    self.at_frame_edge = 1;

    self.dctx = ZSTD_createDCtx();
    if (self.dctx == NULL) {
        PyErr_SetString(PyExc_RuntimeError, "Unable to create ZSTD_DCtx instance.");
        goto error;
    }

    /* Load dictionary to decompress context */
    if (zstd_dict != Py_None) {
        if (load_d_dict(&self, zstd_dict) < 0) {
            goto error;
        }

        /* Py_INCREF the dict */
        Py_INCREF(zstd_dict);
        self.dict = zstd_dict;
    }

    /* Set option to decompress context */
    if (option != Py_None) {
        if (set_d_parameters(&self, option) < 0) {
            goto error;
        }
    }

    /* Fast path for empty input */
    if (data.len == 0) {
        ret = PyBytes_FromStringAndSize(NULL, 0);
        if (ret) {
            goto success;
        } else {
            goto error;
        }
    }

    /* Prepare input data */
    in.src = data.buf;
    in.pos = 0;
    in.size = data.len;

    /* Get decompressed size */
    decompressed_size = ZSTD_getDecompressedSize(data.buf, data.len);
    if (decompressed_size > PY_SSIZE_T_MAX) {
        decompressed_size = 0;
    }

    /* Decompress */
    ret = decompress_func_impl(&self, &in, (Py_ssize_t) decompressed_size);
    if (ret == NULL) {
        goto error;
    }

    /* Check data integrity.
       In this function, at_frame_edge flag is 1 when both input and output
       streams are at a frame edge. */
    if (self.at_frame_edge == 0) {
        PyErr_SetString(static_state.ZstdError, "Zstd data ends in an incomplete frame.");
        goto error;
    }

    goto success;

error:
    Py_CLEAR(ret);
success:
    /* Release data */
    PyBuffer_Release(&data);

    /* Clean up EndlessZstdDecompressor, keep this order. */
    if (self.dctx) {
        ZSTD_freeDCtx(self.dctx);
    }
    Py_XDECREF(self.dict);

    return ret;
}


PyDoc_STRVAR(_get_cparam_bounds_doc,
"_zstd._get_cparam_bounds\n"
"--\n"
"\n"
"Internal funciton, get CParameter bounds.");

static PyObject *
_get_cparam_bounds(PyObject *module, PyObject *args)
{
    int cParam;

    PyObject *ret;
    PyObject *temp;

    if (!PyArg_ParseTuple(args, "i:_get_cparam_bounds", &cParam)) {
        return NULL;
    }

    ZSTD_bounds const bound = ZSTD_cParam_getBounds(cParam);
    if (ZSTD_isError(bound.error)) {
        PyErr_SetString(static_state.ZstdError, ZSTD_getErrorName(bound.error));
        return NULL;
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

PyDoc_STRVAR(_get_dparam_bounds_doc,
"_zstd._get_dparam_bounds\n"
"--\n"
"\n"
"Internal funciton, get DParameter bounds.");

static PyObject *
_get_dparam_bounds(PyObject *module, PyObject *args)
{
    int dParam;

    PyObject *ret;
    PyObject *temp;

    if (!PyArg_ParseTuple(args, "i:_get_dparam_bounds", &dParam)) {
        return NULL;
    }

    ZSTD_bounds const bound = ZSTD_dParam_getBounds(dParam);
    if (ZSTD_isError(bound.error)) {
        PyErr_SetString(static_state.ZstdError, ZSTD_getErrorName(bound.error));
        return NULL;
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
"_zstd.get_frame_size\n"
"--\n"
"\n"
"Get the size of a zstd frame, including frame header and epilogue.\n"
"It will iterate all blocks' header within a frame, to accumulate the frame's "
"size.");

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
        PyErr_SetString(static_state.ZstdError, ZSTD_getErrorName(frame_size));
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
"_zstd._get_frame_info\n"
"--\n"
"\n"
"Internal function, get zstd frame infomation from a frame header.\n"
"Return a two-items tuple: (decompressed_size, dictionary_id). If decompressed "
"size is unknown (generated by stream compression), it will be None. If no "
"dictionary, dictionary_id will be 0.\n"
"It's possible to add more items to the tuple in the future.");

static PyObject *
_get_frame_info(PyObject *module, PyObject *args)
{
    Py_buffer frame_buffer;

    unsigned long long content_size;
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
                        "Error when getting a frame's decompressed size, make "
                        "sure that frame_buffer argument starts from the "
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

static PyMethodDef _zstd_methods[] = {
    {"decompress", (PyCFunction)decompress, METH_VARARGS|METH_KEYWORDS, decompress_doc},
    {"_train_dict", (PyCFunction)_train_dict, METH_VARARGS, _train_dict_doc},
    {"_finalize_dict", (PyCFunction)_finalize_dict, METH_VARARGS, _finalize_dict_doc},
    {"_get_cparam_bounds", (PyCFunction)_get_cparam_bounds, METH_VARARGS, _get_cparam_bounds_doc},
    {"_get_dparam_bounds", (PyCFunction)_get_dparam_bounds, METH_VARARGS, _get_dparam_bounds_doc},
    {"get_frame_size", (PyCFunction)get_frame_size, METH_VARARGS, get_frame_size_doc},
    {"_get_frame_info", (PyCFunction)_get_frame_info, METH_VARARGS, _get_frame_info_doc},
    {NULL}
};

static int
_zstd_traverse(PyObject *module, visitproc visit, void *arg)
{
    Py_VISIT(static_state.ZstdError);
    Py_VISIT(static_state.ZstdDict_type);
    Py_VISIT(static_state.ZstdCompressor_type);
    Py_VISIT(static_state.RichMemZstdCompressor_type);
    Py_VISIT(static_state.EndlessZstdDecompressor_type);
    return 0;
}

static int
_zstd_clear(PyObject *module)
{
    Py_CLEAR(static_state.ZstdError);
    Py_CLEAR(static_state.ZstdDict_type);
    Py_CLEAR(static_state.ZstdCompressor_type);
    Py_CLEAR(static_state.RichMemZstdCompressor_type);
    Py_CLEAR(static_state.EndlessZstdDecompressor_type);
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

    /* compressionLevel values */
    temp = PyLong_FromLong(ZSTD_CLEVEL_DEFAULT);
    if (temp == NULL) {
        goto error;
    }
    if (PyModule_AddObject(module, "_ZSTD_CLEVEL_DEFAULT", temp) < 0) {
        Py_DECREF(temp);
        goto error;
    }

    temp = PyLong_FromLong(ZSTD_minCLevel());
    if (temp == NULL) {
        goto error;
    }
    if (PyModule_AddObject(module, "_ZSTD_minCLevel", temp) < 0) {
        Py_DECREF(temp);
        goto error;
    }

    temp = PyLong_FromLong(ZSTD_maxCLevel());
    if (temp == NULL) {
        goto error;
    }
    if (PyModule_AddObject(module, "_ZSTD_maxCLevel", temp) < 0) {
        Py_DECREF(temp);
        goto error;
    }

    return 0;
error:
    return -1;
}

PyMODINIT_FUNC
PyInit__zstd(void)
{
    PyObject *module;
    PyObject *temp;

    module = PyModule_Create(&_zstdmodule);
    if (!module) {
        goto error;
    }

    /* Constants */
    if (add_constants(module) < 0) {
        goto error;
    }

    /* ZstdError */
    static_state.ZstdError = PyErr_NewExceptionWithDoc("_zstd.ZstdError", "Call to zstd failed.", NULL, NULL);
    if (static_state.ZstdError == NULL) {
        goto error;
    }

    Py_INCREF(static_state.ZstdError);
    if (PyModule_AddObject(module, "ZstdError", static_state.ZstdError) < 0) {
        Py_DECREF(static_state.ZstdError);
        goto error;
    }

    /* ZstdDict */
    temp = PyType_FromSpec(&zstddict_type_spec);
    if (temp == NULL) {
        goto error;
    }

    if (PyModule_AddObject(module, "ZstdDict", temp) < 0) {
        Py_DECREF(temp);
        goto error;
    }
    Py_INCREF(temp);
    static_state.ZstdDict_type = (PyTypeObject*) temp;

    /* ZstdCompressor */
    temp = PyType_FromSpec(&zstdcompressor_type_spec);
    if (temp == NULL) {
        goto error;
    }

    if (PyModule_AddObject(module, "ZstdCompressor", temp) < 0) {
        Py_DECREF(temp);
        goto error;
    }
    Py_INCREF(temp);
    static_state.ZstdCompressor_type = (PyTypeObject*) temp;

    /* Add EndDirective enum to ZstdCompressor */
    temp = PyLong_FromLong(ZSTD_e_continue);
    if (PyObject_SetAttrString((PyObject*)static_state.ZstdCompressor_type,
                               "CONTINUE", temp) < 0) {
        Py_DECREF(temp);
        goto error;
    }
    Py_DECREF(temp);

    temp = PyLong_FromLong(ZSTD_e_flush);
    if (PyObject_SetAttrString((PyObject*)static_state.ZstdCompressor_type,
                               "FLUSH_BLOCK", temp) < 0) {
        Py_DECREF(temp);
        goto error;
    }
    Py_DECREF(temp);

    temp = PyLong_FromLong(ZSTD_e_end);
    if (PyObject_SetAttrString((PyObject*)static_state.ZstdCompressor_type,
                               "FLUSH_FRAME", temp) < 0) {
        Py_DECREF(temp);
        goto error;
    }
    Py_DECREF(temp);

    /* RichMemZstdCompressor */
    temp = PyType_FromSpec(&richmem_zstdcompressor_type_spec);
    if (temp == NULL) {
        goto error;
    }

    if (PyModule_AddObject(module, "RichMemZstdCompressor", temp) < 0) {
        Py_DECREF(temp);
        goto error;
    }
    Py_INCREF(temp);
    static_state.RichMemZstdCompressor_type = (PyTypeObject*) temp;

    /* EndlessZstdDecompressor */
    temp = PyType_FromSpec(&EndlessZstdDecompressor_type_spec);
    if (temp == NULL) {
        goto error;
    }

    if (PyModule_AddObject(module, "EndlessZstdDecompressor", temp) < 0) {
        Py_DECREF(temp);
        goto error;
    }
    Py_INCREF(temp);
    static_state.EndlessZstdDecompressor_type = (PyTypeObject*) temp;

    /* zstd_version, ZSTD_versionString() requires zstd v1.3.0+ */
    if (!(temp = PyUnicode_FromString(ZSTD_versionString()))) {
        goto error;
    }
    if (PyModule_AddObject(module, "zstd_version", temp) < 0) {
        Py_DECREF(temp);
        goto error;
    }

    /* zstd_version_info */
    if (!(temp = PyTuple_New(3))) {
        goto error;
    }
    PyTuple_SET_ITEM(temp, 0, PyLong_FromLong(ZSTD_VERSION_MAJOR));
    PyTuple_SET_ITEM(temp, 1, PyLong_FromLong(ZSTD_VERSION_MINOR));
    PyTuple_SET_ITEM(temp, 2, PyLong_FromLong(ZSTD_VERSION_RELEASE));
    if (PyModule_AddObject(module, "zstd_version_info", temp) < 0) {
        Py_DECREF(temp);
        goto error;
    }

    return module;

error:
    _zstd_clear(NULL);
    Py_XDECREF(module);

    if(!PyErr_Occurred()) {
        PyErr_SetString(PyExc_SystemError, "Failed to initialize pyzstd module.");
    }
    return NULL;
}
