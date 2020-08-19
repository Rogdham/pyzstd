
#include "Python.h"
#include "structmember.h"         // PyMemberDef

#include "..\lib\zstd.h"
#include "..\lib\dictBuilder\zdict.h"


typedef struct {
    PyObject_HEAD

    /* Content of the dictionary, bytes object. */
    PyObject *dict_content;
    /* Dictionary id */
    UINT32 dict_id;

    /* Reuseable compress/decompress dictionary, they are created once and
       can be shared by multiple threads concurrently, since its usage is
       read-only.

       c_dicts is a dict, int(compressionLevel):PyCapsule(ZSTD_CDict*) */
    PyObject *c_dicts;
    ZSTD_DDict *d_dict;

    /* Thread lock for generating ZSTD_CDict/ZSTD_DDict */
    PyThread_type_lock lock;

    /* __init__ has been called */
    int inited;
} ZstdDict;

typedef struct {
    PyObject_HEAD

    /* Compress context */
    ZSTD_CCtx *cctx;

    /* ZstdDict object in use */
    PyObject *dict;

    /* Last end directive, initialized as ZSTD_e_end */
    int last_end_directive;

    /* Thread lock for compressing */
    PyThread_type_lock lock;

    /* __init__ has been called */
    int inited;
} ZstdCompressor;

typedef struct {
    PyObject_HEAD

    /* Decompress context */
    ZSTD_DCtx *dctx;

    /* ZstdDict object in use */
    PyObject *dict;

    /* True when the output is at a frame edge, means a frame is completely
       decoded and fully flushed, or the decompressor just be initialized.
       Note that the input stream is not necessarily at a frame edge. */
    char at_frame_edge;

    /* False if input_buffer has unconsumed data */
    char needs_input;

    /* Unconsumed input data */
    uint8_t *input_buffer;
    size_t input_buffer_size;
    size_t in_begin, in_end;

    /* Thread lock for compressing */
    PyThread_type_lock lock;

    /* __init__ has been called */
    int inited;
} ZstdDecompressor;

typedef struct {
    PyTypeObject *ZstdDict_type;
    PyTypeObject *ZstdCompressor_type;
    PyTypeObject *ZstdDecompressor_type;
    PyObject *ZstdError;
} _zstd_state;

/*[clinic input]
module _zstd
class _zstd.ZstdDict "ZstdDict *" "&ZstdDict_Type"
class _zstd.ZstdCompressor "ZstdCompressor *" "&ZstdCompressor_type"
class _zstd.ZstdDecompressor "ZstdDecompressor *" "&ZstdDecompressor_type"
[clinic start generated code]*/
/*[clinic end generated code: output=da39a3ee5e6b4b0d input=7208e8cc544a5228]*/

#include "pypi1.h"
#include "clinic\_zstdmodule.c.h"

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


/* Block size sequence. Some compressor/decompressor can't process large
   buffer (>4GB), so the type is int. Below functions assume the type is int.
*/
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
static int
OutputBuffer_InitAndGrow(BlocksOutputBuffer *buffer, Py_ssize_t max_length,
                                ZSTD_outBuffer *ob)
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
        buffer->list = NULL; // For _BlocksOutputBuffer_OnError()
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


