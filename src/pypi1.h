#if PY_VERSION_HEX < 0x03080000
#include "args.h"
#endif

/* _zstd_state */
static _zstd_state static_state;

static inline _zstd_state *
get_zstd_state(PyObject *module)
{
    return &static_state;
}

#define _PyTuple_CAST(op) (assert(PyTuple_Check(op)), (PyTupleObject *)(op))

static inline PyObject *
_PyNumber_Index(PyObject *item)
{
    return PyNumber_Index(item);
}

#if PY_VERSION_HEX < 0x03090000

static inline _zstd_state *
PyType_GetModuleState(void *arg)
{
    return &static_state;
}

static inline int
PyModule_AddType(PyObject *module, PyTypeObject *type)
{
    return 0;
}

static PyObject inline *
PyType_FromModuleAndSpec(PyObject *module, PyType_Spec *spec, PyObject *bases)
{
    return NULL;
}
#endif


