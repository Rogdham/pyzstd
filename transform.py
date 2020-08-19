# Automatically transform from CPython code

import re
import os
from shutil import copy

CPYTHON_DIR = r'E:\dev\cpython'
PYZSTD_DIR = r'E:\dev\pyzstd'

# Copy pyzstd.py
PY_FILE = r'Lib\zstd.py'
path1 = os.path.join(CPYTHON_DIR, PY_FILE)
path2 = os.path.join(PYZSTD_DIR, 'pyzstd.py')
try:
   copy(path1, path2)
except:
   print("Unable to copy file. %s" % C_FILE)
   raise

# Transform
with open(path2, encoding='utf-8') as f:
    text = f.read()

text = text.replace('from _zstd import *',
                    'from ._zstd import *')
text = text.replace('import _zstd',
                    'from . import _zstd')
       
with open(path2, 'w', encoding='utf-8') as f:
    f.write(text)


# Copy _zstdmodule.c
C_FILE = r'Modules\_zstdmodule.c'
path1 = os.path.join(CPYTHON_DIR, C_FILE)
path2 = os.path.join(PYZSTD_DIR, r'src')
try:
   copy(path1, path2)
except:
   print("Unable to copy file. %s" % C_FILE)
   raise

# Copy _zstdmodule.c.h
H_FILE = r'Modules\clinic\_zstdmodule.c.h'
path1 = os.path.join(CPYTHON_DIR, H_FILE)
path2 = os.path.join(PYZSTD_DIR, r'src\clinic')
try:
   copy(path1, path2)
except:
   print("Unable to copy file. %s" % C_FILE)
   raise

# Transform
path = os.path.join(PYZSTD_DIR, r'src\_zstdmodule.c')
with open(path, encoding='utf-8') as f:
    text = f.read()

text = text.replace('#include "lib\zstd.h"',
                    '#include "..\lib\zstd.h"')
text = text.replace('#include "lib\dictBuilder\zdict.h"',
                    '#include "..\lib\dictBuilder\zdict.h"')

text = re.sub(r'(\n#include "clinic\\_zstdmodule.c.h")',
              r'\n#include "pypi1.h"\1',
              text)

text = text.replace(r'get_zstd_state(PyObject *module)',
                    r'get_zstd_state_NOUSE(PyObject *module)')

init_1 = \
"""PyMODINIT_FUNC
PyInit__zstd(void)
{
    return PyModuleDef_Init(&_zstdmodule);
}"""

init_2 = \
"""#include "pypi2.h"

// PyMODINIT_FUNC
// PyInit__zstd(void)
// {
//     return PyModuleDef_Init(&_zstdmodule);
// }"""

text = text.replace(init_1, init_2)
       
with open(path, 'w', encoding='utf-8') as f:
    f.write(text)

print('ok')