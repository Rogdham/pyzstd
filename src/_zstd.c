/* _lzma - Low-level Python interface to liblzma.

   Initial implementation by Per Øyvind Karlsen.
   Rewritten by Nadeem Vawda.

*/

#define PY_SSIZE_T_CLEAN

#include "Python.h"
#include "structmember.h"         // PyMemberDef

#include <stdarg.h>
#include <string.h>

#include "..\lib\zstd.h"
#include "clinic\_zstd.c.h"

/* _BlocksOutputBuffer code */
typedef struct {
    /* List of blocks */
    PyObject *list;
    /* Number of whole allocated size. */
    Py_ssize_t allocated;

    /* Max length of the buffer, negative number for unlimited length. */
    Py_ssize_t max_length;
} _BlocksOutputBuffer;


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
_BlocksOutputBuffer_InitAndGrow(_BlocksOutputBuffer *buffer, Py_ssize_t max_length,
                                ZSTD_outBuffer *ob)
{
    PyObject *b;
    int block_size;

    // Set & check max_length
    buffer->max_length = max_length;
    if (max_length >= 0 && BUFFER_BLOCK_SIZE[0] > max_length) {
        block_size = (int) max_length;
    } else {
        block_size = BUFFER_BLOCK_SIZE[0];
    }

    // The first block
    b = PyBytes_FromStringAndSize(NULL, block_size);
    if (b == NULL) {
        buffer->list = NULL; // For _BlocksOutputBuffer_OnError()
        return -1;
    }

    // Create list
    buffer->list = PyList_New(1);
    if (buffer->list == NULL) {
        Py_DECREF(b);
        return -1;
    }
    PyList_SET_ITEM(buffer->list, 0, b);

    // Set variables
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
_BlocksOutputBuffer_Grow(_BlocksOutputBuffer *buffer, ZSTD_outBuffer *ob)
{
    PyObject *b;
    const Py_ssize_t list_len = Py_SIZE(buffer->list);
    int block_size;

    // Ensure no gaps in the data
    assert(ob->pos == ob->size);

    // Get block size
    if (list_len < Py_ARRAY_LENGTH(BUFFER_BLOCK_SIZE)) {
        block_size = BUFFER_BLOCK_SIZE[list_len];
    } else {
        block_size = BUFFER_BLOCK_SIZE[Py_ARRAY_LENGTH(BUFFER_BLOCK_SIZE) - 1];
    }

    // Check max_length
    if (buffer->max_length >= 0) {
        // Prevent adding unlimited number of empty bytes to the list.
        if (buffer->max_length == 0) {
            assert(ob->pos == ob->size);
            return 0;
        }
        // block_size of the last block
        if (block_size > buffer->max_length - buffer->allocated) {
            block_size = (int) (buffer->max_length - buffer->allocated);
        }
    }

    // Create the block
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

    // Set variables
    buffer->allocated += block_size;

    ob->dst = PyBytes_AS_STRING(b);
    ob->size = block_size;
    ob->pos = 0;
    return 0;
}


/* Return the current outputted data size. */
static inline Py_ssize_t
_BlocksOutputBuffer_GetDataSize(_BlocksOutputBuffer *buffer, ZSTD_outBuffer *ob)
{
    return buffer->allocated - (ob->size - ob->pos);
}


/* Finish the buffer.
   Return a bytes object on success
   Return NULL on failure
*/
static PyObject *
_BlocksOutputBuffer_Finish(_BlocksOutputBuffer *buffer, ZSTD_outBuffer *ob)
{
    PyObject *result, *block;
    int8_t *offset;

    // Final bytes object
    result = PyBytes_FromStringAndSize(NULL, buffer->allocated - (ob->size - ob->pos));
    if (result == NULL) {
        PyErr_SetString(PyExc_MemoryError, "Unable to allocate output buffer.");
        return NULL;
    }

    // Memory copy
    if (Py_SIZE(buffer->list) > 0) {
        offset = PyBytes_AS_STRING(result);

        // blocks except the last one
        Py_ssize_t i = 0;
        for (; i < Py_SIZE(buffer->list)-1; i++) {
            block = PyList_GET_ITEM(buffer->list, i);
            memcpy(offset,  PyBytes_AS_STRING(block), Py_SIZE(block));
            offset += Py_SIZE(block);
        }
        // the last block
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
_BlocksOutputBuffer_OnError(_BlocksOutputBuffer *buffer)
{
    Py_XDECREF(buffer->list);
}

#define OutputBuffer(F) _BlocksOutputBuffer_##F
/* _BlocksOutputBuffer code end */

/*[clinic input]
module _zstd
[clinic start generated code]*/
/*[clinic end generated code: output=da39a3ee5e6b4b0d input=7ed764541d497cc6]*/

/*[clinic input]
_zstd.compress

    data: Py_buffer
        Binary data to be compressed.

Returns a bytes object containing compressed data.
[clinic start generated code]*/

static PyObject *
_zstd_compress_impl(PyObject *module, Py_buffer *data)
/*[clinic end generated code: output=01007fa703be1682 input=26f155ed6047c8ba]*/
{
    ZSTD_CCtx *cctx = NULL;
    ZSTD_inBuffer in;
    ZSTD_outBuffer out;
    _BlocksOutputBuffer buffer;
    size_t zstd_ret;
    PyObject *ret;

    // prepare input & output buffers
    in.src = data->buf;
    in.size = data->len;
    in.pos = 0;

    if (OutputBuffer(InitAndGrow)(&buffer, -1, &out) < 0) {
        goto error;
    }

    // creat zstd context
    cctx = ZSTD_createCCtx();
    if (cctx == NULL) {
        goto error;
    }

    do {
        /* Zstd optimizes the case where the first flush mode is ZSTD_e_end,
           since it knows it is compressing the entire source in one pass. */
        zstd_ret = ZSTD_compressStream2(cctx, &out, &in, ZSTD_e_end);

        // check error
        if (ZSTD_isError(zstd_ret)) {
            PyErr_SetString(PyExc_Exception, ZSTD_getErrorName(zstd_ret));
            goto error;
        }

        // finished?
        if (zstd_ret == 0) {
            ret = OutputBuffer(Finish)(&buffer, &out);
            if (ret != NULL) {
                goto success;
            } else {
                goto error;
            }
        }

        // output buffer exhausted, grow the buffer
        if (out.pos == out.size) {
            if (OutputBuffer(Grow)(&buffer, &out) < 0) {
                goto error;
            }
        }

        assert(in.pos < in.size);
    } while(1);

error:
    OutputBuffer(OnError)(&buffer);
    ret = NULL;
success:
    if (cctx != NULL) {
        ZSTD_freeCCtx(cctx);
    }
    return ret;
}


/*[clinic input]
_zstd.decompress

    data: Py_buffer
        Compressed data.

Returns a bytes object containing the uncompressed data.
[clinic start generated code]*/

static PyObject *
_zstd_decompress_impl(PyObject *module, Py_buffer *data)
/*[clinic end generated code: output=69aee3f2cf35b025 input=a603d6aa31e2ef0c]*/
{
    ZSTD_DCtx *dctx = NULL;
    ZSTD_inBuffer in;
    ZSTD_outBuffer out;
    _BlocksOutputBuffer buffer;
    size_t zstd_ret;
    PyObject *ret;

    // prepare input & output buffers
    in.src = data->buf;
    in.size = data->len;
    in.pos = 0;

    if (OutputBuffer(InitAndGrow)(&buffer, -1, &out) < 0) {
        goto error;
    }

    // creat zstd context
    dctx = ZSTD_createDCtx();
    if (dctx == NULL) {
        goto error;
    }

    while (in.pos < in.size) {
        zstd_ret = ZSTD_decompressStream(dctx, &out , &in);

        // check error
        if (ZSTD_isError(zstd_ret)) {
            PyErr_SetString(PyExc_Exception, ZSTD_getErrorName(zstd_ret));
            goto error;
        }

        // output buffer exhausted, grow the buffer
        if (out.pos == out.size) {
            if (OutputBuffer(Grow)(&buffer, &out) < 0) {
                goto error;
            }
        }
    }

    // finished
    assert(in.pos == in.size);

    ret = OutputBuffer(Finish)(&buffer, &out);
    if (ret != NULL) {
        goto success;
    }

error:
    OutputBuffer(OnError)(&buffer);
    ret = NULL;
success:
    if (dctx != NULL) {
        ZSTD_freeDCtx(dctx);
    }
    return ret;
}

static PyMethodDef _zstd_methods[] = {
    _ZSTD_COMPRESS_METHODDEF
    _ZSTD_DECOMPRESS_METHODDEF
    {NULL}
};


static PyModuleDef _zstdmodule = {
    PyModuleDef_HEAD_INIT,
    .m_name = "_zstd",
    .m_methods = _zstd_methods,
};

PyMODINIT_FUNC
PyInit__zstd(void)
{
    return PyModuleDef_Init(&_zstdmodule);
}