/* Grow the buffer. The avail_out must be 0, please check it before calling.
   Return 0 on success
   Return -1 on failure
*/
static int
OutputBuffer_Grow(BlocksOutputBuffer *buffer, ZSTD_outBuffer *ob)
{
    PyObject *b;
    const Py_ssize_t list_len = Py_SIZE(buffer->list);
    int block_size;

    /* Ensure no gaps in the data */
    assert(ob->pos == ob->size);

    /* Get block size */
    if (list_len < Py_ARRAY_LENGTH(BUFFER_BLOCK_SIZE)) {
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

    /* Final bytes object */
    result = PyBytes_FromStringAndSize(NULL, buffer->allocated - (ob->size - ob->pos));
    if (result == NULL) {
        PyErr_SetString(PyExc_MemoryError, "Unable to allocate output buffer.");
        return NULL;
    }

    /* Memory copy */
    if (Py_SIZE(buffer->list) > 0) {
        offset = PyBytes_AS_STRING(result);

        /* Blocks except the last one */
        Py_ssize_t i = 0;
        for (; i < Py_SIZE(buffer->list)-1; i++) {
            block = PyList_GET_ITEM(buffer->list, i);
            memcpy(offset,  PyBytes_AS_STRING(block), Py_SIZE(block));
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


static inline _zstd_state *
get_zstd_state_NOUSE(PyObject *module)
{
    void *state = PyModule_GetState(module);
    assert(state != NULL);
    return (_zstd_state *)state;
}

#define ACQUIRE_LOCK(obj) do { \
    if (!PyThread_acquire_lock((obj)->lock, 0)) { \
        Py_BEGIN_ALLOW_THREADS \
        PyThread_acquire_lock((obj)->lock, 1); \
        Py_END_ALLOW_THREADS \
    } } while (0)
#define RELEASE_LOCK(obj) PyThread_release_lock((obj)->lock)


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
    {ZSTD_c_dictIDFlag,       "dictIDFlag"}
};

static const ParameterInfo dp_list[] =
{
    {ZSTD_d_windowLogMax, "windowLogMax"}
};


/* Format an user friendly error message. */
static inline void
get_parameter_error_msg(char *buf, int buf_size, Py_ssize_t pos,
                        int key_v, int value_v, char is_compress)
{
    ParameterInfo const *list;
    int list_size;
    char const *name;
    char *type;
    ZSTD_bounds bounds;

    assert(buf_size >= 160);

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
                  "should %d <= value <= %d, provided value is %d.",
                  type, name, bounds.lowerBound, bounds.upperBound, value_v);
}

static int
module_add_int_constant(PyObject *m, const char *name, long long value)
{
    PyObject *o = PyLong_FromLongLong(value);
    if (o == NULL) {
        return -1;
    }
    if (PyModule_AddObject(m, name, o) == 0) {
        return 0;
    }
    Py_DECREF(o);
    return -1;
}

#define ADD_INT_MACRO(module, macro)                              \
    do {                                                                 \
        if (module_add_int_constant(module, #macro, macro) < 0) {  \
            return -1;                                                   \
        }                                                                \
    } while(0)

static int
add_parameters(PyObject *module)
{
    /* Compress parameters */
    ADD_INT_MACRO(module, ZSTD_c_compressionLevel);
    ADD_INT_MACRO(module, ZSTD_c_windowLog);
    ADD_INT_MACRO(module, ZSTD_c_hashLog);
    ADD_INT_MACRO(module, ZSTD_c_chainLog);
    ADD_INT_MACRO(module, ZSTD_c_searchLog);
    ADD_INT_MACRO(module, ZSTD_c_minMatch);
    ADD_INT_MACRO(module, ZSTD_c_targetLength);
    ADD_INT_MACRO(module, ZSTD_c_strategy);
    ADD_INT_MACRO(module, ZSTD_c_enableLongDistanceMatching);
    ADD_INT_MACRO(module, ZSTD_c_ldmHashLog);
    ADD_INT_MACRO(module, ZSTD_c_ldmMinMatch);
    ADD_INT_MACRO(module, ZSTD_c_ldmBucketSizeLog);
    ADD_INT_MACRO(module, ZSTD_c_ldmHashRateLog);
    ADD_INT_MACRO(module, ZSTD_c_contentSizeFlag);
    ADD_INT_MACRO(module, ZSTD_c_checksumFlag);
    ADD_INT_MACRO(module, ZSTD_c_dictIDFlag);

    /* Decompress parameters */
    ADD_INT_MACRO(module, ZSTD_d_windowLogMax);

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
    capsule = PyDict_GetItem(self->c_dicts, level);

    if (capsule != NULL) {
        /* ZSTD_CDict instance already exists */
        cdict = PyCapsule_GetPointer(capsule, NULL);
        goto success;
    } else {
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
        goto success;
    }

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
_ZstdDict_new(PyTypeObject *type, PyObject *args, PyObject *kwds)
{
    ZstdDict *self;
    self = (ZstdDict*)type->tp_alloc(type, 0);
    if (self == NULL) {
        PyErr_NoMemory();
        goto error;
    }

    assert(self->dict_content == NULL);
    assert(self->dict_id == 0);
    assert(self->d_dict == NULL);
    assert(self->inited == 0);

    /* ZSTD_CDict dict */
    self->c_dicts = PyDict_New();
    if (self->c_dicts == NULL) {
        PyErr_NoMemory();
        goto error;
    }

    /* Thread lock */
    self->lock = PyThread_allocate_lock();
    if (self->lock == NULL) {
        PyErr_SetString(PyExc_MemoryError, "Unable to allocate lock");
        goto error;
    }
    return (PyObject*)self;

error:
    Py_XDECREF(self);
    return NULL;
}

static void
_ZstdDict_dealloc(ZstdDict *self)
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

/*[clinic input]
_zstd.ZstdDict.__init__

    dict_content: object
        Dictionary's content, a bytes-like object.

Initialize a ZstdDict object, it can used for compress/decompress.
[clinic start generated code]*/

static int
_zstd_ZstdDict___init___impl(ZstdDict *self, PyObject *dict_content)
/*[clinic end generated code: output=49ae79dcbb8ad2df input=951e34a71eaceee0]*/
{
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

    /* Get dict_id */
    self->dict_id = ZDICT_getDictID(PyBytes_AS_STRING(dict_content),
                                    Py_SIZE(dict_content));
    if (self->dict_id == 0) {
        Py_CLEAR(self->dict_content);
        PyErr_SetString(PyExc_ValueError, "Not a valid Zstd dictionary content.");
        return -1;
    }

    return 0;
}


/*[clinic input]
_zstd.ZstdDict.__reduce__

Return state information for pickling.
[clinic start generated code]*/

static PyObject *
_zstd_ZstdDict___reduce___impl(ZstdDict *self)
/*[clinic end generated code: output=5c9b8a3550429417 input=1a45441f8f3f7085]*/
{
    return Py_BuildValue("O(O)", Py_TYPE(self), self->dict_content);
}


static PyMethodDef _ZstdDict_methods[] = {
    _ZSTD_ZSTDDICT___REDUCE___METHODDEF
    {NULL}
};

PyDoc_STRVAR(_ZstdDict_dict_doc,
    "Zstd dictionary, used for compress/decompress.");

PyDoc_STRVAR(ZstdDict_dictid_doc,
    "ID of Zstd dictionary, a 32-bit unsigned int value.");

PyDoc_STRVAR(ZstdDict_dictbuffer_doc,
    "The content of the Zstd dictionary, a bytes object.");

static PyObject *
_ZstdDict_str(ZstdDict *dict)
{
    char buf[64];
    PyOS_snprintf(buf, sizeof(buf),
                  "<ZstdDict dict_id=%u dict_size=%zd>",
                  dict->dict_id, Py_SIZE(dict->dict_content));

    return PyUnicode_FromString(buf);
}

static PyMemberDef _ZstdDict_members[] = {
    {"dict_id", T_UINT, offsetof(ZstdDict, dict_id), READONLY, ZstdDict_dictid_doc},
    {"dict_content", T_OBJECT_EX, offsetof(ZstdDict, dict_content), READONLY, ZstdDict_dictbuffer_doc},
    {NULL}
};

static PyType_Slot zstddict_slots[] = {
    {Py_tp_methods, _ZstdDict_methods},
    {Py_tp_members, _ZstdDict_members},
    {Py_tp_new, _ZstdDict_new},
    {Py_tp_dealloc, _ZstdDict_dealloc},
    {Py_tp_init, _zstd_ZstdDict___init__},
    {Py_tp_str, _ZstdDict_str},
    {Py_tp_doc, (char*)_ZstdDict_dict_doc},
    {0, 0}
};

static PyType_Spec zstddict_type_spec = {
    .name = "_zstd.ZstdDict",
    .basicsize = sizeof(ZstdDict),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    .slots = zstddict_slots,
};
/* ZstdDict code end */

/*[clinic input]
_zstd._train_dict

    dst_data: PyBytesObject
    dst_data_sizes: object
    dict_size: Py_ssize_t

Internal function, train a Zstd dictionary.
[clinic start generated code]*/

static PyObject *
_zstd__train_dict_impl(PyObject *module, PyBytesObject *dst_data,
                       PyObject *dst_data_sizes, Py_ssize_t dict_size)
/*[clinic end generated code: output=d39b262ebfcac776 input=b89015c8464efb81]*/
{
    size_t *chunk_sizes = NULL;
    PyObject *dict_buffer = NULL;
    size_t zstd_ret;

    /* Prepare chunk_sizes */
    const Py_ssize_t chunks_number = Py_SIZE(dst_data_sizes);
    if (chunks_number > UINT32_MAX) {
        PyErr_SetString(PyExc_ValueError, "Number of data chunks is too big, should <= 4294967295.");
        goto error;
    }

    chunk_sizes = PyMem_Malloc(chunks_number * sizeof(size_t));
    if (chunk_sizes == NULL) {
        goto error;
    }

    for (Py_ssize_t i = 0; i < chunks_number; i++) {
        PyObject *size = PyList_GET_ITEM(dst_data_sizes, i);
        chunk_sizes[i] = PyLong_AsSize_t(size);
        if (chunk_sizes[i] == -1 && PyErr_Occurred()) {
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
                                     chunk_sizes, (UINT32)chunks_number);
    Py_END_ALLOW_THREADS

    /* Check zstd dict error. */
    if (ZDICT_isError(zstd_ret)) {
        _zstd_state *state = get_zstd_state(module);
        PyErr_SetString(state->ZstdError, ZDICT_getErrorName(zstd_ret));
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

/* Set compressLevel or compress parameters to compress context. */
static int
set_c_parameters(ZstdCompressor *self, PyObject *level_or_option, int *compress_level)
{
    size_t zstd_ret;
    char msg_buf[160];

    assert(PyType_GetModuleState(Py_TYPE(self)) != NULL);

    /* Integer compression level */
    if (PyLong_Check(level_or_option)) {
        *compress_level = _PyLong_AsInt(level_or_option);
        if (*compress_level == -1 && PyErr_Occurred()) {
            PyErr_SetString(PyExc_ValueError,
                            "Compress level should be 32-bit signed int value.");
            return -1;
        }

        /* Set ZSTD_c_compressionLevel to compress context */
        zstd_ret = ZSTD_CCtx_setParameter(self->cctx, ZSTD_c_compressionLevel, *compress_level);

        /* Check error */
        if (ZSTD_isError(zstd_ret)) {
            _zstd_state *state = PyType_GetModuleState(Py_TYPE(self));
            PyErr_Format(state->ZstdError,
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

            /* Get ZSTD_c_compressionLevel for generating ZSTD_CDICT */
            if (key_v == ZSTD_c_compressionLevel) {
                *compress_level = value_v;
            }

            /* Set parameter to compress context */
            zstd_ret = ZSTD_CCtx_setParameter(self->cctx, key_v, value_v);
            if (ZSTD_isError(zstd_ret)) {
                _zstd_state *state = PyType_GetModuleState(Py_TYPE(self));
                get_parameter_error_msg(msg_buf, sizeof(msg_buf), pos, key_v, value_v, 1),
                PyErr_Format(state->ZstdError, msg_buf);
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
    _zstd_state *state = PyType_GetModuleState(Py_TYPE(self));
    assert(state != NULL);

    /* Check dict type */
    ret = PyObject_IsInstance(dict, (PyObject*)state->ZstdDict_type);
    if (ret < 0) {
        return -1;
    } else if (ret == 0) {
        PyErr_SetString(PyExc_TypeError, "dict argument should be ZstdDict object.");
        return -1;
    }

    /* Get ZSTD_CDict */
    c_dict = _get_CDict((ZstdDict*)dict, compress_level);
    if (c_dict == NULL) {
        PyErr_SetString(PyExc_SystemError, "Failed to get ZSTD_CDict.");
        return -1;
    }

    /* Reference a prepared dictionary */
    zstd_ret = ZSTD_CCtx_refCDict(self->cctx, c_dict);

    /* Check error */
    if (ZSTD_isError(zstd_ret)) {
        PyErr_SetString(state->ZstdError, ZSTD_getErrorName(zstd_ret));
        return -1;
    }
    return 0;
}


/* Set decompress parameters to decompress context. */
static int
set_d_parameters(ZstdDecompressor *self, PyObject *option)
{
    size_t zstd_ret;
    PyObject *key, *value;
    Py_ssize_t pos;
    char msg_buf[160];

    assert(PyType_GetModuleState(Py_TYPE(self)) != NULL);

    if (!PyDict_Check(option)) {
        PyErr_SetString(PyExc_TypeError, "option argument wrong type.");
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
            _zstd_state *state = PyType_GetModuleState(Py_TYPE(self));
            get_parameter_error_msg(msg_buf, sizeof(msg_buf), pos, key_v, value_v, 0),
            PyErr_Format(state->ZstdError, msg_buf);
            return -1;
        }
    }
    return 0;
}

/* Load dictionary (ZSTD_DDict instance) to decompress context (ZSTD_DCtx instance). */
static int
load_d_dict(ZstdDecompressor *self, PyObject *dict)
{
    size_t zstd_ret;
    ZSTD_DDict *d_dict;
    int ret;
    _zstd_state *state = PyType_GetModuleState(Py_TYPE(self));
    assert(state != NULL);

    /* Check dict type */
    ret = PyObject_IsInstance(dict, (PyObject*)state->ZstdDict_type);
    if (ret < 0) {
        return -1;
    } else if (ret == 0) {
        PyErr_SetString(PyExc_TypeError, "dict argument should be ZstdDict object.");
        return -1;
    }

    /* Get ZSTD_DDict */
    d_dict = _get_DDict((ZstdDict*)dict);
    if (d_dict == NULL) {
        PyErr_SetString(PyExc_SystemError, "Failed to get ZSTD_DDict.");
        return -1;
    }

    /* Reference a decompress dictionary */
    zstd_ret = ZSTD_DCtx_refDDict(self->dctx, d_dict);

    /* Check error */
    if (ZSTD_isError(zstd_ret)) {
        PyErr_SetString(state->ZstdError, ZSTD_getErrorName(zstd_ret));
        return -1;
    }
    return 0;
}


static PyObject *
_ZstdCompressor_new(PyTypeObject *type, PyObject *args, PyObject *kwds)
{
    ZstdCompressor *self;
    self = (ZstdCompressor*)type->tp_alloc(type, 0);
    if (self == NULL) {
        PyErr_NoMemory();
        goto error;
    }

    assert(self->dict == NULL);
    assert(self->inited == 0);

    /* Compress context */
    self->cctx = ZSTD_createCCtx();
    if (self->cctx == NULL) {
        PyErr_SetString(PyExc_RuntimeError, "Unable to create ZSTD_CCtx instance.");
        goto error;
    }

    /* Last end directive */
    self->last_end_directive = ZSTD_e_end;

    /* Thread lock */
    self->lock = PyThread_allocate_lock();
    if (self->lock == NULL) {
        PyErr_SetString(PyExc_MemoryError, "Unable to allocate lock");
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

/*[clinic input]
_zstd.ZstdCompressor.__init__

    level_or_option: object = None
        It can be an int object, in this case represents the compression
        level. It can also be a dictionary for setting various advanced
        parameters. The default value None means to use zstd's default
        compression level/parameters.
    zstd_dict: object = None
        Pre-trained dictionary for compression, a ZstdDict object.

Initialize a ZstdCompressor object.
[clinic start generated code]*/

static int
_zstd_ZstdCompressor___init___impl(ZstdCompressor *self,
                                   PyObject *level_or_option,
                                   PyObject *zstd_dict)
/*[clinic end generated code: output=65d92fb9ff1519cb input=c1f7dd886ebfed34]*/
{
    int compress_level = 0; /* 0 means use zstd's default compression level */

    assert(PyType_GetModuleState(Py_TYPE(self)) != NULL);

    /* Only called once */
    if (self->inited) {
        PyErr_SetString(PyExc_RuntimeError, 
                        "ZstdCompressor.__init__ function was called twice.");
        return -1;
    }
    self->inited = 1;

    /* Set compressLevel/options to compress context */
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
              ZSTD_EndDirective end_directive)
{
    ZSTD_inBuffer in;
    ZSTD_outBuffer out;
    BlocksOutputBuffer buffer;
    size_t zstd_ret;
    PyObject *ret;

    assert(PyType_GetModuleState(Py_TYPE(self)) != NULL);

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

    /* OutputBuffer(OnError)(&buffer) is after `error` label,
       so initialize the buffer before any `goto error` statement. */
    if (OutputBuffer_InitAndGrow(&buffer, -1, &out) < 0) {
        goto error;
    }

    /* zstd stream compress */
    while (1) {
        Py_BEGIN_ALLOW_THREADS
        zstd_ret = ZSTD_compressStream2(self->cctx, &out, &in, end_directive);
        Py_END_ALLOW_THREADS

        /* Check error */
        if (ZSTD_isError(zstd_ret)) {
            _zstd_state *state = PyType_GetModuleState(Py_TYPE(self));
            PyErr_SetString(state->ZstdError, ZSTD_getErrorName(zstd_ret));
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

        /* Output buffer exhausted, grow the buffer */
        if (out.pos == out.size) {
            if (OutputBuffer_Grow(&buffer, &out) < 0) {
                goto error;
            }
        }

        assert(in.pos <= in.size);
    }

error:
    OutputBuffer_OnError(&buffer);
    ret = NULL;
success:
    return ret;
}

/*[clinic input]
_zstd.ZstdCompressor.compress

    data: Py_buffer
        Data to be compressed, a bytes-like object.
        
    end_directive: int(c_default="ZSTD_e_continue") = EndDirective.CONTINUE
        EndDirective.CONTINUE: Collect more data, encoder decides when to output
        compressed result, for optimal compression ratio. Usually used for ordinary
        streaming compression.
        EndDirective.FLUSH: Flush any remaining data, but don't end current frame.
        Usually used for communication, the receiver can decode the data immediately.
        EndDirective.END: Flush any remaining data _and_ close current frame.

Provide data to the compressor object.

Returns a chunk of compressed data if possible, or b'' otherwise.
[clinic start generated code]*/

static PyObject *
_zstd_ZstdCompressor_compress_impl(ZstdCompressor *self, Py_buffer *data,
                                   int end_directive)
/*[clinic end generated code: output=09f541ea51afd468 input=9a74f09aefc25554]*/
{
    PyObject *ret;

    ACQUIRE_LOCK(self);
    ret = compress_impl(self, data, end_directive);

    if (ret) {
        self->last_end_directive = end_directive;
    }
    RELEASE_LOCK(self);

    return ret;
}

/*[clinic input]
_zstd.ZstdCompressor.flush

    end_frame: bool=True
        True flush data and end the frame.
        False flush data, don't end the frame, usually used for communication,
        the receiver can decode the data immediately.

Finish the compression process.

Returns the compressed data left in internal buffers.

Since zstd data consists of one or more independent frames, the compressor
object can be used after this method is called.
[clinic start generated code]*/

static PyObject *
_zstd_ZstdCompressor_flush_impl(ZstdCompressor *self, int end_frame)
/*[clinic end generated code: output=0206a53c394f4620 input=a7bc773c3a228735]*/
{
    PyObject *ret;
    const int end_directive = end_frame ? ZSTD_e_end : ZSTD_e_flush;

    ACQUIRE_LOCK(self);
    ret = compress_impl(self, NULL, end_directive);

    if (ret) {
        self->last_end_directive = end_directive;
    }
    RELEASE_LOCK(self);

    return ret;
}


/*[clinic input]
_zstd.ZstdCompressor.__reduce__
[clinic start generated code]*/

static PyObject *
_zstd_ZstdCompressor___reduce___impl(ZstdCompressor *self)
/*[clinic end generated code: output=1042cabbf3957e9c input=d943c46618a56ffe]*/
{
    PyErr_Format(PyExc_TypeError,
                 "Cannot pickle %s object.",
                 Py_TYPE(self)->tp_name);
    return NULL;
}


static PyMethodDef _ZstdCompressor_methods[] = {
    _ZSTD_ZSTDCOMPRESSOR_COMPRESS_METHODDEF
    _ZSTD_ZSTDCOMPRESSOR_FLUSH_METHODDEF
    _ZSTD_ZSTDCOMPRESSOR___REDUCE___METHODDEF
    {NULL, NULL}
};

PyDoc_STRVAR(ZstdCompressor_last_end_directive_doc,
"The last end directive, initialized as ZSTD_e_end.");

static PyMemberDef _ZstdCompressor_members[] = {
    {"last_end_directive", T_INT, offsetof(ZstdCompressor, last_end_directive),
      READONLY, ZstdCompressor_last_end_directive_doc},
    {NULL}
};

PyDoc_STRVAR(_ZstdCompressor_doc,
    "Zstd dictionary, used for compress/decompress.");

static PyType_Slot zstdcompressor_slots[] = {
    {Py_tp_new, _ZstdCompressor_new},
    {Py_tp_dealloc, _ZstdCompressor_dealloc},
    {Py_tp_init, _zstd_ZstdCompressor___init__},
    {Py_tp_methods, _ZstdCompressor_methods},
    {Py_tp_members, _ZstdCompressor_members},
    {Py_tp_doc, (char*)_zstd_ZstdCompressor___init____doc__},
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

/* ZstdDecompressor */

static PyObject *
_ZstdDecompressor_new(PyTypeObject *type, PyObject *args, PyObject *kwds)
{
    ZstdDecompressor *self;
    self = (ZstdDecompressor*)type->tp_alloc(type, 0);
    if (self == NULL) {
        PyErr_NoMemory();
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

    /* Need input flag */
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
        PyErr_SetString(PyExc_MemoryError, "Unable to allocate lock");
        goto error;
    }
    return (PyObject*)self;

error:
    Py_XDECREF(self);
    return NULL;
}

static void
_ZstdDecompressor_dealloc(ZstdDecompressor *self)
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

/*[clinic input]
_zstd.ZstdDecompressor.__init__

    zstd_dict: object = None
        Pre-trained dictionary for decompression, a ZstdDict object.
    option: object = None
        A dictionary for setting advanced parameters. The default
        value None means to use zstd's default decompression parameters.

Initialize a ZstdDecompressor object.
[clinic start generated code]*/

static int
_zstd_ZstdDecompressor___init___impl(ZstdDecompressor *self,
                                     PyObject *zstd_dict, PyObject *option)
/*[clinic end generated code: output=182ba99f2278542e input=be83f6924b0baaf7]*/
{
    assert(PyType_GetModuleState(Py_TYPE(self)) != NULL);

    /* Only called once */
    if (self->inited) {
        PyErr_SetString(PyExc_RuntimeError, 
                        "ZstdDecompressor.__init__ function was called twice.");
        return -1;
    }
    self->inited = 1;

    /* Set decompressLevel/options to decompress context */
    if (option != Py_None) {
        if (set_d_parameters(self, option) < 0) {
            return -1;
        }
    }

    /* Load dictionary to decompress context */
    if (zstd_dict != Py_None) {
        if (load_d_dict(self, zstd_dict) < 0) {
            return -1;
        }

        /* Py_INCREF the dict */
        Py_INCREF(zstd_dict);
        self->dict = zstd_dict;
    }

    return 0;
}

static inline PyObject *
decompress_impl(ZstdDecompressor *self, ZSTD_inBuffer *in,
                Py_buffer *data, Py_ssize_t max_length)
{
    size_t zstd_ret;
    ZSTD_outBuffer out;
    BlocksOutputBuffer buffer;
    PyObject *ret;

    assert(PyType_GetModuleState(Py_TYPE(self)) != NULL);

    /* OutputBuffer(OnError)(&buffer) is after `error` label,
       so initialize the buffer before any `goto error` statement. */
    if (OutputBuffer_InitAndGrow(&buffer, max_length, &out) < 0) {
        goto error;
    }

    while (1) {
        Py_BEGIN_ALLOW_THREADS
        zstd_ret = ZSTD_decompressStream(self->dctx, &out, in);
        Py_END_ALLOW_THREADS

        /* Check error */
        if (ZSTD_isError(zstd_ret)) {
            _zstd_state *state = PyType_GetModuleState(Py_TYPE(self));
            PyErr_SetString(state->ZstdError, ZSTD_getErrorName(zstd_ret));
            goto error;
        }

        if (out.pos == out.size) {
            /* Output buffer exhausted.
               Need to check `out` before `in`. Maybe zstd's internal buffer still
               have a few bytes can be output, grow the output buffer and continue
               if max_lengh < 0. */

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
    /* (zstd_ret == 0) means a frame is completely decoded and fully flushed */
    self->at_frame_edge = (zstd_ret == 0) ? 1 : 0;
    return ret;
error:
    OutputBuffer_OnError(&buffer);
    return NULL;
}

/*[clinic input]
_zstd.ZstdDecompressor.decompress

    data: Py_buffer
        Data to be decompressed, a bytes-like object.
    max_length: Py_ssize_t = -1
        If max_length is nonnegative, returns at most max_length bytes of
        decompressed data. If this limit is reached and further output can be
        produced, the needs_input attribute will be set to False. In this case,
        the next call to decompress() may provide data as b'' to obtain more of
        the output.

Decompress *data*, returning uncompressed data as bytes.

If all of the input data was decompressed and returned (either because this
was less than *max_length *bytes, or because *max_length *was negative),
*self.needs_input *will be set to True.
[clinic start generated code]*/

static PyObject *
_zstd_ZstdDecompressor_decompress_impl(ZstdDecompressor *self,
                                       Py_buffer *data,
                                       Py_ssize_t max_length)
/*[clinic end generated code: output=a4302b3c940dbec6 input=c745631ba8a11577]*/
{
    ZSTD_inBuffer in;
    PyObject *ret;
    char use_input_buffer;

    ACQUIRE_LOCK(self);

    if (self->in_begin == self->in_end) {
        /* No unconsumed data */
        use_input_buffer = 0;

        in.src = data->buf;
        in.size = data->len;
        in.pos = 0;
    } else if (data->len == 0) {
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

        if (avail_total < data->len) {
            uint8_t *tmp;
            const size_t new_size = used_now + data->len;

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
        } else if (avail_now < data->len) {
            /* Move unconsumed data to the beginning */
            memmove(self->input_buffer,
                    self->input_buffer + self->in_begin,
                    used_now);

            /* Set begin & end position */
            self->in_begin = 0;
            self->in_end = used_now;
        }

        /* Copy data to input buffer */
        memcpy(self->input_buffer + self->in_end, data->buf, data->len);
        self->in_end += data->len;

        in.src = self->input_buffer + self->in_begin;
        in.size = used_now + data->len;
        in.pos = 0;
    }
    assert(in.pos == 0);

    /* Decompress */
    ret = decompress_impl(self, &in, data, max_length);
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
    /* Reset needs_input */
    self->needs_input = 1;

    /* Clear input_buffer */
    self->in_begin = 0;
    self->in_end = 0;

    ret = NULL;
success:
    RELEASE_LOCK(self);
    return ret;
}


/*[clinic input]
_zstd.ZstdDecompressor.__reduce__
[clinic start generated code]*/

static PyObject *
_zstd_ZstdDecompressor___reduce___impl(ZstdDecompressor *self)
/*[clinic end generated code: output=3b2f7c81240639b5 input=9eda5eb42eec2e2b]*/
{
    PyErr_Format(PyExc_TypeError,
                 "Cannot pickle %s object.",
                 Py_TYPE(self)->tp_name);
    return NULL;
}

static PyMethodDef _ZstdDecompressor_methods[] = {
    _ZSTD_ZSTDDECOMPRESSOR_DECOMPRESS_METHODDEF
    _ZSTD_ZSTDDECOMPRESSOR___REDUCE___METHODDEF
    {NULL, NULL}
};

PyDoc_STRVAR(ZstdDecompressor_needs_input_doc,
"True if more input is needed before more decompressed data can be produced.");

PyDoc_STRVAR(ZstdDecompressor_at_frame_edge_doc,
"True when the output is at a frame edge, means a frame is completely decoded "
"and fully flushed, or the decompressor just be initialized. Note that the input "
"stream is not necessarily at a frame edge.");

static PyMemberDef _ZstdDecompressor_members[] = {
    {"needs_input", T_BOOL, offsetof(ZstdDecompressor, needs_input),
      READONLY, ZstdDecompressor_needs_input_doc},
    {"at_frame_edge", T_BOOL, offsetof(ZstdDecompressor, at_frame_edge),
      READONLY, ZstdDecompressor_at_frame_edge_doc},
    {NULL}
};

static PyType_Slot zstddecompressor_slots[] = {
    {Py_tp_new, _ZstdDecompressor_new},
    {Py_tp_dealloc, _ZstdDecompressor_dealloc},
    {Py_tp_init, _zstd_ZstdDecompressor___init__},
    {Py_tp_methods, _ZstdDecompressor_methods},
    {Py_tp_members, _ZstdDecompressor_members},
    {Py_tp_doc, (char*)_zstd_ZstdDecompressor___init____doc__},
    {0, 0}
};

static PyType_Spec zstddecompressor_type_spec = {
    .name = "_zstd.ZstdDecompressor",
    .basicsize = sizeof(ZstdDecompressor),
    /* Calling PyType_GetModuleState() on a subclass is not safe.
       zstddecompressor_type_spec does not have Py_TPFLAGS_BASETYPE flag
       which prevents to create a subclass.
       So calling PyType_GetModuleState() in this file is always safe. */
    .flags = Py_TPFLAGS_DEFAULT,
    .slots = zstddecompressor_slots,
};


/*[clinic input]
_zstd._get_cparam_bounds

    cParam: int

Internal funciton, get cParameter bounds.
[clinic start generated code]*/

static PyObject *
_zstd__get_cparam_bounds_impl(PyObject *module, int cParam)
/*[clinic end generated code: output=5b0f68046a6f0721 input=c7dd07c0298fdba3]*/
{
    PyObject *ret;

    ZSTD_bounds const bound = ZSTD_cParam_getBounds(cParam);
    if (ZSTD_isError(bound.error)) {
        _zstd_state *state = get_zstd_state(module);
        PyErr_SetString(state->ZstdError, ZSTD_getErrorName(bound.error));
        return NULL;
    }

    ret = PyTuple_New(2);
    if (ret == NULL) {
        PyErr_NoMemory();
        return NULL;
    }

    PyTuple_SET_ITEM(ret, 0, PyLong_FromLong(bound.lowerBound));
    PyTuple_SET_ITEM(ret, 1, PyLong_FromLong(bound.upperBound));

    return ret;
}

/*[clinic input]
_zstd._get_dparam_bounds

    dParam: int

Internal funciton, get dParameter bounds.
[clinic start generated code]*/

static PyObject *
_zstd__get_dparam_bounds_impl(PyObject *module, int dParam)
/*[clinic end generated code: output=6382b8e9779430c2 input=9749914e8f919d60]*/
{
    PyObject *ret;

    ZSTD_bounds const bound = ZSTD_dParam_getBounds(dParam);
    if (ZSTD_isError(bound.error)) {
        _zstd_state *state = get_zstd_state(module);
        PyErr_SetString(state->ZstdError, ZSTD_getErrorName(bound.error));
        return NULL;
    }

    ret = PyTuple_New(2);
    if (ret == NULL) {
        PyErr_NoMemory();
        return NULL;
    }

    PyTuple_SET_ITEM(ret, 0, PyLong_FromLong(bound.lowerBound));
    PyTuple_SET_ITEM(ret, 1, PyLong_FromLong(bound.upperBound));

    return ret;
}

/*[clinic input]
_zstd.get_frame_info

    frame_buffer: Py_buffer
        A bytes-like object. It should starts from the beginning of a frame, and
        needs to include at least the frame header (2 to 14 bytes).

Get zstd frame infomation from a frame header.

Return a two-items tuple: (decompressed_size, dictinary_id). If decompressed
size is unknown (generated by stream compression), it will be None. If no
dictionary, dictinary_id will be 0.
[clinic start generated code]*/

static PyObject *
_zstd_get_frame_info_impl(PyObject *module, Py_buffer *frame_buffer)
/*[clinic end generated code: output=56e033cf48001929 input=5738fc9c9eeda6dd]*/
{
    unsigned long long content_size;
    char unknown_content_size;
    UINT32 dict_id;
    PyObject *temp;
    PyObject *ret = NULL;

    /* ZSTD_getFrameContentSize */
    content_size = ZSTD_getFrameContentSize(frame_buffer->buf,
                                            frame_buffer->len);
    if (content_size == ZSTD_CONTENTSIZE_UNKNOWN) {
        unknown_content_size = 1;
    } else if (content_size == ZSTD_CONTENTSIZE_ERROR) {
        _zstd_state *state = get_zstd_state(module);
        PyErr_SetString(state->ZstdError,
                        "Error when getting frame content size, "
                        "please make sure that frame_buffer points "
                        "to the beginning of a frame and provide "
                        "a size larger than the frame header.");
        goto error;
    } else {
        unknown_content_size = 0;
    }

    /* ZSTD_getDictID_fromFrame */
    dict_id = ZSTD_getDictID_fromFrame(frame_buffer->buf, frame_buffer->len);

    /* Build tuple */
    ret = PyTuple_New(2);
    if (ret == NULL) {
        PyErr_NoMemory();
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

    return ret;
error:
    Py_XDECREF(ret);
    return NULL;
}


/*[clinic input]
_zstd.get_frame_size

    frame_buffer: Py_buffer
        A bytes-like object. It should starts from the beginning of a frame,
        and needs to contain at least one complete frame.

Get the size of a zstd frame.

It will iterate all blocks' header within a frame, to get the size of the
frame.
[clinic start generated code]*/

static PyObject *
_zstd_get_frame_size_impl(PyObject *module, Py_buffer *frame_buffer)
/*[clinic end generated code: output=a7384c2f8780f442 input=f21fb47ec793e693]*/
{
    size_t frame_size;
    PyObject *ret;

    frame_size = ZSTD_findFrameCompressedSize(frame_buffer->buf, frame_buffer->len);
    if (ZSTD_isError(frame_size)) {
        _zstd_state *state = get_zstd_state(module);
        PyErr_SetString(state->ZstdError, ZSTD_getErrorName(frame_size));
        return NULL;
    }

    ret = PyLong_FromSize_t(frame_size);
    if (ret == NULL) {
        PyErr_NoMemory();
        return NULL;
    }

    return ret;
}


static int
zstd_exec(PyObject *module)
{
    PyObject *temp;
    _zstd_state *state = get_zstd_state(module);

    /* Add zstd parameters */
    if (add_parameters(module) < 0) {
        return -1;
    }

    /* ZSTD_strategy enum */
    ADD_INT_MACRO(module, ZSTD_fast);
    ADD_INT_MACRO(module, ZSTD_dfast);
    ADD_INT_MACRO(module, ZSTD_greedy);
    ADD_INT_MACRO(module, ZSTD_lazy);
    ADD_INT_MACRO(module, ZSTD_lazy2);
    ADD_INT_MACRO(module, ZSTD_btlazy2);
    ADD_INT_MACRO(module, ZSTD_btopt);
    ADD_INT_MACRO(module, ZSTD_btultra);
    ADD_INT_MACRO(module, ZSTD_btultra2);

    /* EndDirective enum */
    ADD_INT_MACRO(module, ZSTD_e_continue);
    ADD_INT_MACRO(module, ZSTD_e_flush);
    ADD_INT_MACRO(module, ZSTD_e_end);

    /* Set state's objects to NULL */
    state->ZstdError = NULL;
    state->ZstdDict_type = NULL;
    state->ZstdCompressor_type = NULL;
    state->ZstdDecompressor_type = NULL;

    /* ZstdError */
    state->ZstdError = PyErr_NewExceptionWithDoc("_zstd.ZstdError", "Call to zstd failed.", NULL, NULL);
    if (state->ZstdError == NULL) {
        goto error;
    }
    if (PyModule_AddType(module, (PyTypeObject *)state->ZstdError) < 0) {
        goto error;
    }

    /* ZstdDict */
    state->ZstdDict_type = (PyTypeObject *)PyType_FromModuleAndSpec(module,
                                                                    &zstddict_type_spec,
                                                                    NULL);
    if (state->ZstdDict_type == NULL) {
        goto error;
    }
    if (PyModule_AddType(module, (PyTypeObject *)state->ZstdDict_type) < 0) {
        goto error;
    }

    /* ZstdCompressor */
    state->ZstdCompressor_type = (PyTypeObject*)PyType_FromModuleAndSpec(module,
                                                &zstdcompressor_type_spec, NULL);
    if (state->ZstdCompressor_type == NULL) {
        goto error;
    }
    if (PyModule_AddType(module, (PyTypeObject*)state->ZstdCompressor_type) < 0) {
        goto error;
    }

    /* ZstdDecompressor */
    state->ZstdDecompressor_type = (PyTypeObject*)PyType_FromModuleAndSpec(module,
                                                  &zstddecompressor_type_spec, NULL);
    if (state->ZstdDecompressor_type == NULL) {
        goto error;
    }
    if (PyModule_AddType(module, (PyTypeObject*)state->ZstdDecompressor_type) < 0) {
        goto error;
    }

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

    /* compress_level_bounds */
    if (!(temp = PyTuple_New(2))) {
        goto error;
    }
    PyTuple_SET_ITEM(temp, 0, PyLong_FromLong(ZSTD_minCLevel()));
    PyTuple_SET_ITEM(temp, 1, PyLong_FromLong(ZSTD_maxCLevel()));
    if (PyModule_AddObject(module, "compress_level_bounds", temp) < 0) {
        Py_DECREF(temp);
        goto error;
    }

    return 0;

error:
    Py_XDECREF(state->ZstdError);
    Py_XDECREF(state->ZstdDict_type);
    Py_XDECREF(state->ZstdCompressor_type);
    Py_XDECREF(state->ZstdDecompressor_type);
    return -1;
}

static PyMethodDef _zstd_methods[] = {
    _ZSTD__TRAIN_DICT_METHODDEF
    _ZSTD__GET_CPARAM_BOUNDS_METHODDEF
    _ZSTD__GET_DPARAM_BOUNDS_METHODDEF
    _ZSTD_GET_FRAME_INFO_METHODDEF
    _ZSTD_GET_FRAME_SIZE_METHODDEF
    {NULL}
};

static PyModuleDef_Slot _zstd_slots[] = {
    {Py_mod_exec, zstd_exec},
    {0, NULL}
};

static int
_zstd_traverse(PyObject *module, visitproc visit, void *arg)
{
    _zstd_state *state = get_zstd_state(module);
    Py_VISIT(state->ZstdError);
    Py_VISIT(state->ZstdDict_type);
    Py_VISIT(state->ZstdCompressor_type);
    Py_VISIT(state->ZstdDecompressor_type);
    return 0;
}

static int
_zstd_clear(PyObject *module)
{
    _zstd_state *state = get_zstd_state(module);
    Py_CLEAR(state->ZstdError);
    Py_CLEAR(state->ZstdDict_type);
    Py_CLEAR(state->ZstdCompressor_type);
    Py_CLEAR(state->ZstdDecompressor_type);
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
    .m_size = sizeof(_zstd_state),
    .m_methods = _zstd_methods,
    .m_slots = _zstd_slots,
    .m_traverse = _zstd_traverse,
    .m_clear = _zstd_clear,
    .m_free = _zstd_free,
};

#include "pypi2.h"

// PyMODINIT_FUNC
// PyInit__zstd(void)
// {
//     return PyModuleDef_Init(&_zstdmodule);
// }