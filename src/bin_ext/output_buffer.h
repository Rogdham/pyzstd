#include "pyzstd.h"

/* This file has two codes:
   1, mremap output buffer
      When realloc, mremap can avoid memcpy, so this code uses
      _PyBytes_Resize to extend output buffer.
   2, Blocks output buffer
      This code uses blocks to represent output buffer, it can
      provide decent performance on systems without mremap. */

/* On Linux, use mremap output buffer. */
#if defined(__linux__) && defined(_GNU_SOURCE) && !defined(PYZSTD_NO_MREMAP)
#  define PYZSTD_MREMAP_OUTPUT_BUFFER
#endif

#define KB (1024)
#define MB (1024*KB)
static const char unable_allocate_msg[] = "Unable to allocate output buffer.";

#ifdef PYZSTD_MREMAP_OUTPUT_BUFFER
/* -----------------------------
     mremap output buffer code
   ----------------------------- */
#define PYZSTD_OB_INIT_SIZE (64*KB)

typedef struct {
    /* Bytes object */
    PyObject *obj;
    /* Max length of the buffer, negative number for unlimited length. */
    Py_ssize_t max_length;
} OutputBuffer;
#define PYZSTD_OUTPUT_BUFFER(BUFFER) \
        OutputBuffer BUFFER = {.obj = NULL};

/* Initialize the buffer, and grow the buffer.
   max_length: Max length of the buffer, -1 for unlimited length.
   Return 0 on success
   Return -1 on failure */
static inline int
OutputBuffer_InitAndGrow(OutputBuffer *buffer, ZSTD_outBuffer *ob,
                         const Py_ssize_t max_length)
{
    PyObject *b;
    Py_ssize_t block_size;

    /* Ensure .obj was set to NULL */
    assert(buffer->obj == NULL);

    /* Get block size */
    if (0 <= max_length && max_length < PYZSTD_OB_INIT_SIZE) {
        block_size = max_length;
    } else {
        block_size = PYZSTD_OB_INIT_SIZE;
    }

    /* The initial bytes object */
    b = PyBytes_FromStringAndSize(NULL, block_size);
    if (b == NULL) {
        return -1;
    }

    /* Set variables */
    buffer->obj = b;
    buffer->max_length = max_length;

    ob->dst = PyBytes_AS_STRING(b);
    ob->size = (size_t) block_size;
    ob->pos = 0;
    return 0;
}

/* Initialize the buffer, with an initial size.
   init_size: the initial size.
   Return 0 on success
   Return -1 on failure */
static inline int
OutputBuffer_InitWithSize(OutputBuffer *buffer, ZSTD_outBuffer *ob,
                          const Py_ssize_t max_length,
                          const Py_ssize_t init_size)
{
    PyObject *b;
    Py_ssize_t block_size;

    /* Ensure .obj was set to NULL */
    assert(buffer->obj == NULL);

    /* Get block size */
    if (0 <= max_length && max_length < init_size) {
        block_size = max_length;
    } else {
        block_size = init_size;
    }

    /* The initial block */
    b = PyBytes_FromStringAndSize(NULL, block_size);
    if (b == NULL) {
        PyErr_SetString(PyExc_MemoryError, unable_allocate_msg);
        return -1;
    }

    /* Set variables */
    buffer->obj = b;
    buffer->max_length = max_length;

    ob->dst = PyBytes_AS_STRING(b);
    ob->size = (size_t) block_size;
    ob->pos = 0;
    return 0;
}

/* Grow the buffer. The avail_out must be 0, please check it before calling.
   Return 0 on success
   Return -1 on failure */
static inline int
OutputBuffer_Grow(OutputBuffer *buffer, ZSTD_outBuffer *ob)
{
    Py_ssize_t new_size;
    const Py_ssize_t old_size = Py_SIZE(buffer->obj);
    const Py_ssize_t max_length = buffer->max_length;

    /* Ensure no gaps in the data */
    assert(ob->pos == ob->size);

    /* Get new size. Note that it can't be 0.
       This sequence works well on Ubuntu 22.04:
       64 KB, 64 KB, 128 KB, 128 KB, 128 KB... */
    if (old_size == 0) {
        new_size = PYZSTD_OB_INIT_SIZE;
    } else if (old_size < 128*KB) {
        new_size = old_size + 64*KB;
    } else {
        new_size = old_size + 128*KB;
    }

    /* Check overflow */
    if (new_size < 0) {
        PyErr_SetString(PyExc_MemoryError, unable_allocate_msg);
        return -1;
    }

    /* Check max_length */
    if (0 <= max_length && max_length < new_size) {
        new_size = max_length;
    }

    /* Resize */
    if (_PyBytes_Resize(&buffer->obj, new_size) < 0) {
        /* buffer->obj is set to NULL */
        PyErr_SetString(PyExc_MemoryError, unable_allocate_msg);
        return -1;
    }

    /* Set variables */
    ob->dst = PyBytes_AS_STRING(buffer->obj) + old_size;
    ob->size = (size_t)(new_size - old_size);
    ob->pos = 0;
    return 0;
}


/* Whether the output data has reached max_length.
   The avail_out must be 0, please check it before calling. */
static inline int
OutputBuffer_ReachedMaxLength(OutputBuffer *buffer, ZSTD_outBuffer *ob)
{
    /* Ensure (data size == allocated size) */
    assert(ob->pos == ob->size);

    return Py_SIZE(buffer->obj) == buffer->max_length;
}

/* Finish the buffer.
   Return a bytes object on success
   Return NULL on failure */
static inline PyObject *
OutputBuffer_Finish(OutputBuffer *buffer, ZSTD_outBuffer *ob)
{
    PyObject *ret;
    const Py_ssize_t new_size = Py_SIZE(buffer->obj) - (ob->size - ob->pos);

    /* Resize */
    if (_PyBytes_Resize(&buffer->obj, new_size) < 0) {
        /* buffer->obj is NULL */
        PyErr_SetString(PyExc_MemoryError, unable_allocate_msg);
        return NULL;
    }

    ret = buffer->obj;
    buffer->obj = NULL;
    return ret;
}

/* Clean up the buffer */
static inline void
OutputBuffer_OnError(OutputBuffer *buffer)
{
    Py_CLEAR(buffer->obj);
}

#else
/* -----------------------------
     Blocks output buffer code
   ----------------------------- */
typedef struct {
    /* List of blocks */
    PyObject *list;
    /* Number of whole allocated size */
    Py_ssize_t allocated;
    /* Max length of the buffer, negative number for unlimited length. */
    Py_ssize_t max_length;
} OutputBuffer;
#define PYZSTD_OUTPUT_BUFFER(BUFFER) \
        OutputBuffer BUFFER = {.list = NULL};

/* Block size sequence */
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
OutputBuffer_InitAndGrow(OutputBuffer *buffer, ZSTD_outBuffer *ob,
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
OutputBuffer_InitWithSize(OutputBuffer *buffer, ZSTD_outBuffer *ob,
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
OutputBuffer_Grow(OutputBuffer *buffer, ZSTD_outBuffer *ob)
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
OutputBuffer_ReachedMaxLength(OutputBuffer *buffer, ZSTD_outBuffer *ob)
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
OutputBuffer_Finish(OutputBuffer *buffer, ZSTD_outBuffer *ob)
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
OutputBuffer_OnError(OutputBuffer *buffer)
{
    Py_CLEAR(buffer->list);
}

#endif
