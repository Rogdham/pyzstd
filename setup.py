#!/usr/bin/env python
from setuptools import setup, Extension
from os.path import join

# with open('README.rst') as file:
    # long_description = file.read()

zstdFiles = []
for f in [
        'compress/zstd_compress.c',
        'compress/zstd_compress_literals.c',
        'compress/zstd_compress_sequences.c',
        'compress/zstd_compress_superblock.c',
        'compress/zstdmt_compress.c',
        'compress/zstd_fast.c', 'compress/zstd_double_fast.c', 'compress/zstd_lazy.c', 'compress/zstd_opt.c', 'compress/zstd_ldm.c',
        'compress/fse_compress.c', 'compress/huf_compress.c',
        'compress/hist.c',

        'common/fse_decompress.c',
        'decompress/zstd_decompress.c',
        'decompress/zstd_decompress_block.c',
        'decompress/zstd_ddict.c',
        'decompress/huf_decompress.c',

        'common/entropy_common.c', 'common/zstd_common.c', 'common/xxhash.c', 'common/error_private.c',
        'common/pool.c',
        'common/threading.c',
    ]:
    zstdFiles.append('lib/'+f)
zstdFiles.append('src/_zstd.c')

setup(
    name='pyzstd',
    version='2020.7.14',
    description='zstd module',
    #long_description=long_description,
    author='Ma Lin',
    author_email='malincns@163.com',
    url='https://github.com/animalize/pyzstd',
    license='Python Software Foundation License',

    classifiers=[
        'Development Status :: 5 - Production/Stable',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: Python Software Foundation License',
        'Operating System :: OS Independent',
        'Programming Language :: Python :: 3.8',
        'Topic :: Scientific/Engineering :: Information Analysis',
        'Topic :: Software Development :: Libraries :: Python Modules',
        'Topic :: Text Processing',
        'Topic :: Text Processing :: General',
    ],

    package_dir={'pyzstd': 'src'},
    py_modules=['zstd'],
    ext_modules=[
        Extension('_zstd', zstdFiles)
        ],
)
