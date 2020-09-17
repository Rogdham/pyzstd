# Automatically transform from CPython code

import re
import os

PYZSTD_DIR = os.path.dirname(os.path.dirname(__file__))

init_list = (
    (r"(?P<a>__version__\s*=\s*').*?(?P<b>')", r'\g<1>{}\g<2>'),
)

setup_list = (
    (r"(?P<a>version=').*?(?P<b>')", r'\g<1>{}\g<2>'),
)


def copy_and_transform(file, re_list, new_ver):

    path = os.path.join(PYZSTD_DIR, file)

    with open(path, encoding='utf-8') as f:
        text = f.read()

    for pattern, repl in re_list:
        text = re.sub(pattern, repl.format(new_ver), text)
        
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)

input_ver = input('New Version:').strip()

copy_and_transform(os.path.join('src', '__init__.py'), init_list, input_ver)
copy_and_transform('setup.py', setup_list, input_ver)
