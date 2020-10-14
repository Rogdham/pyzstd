#!/usr/bin/env python
from setuptools import setup, Extension, find_packages
import io
import os

path = os.path.join(os.path.dirname(__file__), 'README.rst')
with io.open(path, 'r', encoding='utf-8') as file:
    long_description = file.read()

zstd_files = [
    'common/fse_decompress.c',
    'common/entropy_common.c',
    'common/zstd_common.c',
    'common/xxhash.c',
    'common/error_private.c',
    'common/pool.c',
    'common/threading.c',

    'compress/zstd_compress.c',
    'compress/zstd_compress_literals.c',
    'compress/zstd_compress_sequences.c',
    'compress/zstd_compress_superblock.c',
    'compress/zstdmt_compress.c',
    'compress/zstd_fast.c',
    'compress/zstd_double_fast.c',
    'compress/zstd_lazy.c',
    'compress/zstd_opt.c',
    'compress/zstd_ldm.c',
    'compress/fse_compress.c',
    'compress/huf_compress.c',
    'compress/hist.c',

    'decompress/zstd_decompress.c',
    'decompress/zstd_decompress_block.c',
    'decompress/zstd_ddict.c',
    'decompress/huf_decompress.c',

    'dictBuilder/zdict.c',
    'dictBuilder/divsufsort.c',
    'dictBuilder/fastcover.c',
    'dictBuilder/cover.c',
    ]

zstdFiles = []
for f in zstd_files:
    zstdFiles.append('lib/'+f)
zstdFiles.append('src/_zstdmodule.c')

setup(
    name='pyzstd',
    version='0.12.5',
    description="Python bindings for Zstandard (zstd) compression algorithm, the interface is similar to Python's bz2/lzma module.",
    long_description=long_description,
    long_description_content_type='text/x-rst',
    author='Ma Lin',
    author_email='malincns@163.com',
    url='https://github.com/animalize/pyzstd',
    license='The 3-Clause BSD License',
    python_requires=">=3.3",

    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: BSD License",
        "Topic :: System :: Archiving :: Compression",
        "Programming Language :: Python :: 3.5",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
    ],
    keywords="zstandard zstd compression decompression compress decompress",

    package_dir={'pyzstd': 'src'},
    py_modules=['pyzstd.__init__', 'pyzstd.pyzstd'],

    packages=["pyzstd"],
    package_data={"pyzstd": ['__init__.pyi', 'py.typed']},

    ext_modules=[Extension('pyzstd._zstd',
                            zstdFiles,
                            extra_compile_args=["-DZSTD_MULTITHREAD"])
                ],
)
