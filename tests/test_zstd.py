from io import BytesIO, UnsupportedOperation
import builtins
import gc
import itertools
import io
import os
import re
import sys
import array
import pathlib
import pickle
import platform
import random
import subprocess
import tempfile
import unittest

import pyzstd
from pyzstd import ZstdCompressor, RichMemZstdCompressor, \
                   ZstdDecompressor, EndlessZstdDecompressor, ZstdError, \
                   CParameter, DParameter, Strategy, \
                   compress, compress_stream, richmem_compress, \
                   decompress, decompress_stream, \
                   ZstdDict, train_dict, finalize_dict, \
                   zstd_version, zstd_version_info, zstd_support_multithread, \
                   compressionLevel_values, get_frame_info, get_frame_size, \
                   ZstdFile, open, __version__ as pyzstd_version

PYZSTD_CONFIG = pyzstd.PYZSTD_CONFIG # type: ignore
if PYZSTD_CONFIG[1] == 'c':
    from pyzstd.c import _zstd       # type: ignore

build_info = ('Pyzstd build information:\n'
              ' - Environment:\n'
              '   * Machine type: {}\n'
              '   * OS: {}\n'
              '   * Python: {} {}, {}-bit build ({})\n'
              ' - Pyzstd:\n'
              '   * Pyzstd version: {}\n'
              '   * Implementation: {}\n'
              '   * Enable multi-phase init: {}\n'
              '   * Link to zstd library: {}\n'
              ' - Zstd:\n'
              '   * Zstd version: {}\n'
              '   * Enable multi-threaded compression: {}\n').format(
                    platform.machine(), # Environment
                    platform.system(),
                    platform.python_implementation(),
                    platform.python_version(),
                    PYZSTD_CONFIG[0],
                    platform.python_compiler(),
                    pyzstd_version,     # Pyzstd
                    PYZSTD_CONFIG[1].upper(),
                    'Not for CFFI implementation' \
                        if PYZSTD_CONFIG[1] == 'cffi' \
                        else PYZSTD_CONFIG[3],
                    'Statically link' if PYZSTD_CONFIG[2] else 'Dynamically link',
                    zstd_version,       # Zstd
                    zstd_support_multithread)
print(build_info, flush=True)

DAT_130K_D = None
DAT_130K_C = None

DECOMPRESSED_DAT = None
COMPRESSED_DAT = None

DECOMPRESSED_100_PLUS_32KB = None
COMPRESSED_100_PLUS_32KB = None

SKIPPABLE_FRAME = None

THIS_FILE_BYTES = None
THIS_FILE_STR = None
COMPRESSED_THIS_FILE = None

COMPRESSED_BOGUS = None

SAMPLES = None

TRAINED_DICT = None

KB = 1024
MB = 1024*1024

def setUpModule():
    # uncompressed size 130KB, more than a zstd block.
    # with a frame epilogue, 4 bytes checksum.
    global DAT_130K_D
    DAT_130K_D = bytes([random.randint(0, 127) for _ in range(130*1024)])

    global DAT_130K_C
    DAT_130K_C = richmem_compress(DAT_130K_D, {CParameter.checksumFlag:1})

    global DECOMPRESSED_DAT
    DECOMPRESSED_DAT = b'abcdefg123456' * 1000

    global COMPRESSED_DAT
    COMPRESSED_DAT = compress(DECOMPRESSED_DAT)

    global DECOMPRESSED_100_PLUS_32KB
    DECOMPRESSED_100_PLUS_32KB = b'a' * (100 + 32*1024)

    global COMPRESSED_100_PLUS_32KB
    COMPRESSED_100_PLUS_32KB = compress(DECOMPRESSED_100_PLUS_32KB)

    global SKIPPABLE_FRAME
    SKIPPABLE_FRAME = (0x184D2A50).to_bytes(4, byteorder='little') + \
                      (32*1024).to_bytes(4, byteorder='little') + \
                      b'a' * (32*1024)

    global THIS_FILE_BYTES, THIS_FILE_STR
    with builtins.open(os.path.abspath(__file__), 'rb') as f:
        THIS_FILE_BYTES = f.read()
        THIS_FILE_BYTES = re.sub(rb'\r?\n', rb'\n', THIS_FILE_BYTES)
        THIS_FILE_STR = THIS_FILE_BYTES.decode('utf-8')

    global COMPRESSED_THIS_FILE
    COMPRESSED_THIS_FILE = compress(THIS_FILE_BYTES)

    global COMPRESSED_BOGUS
    COMPRESSED_BOGUS = DECOMPRESSED_DAT

    # dict data
    words = [b'red', b'green', b'yellow', b'black', b'withe', b'blue',
             b'lilac', b'purple', b'navy', b'glod', b'silver', b'olive',
             b'dog', b'cat', b'tiger', b'lion', b'fish', b'bird']
    lst = []
    for i in range(300):
        sample = [b'%s = %d' % (random.choice(words), random.randrange(100))
                  for j in range(20)]
        sample = b'\n'.join(sample)

        lst.append(sample)
    global SAMPLES
    SAMPLES = lst
    assert len(SAMPLES) > 10

    global TRAINED_DICT
    TRAINED_DICT = train_dict(SAMPLES, 3*1024)
    assert len(TRAINED_DICT.dict_content) <= 3*1024

class FunctionsTestCase(unittest.TestCase):

    def test_version(self):
        s = '.'.join((str(i) for i in zstd_version_info))
        self.assertEqual(s, zstd_version)

    def test_compressionLevel_values(self):
        self.assertEqual(type(compressionLevel_values.default), int)
        self.assertEqual(type(compressionLevel_values.min), int)
        self.assertEqual(type(compressionLevel_values.max), int)
        self.assertLess(compressionLevel_values.min, compressionLevel_values.max)

    def test_compress_decompress(self):
        raw_dat = THIS_FILE_BYTES[:len(THIS_FILE_BYTES)//6]
        default, minv, maxv = compressionLevel_values

        for level in range(max(-20, minv), maxv+1):
            dat1 = compress(raw_dat, level)
            dat2 = decompress(dat1)
            self.assertEqual(dat2, raw_dat)

    def test_get_frame_info(self):
        # no dict
        info = get_frame_info(COMPRESSED_100_PLUS_32KB[:20])
        self.assertEqual(info.decompressed_size, 32*1024+100)
        self.assertEqual(info.dictionary_id, 0)

        # use dict
        dat = compress(b'a'*345, zstd_dict=TRAINED_DICT)
        info = get_frame_info(dat)
        self.assertEqual(info.decompressed_size, 345)
        self.assertEqual(info.dictionary_id, TRAINED_DICT.dict_id)

        with self.assertRaisesRegex(ZstdError,
                                    'not less than the frame header'):
            get_frame_info(b'aaaaaaaaaaaaaa')

    def test_get_frame_size(self):
        size = get_frame_size(COMPRESSED_100_PLUS_32KB)
        self.assertEqual(size, len(COMPRESSED_100_PLUS_32KB))

        with self.assertRaisesRegex(ZstdError,
                                    'not less than this complete frame'):
            get_frame_size(b'aaaaaaaaaaaaaa')

class ClassShapeTestCase(unittest.TestCase):

    def test_ZstdCompressor(self):
        # class attributes
        ZstdCompressor.CONTINUE
        ZstdCompressor.FLUSH_BLOCK
        ZstdCompressor.FLUSH_FRAME

        # method & member
        ZstdCompressor()
        ZstdCompressor(12, TRAINED_DICT)
        c = ZstdCompressor(level_or_option=2, zstd_dict=TRAINED_DICT)

        c.compress(b'123456')
        c.compress(b'123456', ZstdCompressor.CONTINUE)
        c.compress(data=b'123456', mode=c.CONTINUE)

        c.flush()
        c.flush(ZstdCompressor.FLUSH_BLOCK)
        c.flush(mode=c.FLUSH_FRAME)

        c.last_mode

        # decompressor method & member
        with self.assertRaises(AttributeError):
            c.decompress(b'')
        with self.assertRaises(AttributeError):
            c.at_frame_edge
        with self.assertRaises(AttributeError):
            c.eof
        with self.assertRaises(AttributeError):
            c.needs_input

        # read only attribute
        with self.assertRaises(AttributeError):
            c.last_mode = ZstdCompressor.FLUSH_BLOCK

        # name
        self.assertIn('.ZstdCompressor', str(type(c)))

        # doesn't support pickle
        with self.assertRaises(TypeError):
            pickle.dumps(c)

        # supports subclass
        class SubClass(ZstdCompressor):
            pass

    def test_RichMemZstdCompressor(self):
        # class attributes
        with self.assertRaises(AttributeError):
            RichMemZstdCompressor.CONTINUE

        with self.assertRaises(AttributeError):
            RichMemZstdCompressor.FLUSH_BLOCK

        with self.assertRaises(AttributeError):
            RichMemZstdCompressor.FLUSH_FRAME

        # method & member
        RichMemZstdCompressor()
        RichMemZstdCompressor(12, TRAINED_DICT)
        c = RichMemZstdCompressor(level_or_option=4, zstd_dict=TRAINED_DICT)

        c.compress(b'123456')
        c.compress(data=b'123456')

        # ZstdCompressor method & member
        with self.assertRaises(TypeError):
            c.compress(b'123456', ZstdCompressor.FLUSH_FRAME)
        with self.assertRaises(AttributeError):
            c.flush()
        with self.assertRaises(AttributeError):
            c.last_mode

        # decompressor method & member
        with self.assertRaises(AttributeError):
            c.decompress(b'')
        with self.assertRaises(AttributeError):
            c.at_frame_edge
        with self.assertRaises(AttributeError):
            c.eof
        with self.assertRaises(AttributeError):
            c.needs_input

        # name
        self.assertIn('.RichMemZstdCompressor', str(type(c)))

        # doesn't support pickle
        with self.assertRaises(TypeError):
            pickle.dumps(c)

        # supports subclass
        class SubClass(RichMemZstdCompressor):
            pass

    def test_Decompressor(self):
        # method & member
        ZstdDecompressor()
        ZstdDecompressor(TRAINED_DICT, {})
        d = ZstdDecompressor(zstd_dict=TRAINED_DICT, option={})

        d.decompress(b'')
        d.decompress(b'', 100)
        d.decompress(data=b'', max_length = 100)

        d.eof
        d.needs_input
        d.unused_data

        # ZstdCompressor attributes
        with self.assertRaises(AttributeError):
            d.CONTINUE
        with self.assertRaises(AttributeError):
            d.FLUSH_BLOCK
        with self.assertRaises(AttributeError):
            d.FLUSH_FRAME
        with self.assertRaises(AttributeError):
            d.compress(b'')
        with self.assertRaises(AttributeError):
            d.flush()

        # EndlessZstdDecompressor attribute
        with self.assertRaises(AttributeError):
            d.at_frame_edge

        # read only attributes
        with self.assertRaises(AttributeError):
            d.eof = True
        with self.assertRaises(AttributeError):
            d.needs_input = True
        with self.assertRaises(AttributeError):
            d.unused_data = b''

        # name
        self.assertIn('.ZstdDecompressor', str(type(d)))

        # doesn't support pickle
        with self.assertRaises(TypeError):
            pickle.dumps(d)

        # supports subclass
        class SubClass(ZstdDecompressor):
            pass

    def test_EndlessDecompressor(self):
        # method & member
        EndlessZstdDecompressor(TRAINED_DICT, {})
        EndlessZstdDecompressor(zstd_dict=TRAINED_DICT, option={})
        d = EndlessZstdDecompressor()

        d.decompress(b'')
        d.decompress(b'', 100)
        d.decompress(data=b'', max_length = 100)

        d.at_frame_edge
        d.needs_input

        # ZstdCompressor attributes
        with self.assertRaises(AttributeError):
            EndlessZstdDecompressor.CONTINUE
        with self.assertRaises(AttributeError):
            EndlessZstdDecompressor.FLUSH_BLOCK
        with self.assertRaises(AttributeError):
            EndlessZstdDecompressor.FLUSH_FRAME
        with self.assertRaises(AttributeError):
            d.compress(b'')
        with self.assertRaises(AttributeError):
            d.flush()

        # ZstdDecompressor attributes
        with self.assertRaises(AttributeError):
            d.eof
        with self.assertRaises(AttributeError):
            d.unused_data

        # read only attributes
        with self.assertRaises(AttributeError):
            d.needs_input = True

        with self.assertRaises(AttributeError):
            d.at_frame_edge = True

        # name
        self.assertIn('.EndlessZstdDecompressor', str(type(d)))

        # doesn't support pickle
        with self.assertRaises(TypeError):
            pickle.dumps(d)

        # supports subclass
        class SubClass(EndlessZstdDecompressor):
            pass

    def test_ZstdDict(self):
        ZstdDict(b'12345678', True)
        zd = ZstdDict(b'12345678', is_raw=True)

        self.assertEqual(type(zd.dict_content), bytes)
        self.assertEqual(zd.dict_id, 0)
        self.assertEqual(zd.as_digested_dict[1], 0)
        self.assertEqual(zd.as_undigested_dict[1], 1)
        self.assertEqual(zd.as_prefix[1], 2)

        # name
        self.assertIn('.ZstdDict', str(type(zd)))

        # doesn't support pickle
        with self.assertRaisesRegex(TypeError,
                                    r'save \.dict_content attribute'):
            pickle.dumps(zd)
        with self.assertRaisesRegex(TypeError,
                                    r'save \.dict_content attribute'):
            pickle.dumps(zd.as_prefix)

        # supports subclass
        class SubClass(ZstdDict):
            pass

    def test_Strategy(self):
        # class attributes
        Strategy.fast
        Strategy.dfast
        Strategy.greedy
        Strategy.lazy
        Strategy.lazy2
        Strategy.btlazy2
        Strategy.btopt
        Strategy.btultra
        Strategy.btultra2

    def test_CParameter(self):
        CParameter.compressionLevel
        CParameter.windowLog
        CParameter.hashLog
        CParameter.chainLog
        CParameter.searchLog
        CParameter.minMatch
        CParameter.targetLength
        CParameter.strategy

        CParameter.enableLongDistanceMatching
        CParameter.ldmHashLog
        CParameter.ldmMinMatch
        CParameter.ldmBucketSizeLog
        CParameter.ldmHashRateLog

        CParameter.contentSizeFlag
        CParameter.checksumFlag
        CParameter.dictIDFlag

        CParameter.nbWorkers
        CParameter.jobSize
        CParameter.overlapLog

        t = CParameter.windowLog.bounds()
        self.assertEqual(len(t), 2)
        self.assertEqual(type(t[0]), int)
        self.assertEqual(type(t[1]), int)

    def test_DParameter(self):
        DParameter.windowLogMax

        t = DParameter.windowLogMax.bounds()
        self.assertEqual(len(t), 2)
        self.assertEqual(type(t[0]), int)
        self.assertEqual(type(t[1]), int)

    def test_zstderror_pickle(self):
        try:
            decompress(b'invalid data')
        except Exception as e:
            s = pickle.dumps(e)
            obj = pickle.loads(s)
            self.assertEqual(type(obj), ZstdError)
        else:
            self.assertFalse(True, 'unreachable code path')

    def test_pyzstd_config(self):
        self.assertEqual(len(PYZSTD_CONFIG), 4)
        if sys.maxsize > 2**32:
            self.assertEqual(PYZSTD_CONFIG[0], 64)
        else:
            self.assertEqual(PYZSTD_CONFIG[0], 32)
        self.assertIn(PYZSTD_CONFIG[1], ('c', 'cffi'))
        self.assertEqual(type(PYZSTD_CONFIG[2]), bool)
        self.assertEqual(type(PYZSTD_CONFIG[3]), bool)

    def test_ZstdFile_extend(self):
        # These classes and variables can be used to extend ZstdFile,
        # such as SeekableZstdFile(ZstdFile), so pin them down.
        self.assertTrue(issubclass(ZstdFile, io.BufferedIOBase))
        self.assertTrue(issubclass(pyzstd.zstdfile.ZstdDecompressReader,
                                   io.RawIOBase))
        self.assertIs(ZstdFile._READER_CLASS,
                      pyzstd.zstdfile.ZstdDecompressReader)

        # mode
        self.assertEqual(pyzstd.zstdfile._MODE_CLOSED, 0)
        self.assertEqual(pyzstd.zstdfile._MODE_READ, 1)
        self.assertEqual(pyzstd.zstdfile._MODE_WRITE, 2)

        # file object
        bio = BytesIO()
        with ZstdFile(bio, 'r') as f:
            self.assertTrue(hasattr(f, '_fp'))
            self.assertTrue(hasattr(f, '_mode'))
            self.assertTrue(hasattr(f, '_buffer'))
        with ZstdFile(bio, 'w') as f:
            self.assertTrue(hasattr(f, '_fp'))
            self.assertTrue(hasattr(f, '_mode'))
            self.assertTrue(hasattr(f, '_writer'))

        # file
        with tempfile.NamedTemporaryFile(delete=False) as tmp_f:
            PATH = tmp_f.name
        with ZstdFile(PATH, 'r') as f:
            self.assertTrue(hasattr(f, '_fp'))
            self.assertTrue(hasattr(f, '_mode'))
            self.assertTrue(hasattr(f, '_buffer'))
        with ZstdFile(PATH, 'w') as f:
            self.assertTrue(hasattr(f, '_fp'))
            self.assertTrue(hasattr(f, '_mode'))
            self.assertTrue(hasattr(f, '_writer'))
        os.remove(PATH)

class CompressorDecompressorTestCase(unittest.TestCase):

    def test_simple_bad_args(self):
        # ZstdCompressor
        self.assertRaises(TypeError, ZstdCompressor, [])
        self.assertRaises(TypeError, ZstdCompressor, level_or_option=3.14)
        self.assertRaises(TypeError, ZstdCompressor, level_or_option='abc')
        self.assertRaises(TypeError, ZstdCompressor, level_or_option=b'abc')

        self.assertRaises(TypeError, ZstdCompressor, zstd_dict=123)
        self.assertRaises(TypeError, ZstdCompressor, zstd_dict=b'abcd1234')
        self.assertRaises(TypeError, ZstdCompressor, zstd_dict={1:2, 3:4})
        self.assertRaises(TypeError, ZstdCompressor, rich_mem=True)

        with self.assertRaises(ValueError):
            ZstdCompressor(2**31)
        with self.assertRaises(ValueError):
            ZstdCompressor({2**31 : 100})

        with self.assertRaises(ZstdError):
            ZstdCompressor({CParameter.windowLog:100})
        with self.assertRaises(ZstdError):
            ZstdCompressor({3333 : 100})

        # EndlessZstdDecompressor
        self.assertRaises(TypeError, EndlessZstdDecompressor, ())
        self.assertRaises(TypeError, EndlessZstdDecompressor, zstd_dict=123)
        self.assertRaises(TypeError, EndlessZstdDecompressor, zstd_dict=b'abc')
        self.assertRaises(TypeError, EndlessZstdDecompressor, zstd_dict={1:2, 3:4})

        self.assertRaises(TypeError, EndlessZstdDecompressor, option=123)
        self.assertRaises(TypeError, EndlessZstdDecompressor, option='abc')
        self.assertRaises(TypeError, EndlessZstdDecompressor, option=b'abc')
        self.assertRaises(TypeError, EndlessZstdDecompressor, rich_mem=True)

        with self.assertRaises(ValueError):
            EndlessZstdDecompressor(option={2**31 : 100})

        with self.assertRaises(ZstdError):
            EndlessZstdDecompressor(option={DParameter.windowLogMax:100})
        with self.assertRaises(ZstdError):
            EndlessZstdDecompressor(option={3333 : 100})

        # Method bad arguments
        zc = ZstdCompressor()
        self.assertRaises(TypeError, zc.compress)
        self.assertRaises((TypeError, ValueError), zc.compress, b"foo", b"bar")
        self.assertRaises(TypeError, zc.compress, "str")
        self.assertRaises((TypeError, ValueError), zc.flush, b"foo")
        self.assertRaises(TypeError, zc.flush, b"blah", 1)

        self.assertRaises(ValueError, zc.compress, b'', -1)
        self.assertRaises(ValueError, zc.compress, b'', 3)
        self.assertRaises(ValueError, zc.flush, zc.CONTINUE) # 0
        self.assertRaises(ValueError, zc.flush, 3)

        zc.compress(b'')
        zc.compress(b'', zc.CONTINUE)
        zc.compress(b'', zc.FLUSH_BLOCK)
        zc.compress(b'', zc.FLUSH_FRAME)
        empty = zc.flush()
        zc.flush(zc.FLUSH_BLOCK)
        zc.flush(zc.FLUSH_FRAME)

        lzd = EndlessZstdDecompressor()
        self.assertRaises(TypeError, lzd.decompress)
        self.assertRaises(TypeError, lzd.decompress, b"foo", b"bar")
        self.assertRaises(TypeError, lzd.decompress, "str")
        lzd.decompress(empty)

    def test_compress_parameters(self):
        d = {CParameter.compressionLevel : 10,

             CParameter.windowLog : 12,
             CParameter.hashLog : 10,
             CParameter.chainLog : 12,
             CParameter.searchLog : 12,
             CParameter.minMatch : 4,
             CParameter.targetLength : 12,
             CParameter.strategy : Strategy.lazy,

             CParameter.enableLongDistanceMatching : 1,
             CParameter.ldmHashLog : 12,
             CParameter.ldmMinMatch : 11,
             CParameter.ldmBucketSizeLog : 5,
             CParameter.ldmHashRateLog : 12,

             CParameter.contentSizeFlag : 1,
             CParameter.checksumFlag : 1,
             CParameter.dictIDFlag : 0,

             CParameter.nbWorkers : 2 if zstd_support_multithread else 0,
             CParameter.jobSize : 5*MB if zstd_support_multithread else 0,
             CParameter.overlapLog : 9 if zstd_support_multithread else 0,
             }
        ZstdCompressor(level_or_option=d)

        # larger than signed int, ValueError
        d1 = d.copy()
        d1[CParameter.ldmBucketSizeLog] = 2**31
        self.assertRaises(ValueError, ZstdCompressor, d1)

        # clamp compressionLevel
        compress(b'', compressionLevel_values.max+1)
        compress(b'', compressionLevel_values.min-1)

        compress(b'', {CParameter.compressionLevel:compressionLevel_values.max+1})
        compress(b'', {CParameter.compressionLevel:compressionLevel_values.min-1})

        # zstd lib doesn't support MT compression
        if not zstd_support_multithread:
            with self.assertRaises(ZstdError):
                ZstdCompressor({CParameter.nbWorkers:4})
            with self.assertRaises(ZstdError):
                ZstdCompressor({CParameter.jobSize:4})
            with self.assertRaises(ZstdError):
                ZstdCompressor({CParameter.overlapLog:4})

        # out of bounds error msg
        option = {CParameter.windowLog:100}
        with self.assertRaisesRegex(ZstdError,
                (r'Error when setting zstd compression parameter "windowLog", '
                 r'it should \d+ <= value <= \d+, provided value is 100\. '
                 r'\(zstd v\d\.\d\.\d, (?:32|64)-bit build\)')):
            compress(b'', option)

    def test_decompress_parameters(self):
        d = {DParameter.windowLogMax : 15}
        EndlessZstdDecompressor(option=d)

        # larger than signed int, ValueError
        d1 = d.copy()
        d1[DParameter.windowLogMax] = 2**31
        self.assertRaises(ValueError, EndlessZstdDecompressor, None, d1)

        # out of bounds error msg
        option = {DParameter.windowLogMax:100}
        with self.assertRaisesRegex(ZstdError,
                (r'Error when setting zstd decompression parameter "windowLogMax", '
                 r'it should \d+ <= value <= \d+, provided value is 100\. '
                 r'\(zstd v\d\.\d\.\d, (?:32|64)-bit build\)')):
            decompress(b'', option=option)

    def test_unknown_compression_parameter(self):
        KEY = 100001234
        option = {CParameter.compressionLevel: 10,
                  KEY: 200000000}
        pattern = r'Zstd compression parameter.*?"unknown parameter \(key %d\)"' \
                  % KEY
        with self.assertRaisesRegex(ZstdError, pattern):
            ZstdCompressor(option)

    def test_unknown_decompression_parameter(self):
        KEY = 100001234
        option = {DParameter.windowLogMax: DParameter.windowLogMax.bounds()[1],
                  KEY: 200000000}
        pattern = r'Zstd decompression parameter.*?"unknown parameter \(key %d\)"' \
                  % KEY
        with self.assertRaisesRegex(ZstdError, pattern):
            ZstdDecompressor(option=option)

    @unittest.skipIf(not zstd_support_multithread,
                     "zstd build doesn't support multi-threaded compression")
    def test_zstd_multithread_compress(self):
        size = 40*1024*1024
        b = THIS_FILE_BYTES * (size // len(THIS_FILE_BYTES))

        option = {CParameter.compressionLevel : 4,
                  CParameter.nbWorkers : 2}

        # compress()
        dat1 = compress(b, option)
        dat2 = decompress(dat1)
        self.assertEqual(dat2, b)

        # richmem_compress()
        with self.assertWarns(ResourceWarning):
            dat1 = richmem_compress(b, option)
        dat2 = decompress(dat1)
        self.assertEqual(dat2, b)

        # ZstdCompressor
        c = ZstdCompressor(option)
        dat1 = c.compress(b, c.CONTINUE)
        dat2 = c.compress(b, c.FLUSH_BLOCK)
        dat3 = c.compress(b, c.FLUSH_FRAME)
        dat4 = decompress(dat1+dat2+dat3)
        self.assertEqual(dat4, b * 3)

        # ZstdFile
        with ZstdFile(BytesIO(), 'w',
                      level_or_option=option) as f:
            f.write(b)

    def test_rich_mem_compress(self):
        b = THIS_FILE_BYTES[:len(THIS_FILE_BYTES)//3]

        dat1 = richmem_compress(b)
        dat2 = decompress(dat1)
        self.assertEqual(dat2, b)

    @unittest.skipIf(not zstd_support_multithread,
                     "zstd build doesn't support multi-threaded compression")
    def test_rich_mem_compress_warn(self):
        b = THIS_FILE_BYTES[:len(THIS_FILE_BYTES)//3]

        # warning when multi-threading compression
        with self.assertWarns(ResourceWarning):
            dat1 = richmem_compress(b, {CParameter.nbWorkers:2})

        dat2 = decompress(dat1)
        self.assertEqual(dat2, b)

    def test_set_pledged_input_size(self):
        DAT = DECOMPRESSED_100_PLUS_32KB
        CHUNK_SIZE = len(DAT) // 3

        # wrong value
        c = ZstdCompressor()
        with self.assertRaisesRegex(ValueError, r'64-bit unsigned integer'):
            c._set_pledged_input_size(-300)

        # wrong mode
        c = ZstdCompressor(1)
        c.compress(b'123456')
        self.assertEqual(c.last_mode, c.CONTINUE)
        with self.assertRaisesRegex(RuntimeError, r'\.last_mode == \.FLUSH_FRAME'):
            c._set_pledged_input_size(300)

        # None value
        c = ZstdCompressor(1)
        c._set_pledged_input_size(None)
        dat = c.compress(DAT) + c.flush()

        ret = get_frame_info(dat)
        self.assertEqual(ret.decompressed_size, None)

        # correct value
        c = ZstdCompressor(1)
        c._set_pledged_input_size(len(DAT))

        chunks = []
        posi = 0
        while posi < len(DAT):
            dat = c.compress(DAT[posi:posi+CHUNK_SIZE])
            posi += CHUNK_SIZE
            chunks.append(dat)

        dat = c.flush()
        chunks.append(dat)
        chunks = b''.join(chunks)

        ret = get_frame_info(chunks)
        self.assertEqual(ret.decompressed_size, len(DAT))
        self.assertEqual(decompress(chunks), DAT)

        c._set_pledged_input_size(len(DAT)) # the second frame
        dat = c.compress(DAT) + c.flush()

        ret = get_frame_info(dat)
        self.assertEqual(ret.decompressed_size, len(DAT))
        self.assertEqual(decompress(dat), DAT)

        # wrong value
        c = ZstdCompressor(1)
        c._set_pledged_input_size(len(DAT)+1)

        chunks = []
        posi = 0
        while posi < len(DAT):
            dat = c.compress(DAT[posi:posi+CHUNK_SIZE])
            posi += CHUNK_SIZE
            chunks.append(dat)

        with self.assertRaises(ZstdError):
            c.flush()

    def test_decompress_1byte(self):
        d = EndlessZstdDecompressor()

        dat = d.decompress(COMPRESSED_THIS_FILE, 1)
        size = len(dat)

        while True:
            if d.needs_input:
                break
            else:
                dat = d.decompress(b'', 1)

            if not dat:
                break
            size += len(dat)

            if size < len(THIS_FILE_BYTES):
                self.assertFalse(d.at_frame_edge)
            else:
                self.assertTrue(d.at_frame_edge)

        self.assertEqual(size, len(THIS_FILE_BYTES))
        self.assertTrue(d.at_frame_edge)
        self.assertTrue(d.needs_input)

    def test_decompress_2bytes(self):
        d = EndlessZstdDecompressor()

        dat = d.decompress(COMPRESSED_THIS_FILE, 2)
        size = len(dat)

        while True:
            if d.needs_input:
                break
            else:
                dat = d.decompress(b'', 2)

            if not dat:
                break
            size += len(dat)

            if size < len(THIS_FILE_BYTES):
                self.assertFalse(d.at_frame_edge)
            else:
                self.assertTrue(d.at_frame_edge)

        self.assertEqual(size, len(THIS_FILE_BYTES))
        self.assertTrue(d.at_frame_edge)
        self.assertTrue(d.needs_input)

    def test_decompress_3_1bytes(self):
        d = EndlessZstdDecompressor()
        bi = BytesIO(COMPRESSED_THIS_FILE)
        size = 0

        while True:
            if d.needs_input:
                in_dat = bi.read(3)
                if not in_dat:
                    break
            else:
                in_dat = b''

            dat = d.decompress(in_dat, 1)
            size += len(dat)

            if size < len(THIS_FILE_BYTES):
                self.assertFalse(d.at_frame_edge)
            else:
                self.assertTrue(d.at_frame_edge)

        self.assertEqual(size, len(THIS_FILE_BYTES))
        self.assertTrue(d.at_frame_edge)
        self.assertTrue(d.needs_input)

    def test_decompress_3_2bytes(self):
        d = EndlessZstdDecompressor()
        bi = BytesIO(COMPRESSED_THIS_FILE)
        size = 0

        while True:
            if d.needs_input:
                in_dat = bi.read(3)
                if not in_dat:
                    break
            else:
                in_dat = b''

            dat = d.decompress(in_dat, 2)
            size += len(dat)

            if size < len(THIS_FILE_BYTES):
                self.assertFalse(d.at_frame_edge)
            else:
                self.assertTrue(d.at_frame_edge)

        self.assertEqual(size, len(THIS_FILE_BYTES))
        self.assertTrue(d.at_frame_edge)
        self.assertTrue(d.needs_input)

    def test_decompress_1_3bytes(self):
        d = EndlessZstdDecompressor()
        bi = BytesIO(COMPRESSED_THIS_FILE)
        size = 0

        while True:
            if d.needs_input:
                in_dat = bi.read(1)
                if not in_dat:
                    break
            else:
                in_dat = b''

            dat = d.decompress(in_dat, 3)
            size += len(dat)

            if size < len(THIS_FILE_BYTES):
                self.assertFalse(d.at_frame_edge)
            else:
                self.assertTrue(d.at_frame_edge)

        self.assertEqual(size, len(THIS_FILE_BYTES))
        self.assertTrue(d.at_frame_edge)
        self.assertTrue(d.needs_input)

    def test_decompress_epilogue_flags(self):
        # DAT_130K_C has a 4 bytes checksum at frame epilogue
        _130KB = 130 * 1024

        # full unlimited
        d = EndlessZstdDecompressor()
        dat = d.decompress(DAT_130K_C)
        self.assertEqual(len(dat), _130KB)
        self.assertTrue(d.at_frame_edge)
        self.assertTrue(d.needs_input)

        dat = d.decompress(b'')
        self.assertEqual(len(dat), 0)
        self.assertTrue(d.at_frame_edge)
        self.assertTrue(d.needs_input)

        dat = d.decompress(b'')
        self.assertEqual(len(dat), 0)
        self.assertTrue(d.at_frame_edge)
        self.assertTrue(d.needs_input)

        # full limited
        d = EndlessZstdDecompressor()
        dat = d.decompress(DAT_130K_C, _130KB)
        self.assertEqual(len(dat), _130KB)
        self.assertTrue(d.at_frame_edge)
        self.assertTrue(d.needs_input)

        dat = d.decompress(b'', 0)
        self.assertEqual(len(dat), 0)
        self.assertTrue(d.at_frame_edge)
        self.assertTrue(d.needs_input)

        # [:-4] unlimited
        d = EndlessZstdDecompressor()
        dat = d.decompress(DAT_130K_C[:-4])
        self.assertEqual(len(dat), _130KB)
        self.assertFalse(d.at_frame_edge)
        self.assertTrue(d.needs_input)

        dat = d.decompress(b'')
        self.assertEqual(len(dat), 0)
        self.assertFalse(d.at_frame_edge)
        self.assertTrue(d.needs_input)

        # [:-4] limited
        d = EndlessZstdDecompressor()
        dat = d.decompress(DAT_130K_C[:-4], _130KB)
        self.assertEqual(len(dat), _130KB)
        self.assertFalse(d.at_frame_edge)
        self.assertFalse(d.needs_input)

        dat = d.decompress(b'', 0)
        self.assertEqual(len(dat), 0)
        self.assertFalse(d.at_frame_edge)
        self.assertFalse(d.needs_input)

        # [:-3] unlimited
        d = EndlessZstdDecompressor()
        dat = d.decompress(DAT_130K_C[:-3])
        self.assertEqual(len(dat), _130KB)
        self.assertFalse(d.at_frame_edge)
        self.assertTrue(d.needs_input)

        dat = d.decompress(b'')
        self.assertEqual(len(dat), 0)
        self.assertFalse(d.at_frame_edge)
        self.assertTrue(d.needs_input)

        # [:-3] limited
        d = EndlessZstdDecompressor()
        dat = d.decompress(DAT_130K_C[:-3], _130KB)
        self.assertEqual(len(dat), _130KB)
        self.assertFalse(d.at_frame_edge)
        self.assertFalse(d.needs_input)

        dat = d.decompress(b'', 0)
        self.assertEqual(len(dat), 0)
        self.assertFalse(d.at_frame_edge)
        self.assertFalse(d.needs_input)

        # [:-1] unlimited
        d = EndlessZstdDecompressor()
        dat = d.decompress(DAT_130K_C[:-1])
        self.assertEqual(len(dat), _130KB)
        self.assertFalse(d.at_frame_edge)
        self.assertTrue(d.needs_input)

        dat = d.decompress(b'')
        self.assertEqual(len(dat), 0)
        self.assertFalse(d.at_frame_edge)
        self.assertTrue(d.needs_input)

        # [:-1] limited
        d = EndlessZstdDecompressor()
        dat = d.decompress(DAT_130K_C[:-1], _130KB)
        self.assertEqual(len(dat), _130KB)
        self.assertFalse(d.at_frame_edge)
        self.assertFalse(d.needs_input)

        dat = d.decompress(b'', 0)
        self.assertEqual(len(dat), 0)
        self.assertFalse(d.at_frame_edge)
        self.assertFalse(d.needs_input)

    def test_decompress_2x130KB(self):
        decompressed_size = get_frame_info(DAT_130K_C).decompressed_size
        self.assertEqual(decompressed_size, 130 * 1024)

        d = EndlessZstdDecompressor()
        dat = d.decompress(DAT_130K_C + DAT_130K_C)
        self.assertEqual(len(dat), 2 * 130 * 1024)
        self.assertTrue(d.at_frame_edge)
        self.assertTrue(d.needs_input)

    def test_compress_flushblock(self):
        point = len(THIS_FILE_BYTES) // 2

        c = ZstdCompressor()
        self.assertEqual(c.last_mode, c.FLUSH_FRAME)
        dat1 = c.compress(THIS_FILE_BYTES[:point])
        self.assertEqual(c.last_mode, c.CONTINUE)
        dat1 += c.compress(THIS_FILE_BYTES[point:], c.FLUSH_BLOCK)
        self.assertEqual(c.last_mode, c.FLUSH_BLOCK)

        d = EndlessZstdDecompressor()
        dat2 = d.decompress(dat1)

        self.assertEqual(dat2, THIS_FILE_BYTES)
        self.assertFalse(d.at_frame_edge)
        self.assertTrue(d.needs_input)

    def test_compress_flushframe(self):
        # test compress & decompress
        point = len(THIS_FILE_BYTES) // 2

        c = ZstdCompressor()

        dat1 = c.compress(THIS_FILE_BYTES[:point])
        self.assertEqual(c.last_mode, c.CONTINUE)

        dat1 += c.compress(THIS_FILE_BYTES[point:], c.FLUSH_FRAME)
        self.assertEqual(c.last_mode, c.FLUSH_FRAME)

        nt = get_frame_info(dat1)
        self.assertEqual(nt.decompressed_size, None) # no content size

        d = EndlessZstdDecompressor()
        dat2 = d.decompress(dat1)

        self.assertEqual(dat2, THIS_FILE_BYTES)
        self.assertTrue(d.at_frame_edge)
        self.assertTrue(d.needs_input)

        # single .FLUSH_FRAME mode has content size
        c = ZstdCompressor()
        dat = c.compress(THIS_FILE_BYTES, mode=c.FLUSH_FRAME)
        self.assertEqual(c.last_mode, c.FLUSH_FRAME)

        nt = get_frame_info(dat)
        self.assertEqual(nt.decompressed_size, len(THIS_FILE_BYTES))

    def test_decompressor_arg(self):
        zd = ZstdDict(b'12345678', True)

        with self.assertRaises(TypeError):
            d = ZstdDecompressor(zstd_dict={})

        with self.assertRaises(TypeError):
            d = ZstdDecompressor(option=zd)

        ZstdDecompressor()
        ZstdDecompressor(zd, {})
        ZstdDecompressor(zstd_dict=zd, option={DParameter.windowLogMax:25})

    def test_decompressor_1(self):
        _130_KB = 130 * 1024

        # empty
        d = ZstdDecompressor()
        dat = d.decompress(b'')

        self.assertEqual(dat, b'')
        self.assertFalse(d.eof)

        # 130KB full
        d = ZstdDecompressor()
        dat = d.decompress(DAT_130K_C)

        self.assertEqual(len(dat), _130_KB)
        self.assertTrue(d.eof)
        self.assertFalse(d.needs_input)

        # 130KB full, limit output
        d = ZstdDecompressor()
        dat = d.decompress(DAT_130K_C, _130_KB)

        self.assertEqual(len(dat), _130_KB)
        self.assertTrue(d.eof)
        self.assertFalse(d.needs_input)

        # 130KB, without 4 bytes checksum
        d = ZstdDecompressor()
        dat = d.decompress(DAT_130K_C[:-4])

        self.assertEqual(len(dat), _130_KB)
        self.assertFalse(d.eof)
        self.assertTrue(d.needs_input)

        # above, limit output
        d = ZstdDecompressor()
        dat = d.decompress(DAT_130K_C[:-4], _130_KB)

        self.assertEqual(len(dat), _130_KB)
        self.assertFalse(d.eof)
        self.assertFalse(d.needs_input)

        # full, unused_data
        TRAIL = b'89234893abcd'
        d = ZstdDecompressor()
        dat = d.decompress(DAT_130K_C + TRAIL, _130_KB)

        self.assertEqual(len(dat), _130_KB)
        self.assertTrue(d.eof)
        self.assertFalse(d.needs_input)
        self.assertEqual(d.unused_data, TRAIL)

    def test_decompressor_chunks_read_300(self):
        _130_KB = 130 * 1024
        TRAIL = b'89234893abcd'
        DAT = DAT_130K_C + TRAIL
        d = ZstdDecompressor()

        bi = BytesIO(DAT)
        lst = []
        while True:
            if d.needs_input:
                dat = bi.read(300)
                if not dat:
                    break
            else:
                raise Exception('should not get here')

            ret = d.decompress(dat)
            lst.append(ret)
            if d.eof:
                break

        ret = b''.join(lst)

        self.assertEqual(len(ret), _130_KB)
        self.assertTrue(d.eof)
        self.assertFalse(d.needs_input)
        self.assertEqual(d.unused_data + bi.read(), TRAIL)

    def test_decompressor_chunks_read_3(self):
        _130_KB = 130 * 1024
        TRAIL = b'89234893'
        DAT = DAT_130K_C + TRAIL
        d = ZstdDecompressor()

        bi = BytesIO(DAT)
        lst = []
        while True:
            if d.needs_input:
                dat = bi.read(3)
                if not dat:
                    break
            else:
                dat = b''

            ret = d.decompress(dat, 1)
            lst.append(ret)
            if d.eof:
                break

        ret = b''.join(lst)

        self.assertEqual(len(ret), _130_KB)
        self.assertTrue(d.eof)
        self.assertFalse(d.needs_input)
        self.assertEqual(d.unused_data + bi.read(), TRAIL)

    def test_compress_empty(self):
        # output empty content frame
        self.assertNotEqual(compress(b''), b'')
        self.assertNotEqual(richmem_compress(b''), b'')

        c = ZstdCompressor()
        self.assertNotEqual(c.compress(b'', c.FLUSH_FRAME), b'')

        c = RichMemZstdCompressor()
        self.assertNotEqual(c.compress(b''), b'')

        # output b''
        bi = BytesIO(b'')
        bo = BytesIO()
        ret = compress_stream(bi, bo)
        self.assertEqual(ret, (0, 0))
        self.assertEqual(bo.getvalue(), b'')
        bi.close()
        bo.close()

    def test_decompress_empty(self):
        self.assertEqual(decompress(b''), b'')

        d = ZstdDecompressor()
        self.assertEqual(d.decompress(b''), b'')
        self.assertFalse(d.eof)

        d = EndlessZstdDecompressor()
        self.assertEqual(d.decompress(b''), b'')
        self.assertTrue(d.at_frame_edge)

        bi = BytesIO(b'')
        bo = BytesIO()
        ret = decompress_stream(bi, bo)
        self.assertEqual(ret, (0, 0))
        self.assertEqual(bo.getvalue(), b'')
        bi.close()
        bo.close()

    def test_parameter_bounds_cache(self):
        a = CParameter.compressionLevel.bounds()
        b = CParameter.compressionLevel.bounds()
        self.assertIs(a, b)

        a = CParameter.windowLog.bounds()
        b = CParameter.windowLog.bounds()
        self.assertIs(a, b)

        a = DParameter.windowLogMax.bounds()
        b = DParameter.windowLogMax.bounds()
        self.assertIs(a, b)

class DecompressorFlagsTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        option = {CParameter.checksumFlag:1}
        c = ZstdCompressor(option)

        cls.DECOMPRESSED_42 = b'a'*42
        cls.FRAME_42 = c.compress(cls.DECOMPRESSED_42, c.FLUSH_FRAME)

        cls.DECOMPRESSED_60 = b'a'*60
        cls.FRAME_60 = c.compress(cls.DECOMPRESSED_60, c.FLUSH_FRAME)

        cls.FRAME_42_60 = cls.FRAME_42 + cls.FRAME_60
        cls.DECOMPRESSED_42_60 = cls.DECOMPRESSED_42 + cls.DECOMPRESSED_60

        cls._130KB = 130*1024

        c = ZstdCompressor()
        cls.UNKNOWN_FRAME_42 = c.compress(cls.DECOMPRESSED_42) + c.flush()
        cls.UNKNOWN_FRAME_60 = c.compress(cls.DECOMPRESSED_60) + c.flush()
        cls.UNKNOWN_FRAME_42_60 = cls.UNKNOWN_FRAME_42 + cls.UNKNOWN_FRAME_60

        cls.TRAIL = b'12345678abcdefg!@#$%^&*()_+|'

    def test_function_decompress(self):
        self.assertEqual(decompress(b''), b'')

        self.assertEqual(len(decompress(COMPRESSED_100_PLUS_32KB)), 100+32*1024)

        # 1 frame
        self.assertEqual(decompress(self.FRAME_42), self.DECOMPRESSED_42)

        self.assertEqual(decompress(self.UNKNOWN_FRAME_42), self.DECOMPRESSED_42)

        with self.assertRaisesRegex(ZstdError, "incomplete frame"):
            decompress(self.FRAME_42[:1])

        with self.assertRaisesRegex(ZstdError, "incomplete frame"):
            decompress(self.FRAME_42[:-4])

        with self.assertRaisesRegex(ZstdError, "incomplete frame"):
            decompress(self.FRAME_42[:-1])

        # 2 frames
        self.assertEqual(decompress(self.FRAME_42_60), self.DECOMPRESSED_42_60)

        self.assertEqual(decompress(self.UNKNOWN_FRAME_42_60), self.DECOMPRESSED_42_60)

        self.assertEqual(decompress(self.FRAME_42 + self.UNKNOWN_FRAME_60),
                         self.DECOMPRESSED_42_60)

        self.assertEqual(decompress(self.UNKNOWN_FRAME_42 + self.FRAME_60),
                         self.DECOMPRESSED_42_60)

        with self.assertRaisesRegex(ZstdError, "incomplete frame"):
            decompress(self.FRAME_42_60[:-4])

        with self.assertRaisesRegex(ZstdError, "incomplete frame"):
            decompress(self.UNKNOWN_FRAME_42_60[:-1])

        # 130KB
        self.assertEqual(decompress(DAT_130K_C), DAT_130K_D)

        with self.assertRaisesRegex(ZstdError, "incomplete frame"):
            decompress(DAT_130K_C[:-4])

        with self.assertRaisesRegex(ZstdError, "incomplete frame"):
            decompress(DAT_130K_C[:-1])

        # Unknown frame descriptor
        with self.assertRaisesRegex(ZstdError, "Unknown frame descriptor"):
            decompress(b'aaaaaaaaa')

        with self.assertRaisesRegex(ZstdError, "Unknown frame descriptor"):
            decompress(self.FRAME_42 + b'aaaaaaaaa')

        with self.assertRaisesRegex(ZstdError, "Unknown frame descriptor"):
            decompress(self.UNKNOWN_FRAME_42_60 + b'aaaaaaaaa')

        # doesn't match checksum
        checksum = DAT_130K_C[-4:]
        if checksum[0] == 255:
            wrong_checksum = bytes([254]) + checksum[1:]
        else:
            wrong_checksum = bytes([checksum[0]+1]) + checksum[1:]

        dat = DAT_130K_C[:-4] + wrong_checksum

        with self.assertRaisesRegex(ZstdError, "doesn't match checksum"):
            decompress(dat)

    def test_function_skippable(self):
        self.assertEqual(decompress(SKIPPABLE_FRAME), b'')
        self.assertEqual(decompress(SKIPPABLE_FRAME + SKIPPABLE_FRAME), b'')

        # 1 frame + 2 skippable
        self.assertEqual(len(decompress(SKIPPABLE_FRAME + SKIPPABLE_FRAME + DAT_130K_C)),
                         self._130KB)

        self.assertEqual(len(decompress(DAT_130K_C + SKIPPABLE_FRAME + SKIPPABLE_FRAME)),
                         self._130KB)

        self.assertEqual(len(decompress(SKIPPABLE_FRAME + DAT_130K_C + SKIPPABLE_FRAME)),
                         self._130KB)

        # unknown size
        self.assertEqual(decompress(SKIPPABLE_FRAME + self.UNKNOWN_FRAME_60),
                         self.DECOMPRESSED_60)

        self.assertEqual(decompress(self.UNKNOWN_FRAME_60 + SKIPPABLE_FRAME),
                         self.DECOMPRESSED_60)

        # 2 frames + 1 skippable
        self.assertEqual(decompress(self.FRAME_42 + SKIPPABLE_FRAME + self.FRAME_60),
                         self.DECOMPRESSED_42_60)

        self.assertEqual(decompress(SKIPPABLE_FRAME + self.FRAME_42_60),
                         self.DECOMPRESSED_42_60)

        self.assertEqual(decompress(self.UNKNOWN_FRAME_42_60 + SKIPPABLE_FRAME),
                         self.DECOMPRESSED_42_60)

        # incomplete
        with self.assertRaises(ZstdError):
            decompress(SKIPPABLE_FRAME[:1])

        with self.assertRaises(ZstdError):
            decompress(SKIPPABLE_FRAME[:-1])

        with self.assertRaises(ZstdError):
            decompress(SKIPPABLE_FRAME[:-1] + self.FRAME_60)

        with self.assertRaises(ZstdError):
            decompress(self.FRAME_42 + SKIPPABLE_FRAME[:-1])

        # Unknown frame descriptor
        with self.assertRaisesRegex(ZstdError, "Unknown frame descriptor"):
            decompress(b'aaaaaaaaa' + SKIPPABLE_FRAME)

        with self.assertRaisesRegex(ZstdError, "Unknown frame descriptor"):
            decompress(SKIPPABLE_FRAME + b'aaaaaaaaa')

        with self.assertRaisesRegex(ZstdError, "Unknown frame descriptor"):
            decompress(SKIPPABLE_FRAME + SKIPPABLE_FRAME + b'aaaaaaaaa')

    def test_decompressor_1(self):
        # empty 1
        d = ZstdDecompressor()

        dat = d.decompress(b'')
        self.assertEqual(dat, b'')
        self.assertFalse(d.eof)
        self.assertTrue(d.needs_input)
        self.assertEqual(d.unused_data, b'')
        self.assertEqual(d.unused_data, b'') # twice

        dat = d.decompress(b'', 0)
        self.assertEqual(dat, b'')
        self.assertFalse(d.eof)
        self.assertFalse(d.needs_input)
        self.assertEqual(d.unused_data, b'')
        self.assertEqual(d.unused_data, b'') # twice

        dat = d.decompress(COMPRESSED_100_PLUS_32KB + b'a')
        self.assertEqual(dat, DECOMPRESSED_100_PLUS_32KB)
        self.assertTrue(d.eof)
        self.assertFalse(d.needs_input)
        self.assertEqual(d.unused_data, b'a')
        self.assertEqual(d.unused_data, b'a') # twice

        # empty 2
        d = ZstdDecompressor()

        dat = d.decompress(b'', 0)
        self.assertEqual(dat, b'')
        self.assertFalse(d.eof)
        self.assertFalse(d.needs_input)
        self.assertEqual(d.unused_data, b'')
        self.assertEqual(d.unused_data, b'') # twice

        dat = d.decompress(b'')
        self.assertEqual(dat, b'')
        self.assertFalse(d.eof)
        self.assertTrue(d.needs_input)
        self.assertEqual(d.unused_data, b'')
        self.assertEqual(d.unused_data, b'') # twice

        dat = d.decompress(COMPRESSED_100_PLUS_32KB + b'a')
        self.assertEqual(dat, DECOMPRESSED_100_PLUS_32KB)
        self.assertTrue(d.eof)
        self.assertFalse(d.needs_input)
        self.assertEqual(d.unused_data, b'a')
        self.assertEqual(d.unused_data, b'a') # twice

        # 1 frame
        d = ZstdDecompressor()
        dat = d.decompress(self.FRAME_42)

        self.assertEqual(dat, self.DECOMPRESSED_42)
        self.assertTrue(d.eof)
        self.assertFalse(d.needs_input)
        self.assertEqual(d.unused_data, b'')
        self.assertEqual(d.unused_data, b'') # twice

        with self.assertRaises(EOFError):
            d.decompress(b'')

        # 1 frame, trail
        d = ZstdDecompressor()
        dat = d.decompress(self.FRAME_42 + self.TRAIL)

        self.assertEqual(dat, self.DECOMPRESSED_42)
        self.assertTrue(d.eof)
        self.assertFalse(d.needs_input)
        self.assertEqual(d.unused_data, self.TRAIL)
        self.assertEqual(d.unused_data, self.TRAIL) # twice

        # 1 frame, 32KB
        temp = compress(b'a'*(32*1024))
        d = ZstdDecompressor()
        dat = d.decompress(temp, 32*1024)

        self.assertEqual(dat, b'a'*(32*1024))
        self.assertTrue(d.eof)
        self.assertFalse(d.needs_input)
        self.assertEqual(d.unused_data, b'')
        self.assertEqual(d.unused_data, b'') # twice

        with self.assertRaises(EOFError):
            d.decompress(b'')

        # 1 frame, 32KB+100, trail
        d = ZstdDecompressor()
        dat = d.decompress(COMPRESSED_100_PLUS_32KB+self.TRAIL, 100) # 100 bytes

        self.assertEqual(len(dat), 100)
        self.assertFalse(d.eof)
        self.assertFalse(d.needs_input)
        self.assertEqual(d.unused_data, b'')

        dat = d.decompress(b'') # 32KB

        self.assertEqual(len(dat), 32*1024)
        self.assertTrue(d.eof)
        self.assertFalse(d.needs_input)
        self.assertEqual(d.unused_data, self.TRAIL)
        self.assertEqual(d.unused_data, self.TRAIL) # twice

        with self.assertRaises(EOFError):
            d.decompress(b'')

        # incomplete 1
        d = ZstdDecompressor()
        dat = d.decompress(self.FRAME_60[:1])

        self.assertFalse(d.eof)
        self.assertTrue(d.needs_input)
        self.assertEqual(d.unused_data, b'')
        self.assertEqual(d.unused_data, b'') # twice

        # incomplete 2
        d = ZstdDecompressor()

        dat = d.decompress(self.FRAME_60[:-4])
        self.assertEqual(dat, self.DECOMPRESSED_60)
        self.assertFalse(d.eof)
        self.assertTrue(d.needs_input)
        self.assertEqual(d.unused_data, b'')
        self.assertEqual(d.unused_data, b'') # twice

        # incomplete 3
        d = ZstdDecompressor()

        dat = d.decompress(self.FRAME_60[:-1])
        self.assertEqual(dat, self.DECOMPRESSED_60)
        self.assertFalse(d.eof)
        self.assertTrue(d.needs_input)
        self.assertEqual(d.unused_data, b'')

        # incomplete 4
        d = ZstdDecompressor()

        dat = d.decompress(self.FRAME_60[:-4], 60)
        self.assertEqual(dat, self.DECOMPRESSED_60)
        self.assertFalse(d.eof)
        self.assertFalse(d.needs_input)
        self.assertEqual(d.unused_data, b'')
        self.assertEqual(d.unused_data, b'') # twice

        dat = d.decompress(b'')
        self.assertEqual(dat, b'')
        self.assertFalse(d.eof)
        self.assertTrue(d.needs_input)
        self.assertEqual(d.unused_data, b'')
        self.assertEqual(d.unused_data, b'') # twice

        # Unknown frame descriptor
        d = ZstdDecompressor()
        with self.assertRaisesRegex(ZstdError, "Unknown frame descriptor"):
            d.decompress(b'aaaaaaaaa')

    def test_decompressor_skippable(self):
        # 1 skippable
        d = ZstdDecompressor()
        dat = d.decompress(SKIPPABLE_FRAME)

        self.assertEqual(dat, b'')
        self.assertTrue(d.eof)
        self.assertFalse(d.needs_input)
        self.assertEqual(d.unused_data, b'')
        self.assertEqual(d.unused_data, b'') # twice

        # 1 skippable, max_length=0
        d = ZstdDecompressor()
        dat = d.decompress(SKIPPABLE_FRAME, 0)

        self.assertEqual(dat, b'')
        self.assertTrue(d.eof)
        self.assertFalse(d.needs_input)
        self.assertEqual(d.unused_data, b'')
        self.assertEqual(d.unused_data, b'') # twice

        # 1 skippable, trail
        d = ZstdDecompressor()
        dat = d.decompress(SKIPPABLE_FRAME + self.TRAIL)

        self.assertEqual(dat, b'')
        self.assertTrue(d.eof)
        self.assertFalse(d.needs_input)
        self.assertEqual(d.unused_data, self.TRAIL)
        self.assertEqual(d.unused_data, self.TRAIL) # twice

        # incomplete
        d = ZstdDecompressor()
        dat = d.decompress(SKIPPABLE_FRAME[:-1])

        self.assertEqual(dat, b'')
        self.assertFalse(d.eof)
        self.assertTrue(d.needs_input)
        self.assertEqual(d.unused_data, b'')
        self.assertEqual(d.unused_data, b'') # twice

        # incomplete
        d = ZstdDecompressor()
        dat = d.decompress(SKIPPABLE_FRAME[:-1], 0)

        self.assertEqual(dat, b'')
        self.assertFalse(d.eof)
        self.assertFalse(d.needs_input)
        self.assertEqual(d.unused_data, b'')
        self.assertEqual(d.unused_data, b'') # twice

        dat = d.decompress(b'')

        self.assertEqual(dat, b'')
        self.assertFalse(d.eof)
        self.assertTrue(d.needs_input)
        self.assertEqual(d.unused_data, b'')
        self.assertEqual(d.unused_data, b'') # twice

    def test_endless_1(self):
        # empty
        d = EndlessZstdDecompressor()
        dat = d.decompress(b'')

        self.assertEqual(dat, b'')
        self.assertTrue(d.at_frame_edge)
        self.assertTrue(d.needs_input)

        dat = d.decompress(b'', 0)

        self.assertEqual(dat, b'')
        self.assertTrue(d.at_frame_edge)
        self.assertTrue(d.needs_input)

        # 1 frame, a
        d = EndlessZstdDecompressor()
        dat = d.decompress(self.FRAME_42)

        self.assertEqual(dat, self.DECOMPRESSED_42)
        self.assertTrue(d.at_frame_edge)
        self.assertTrue(d.needs_input)

        dat = d.decompress(self.FRAME_60, 60)

        self.assertEqual(dat, self.DECOMPRESSED_60)
        self.assertTrue(d.at_frame_edge)
        self.assertTrue(d.needs_input)

        # 1 frame, b
        d = EndlessZstdDecompressor()
        dat = d.decompress(self.FRAME_42, 21)

        self.assertNotEqual(dat, self.DECOMPRESSED_42)
        self.assertFalse(d.at_frame_edge)
        self.assertFalse(d.needs_input)

        dat += d.decompress(self.FRAME_60, 21)

        self.assertEqual(dat, self.DECOMPRESSED_42)
        self.assertFalse(d.at_frame_edge)
        self.assertFalse(d.needs_input)

        dat = d.decompress(b'', 60)

        self.assertEqual(dat, self.DECOMPRESSED_60)
        self.assertTrue(d.at_frame_edge)
        self.assertTrue(d.needs_input)

        # 1 frame, trail
        d = EndlessZstdDecompressor()
        dat = None
        with self.assertRaises(ZstdError):
            d.decompress(self.FRAME_42 + self.TRAIL)

        self.assertTrue(d.at_frame_edge) # resetted
        self.assertTrue(d.needs_input)   # resetted

        # 2 frames, a
        d = EndlessZstdDecompressor()
        dat = d.decompress(self.FRAME_42_60)

        self.assertEqual(dat, self.DECOMPRESSED_42+self.DECOMPRESSED_60)
        self.assertTrue(d.at_frame_edge)
        self.assertTrue(d.needs_input)

        dat = d.decompress(b'')

        self.assertEqual(dat, b'')
        self.assertTrue(d.at_frame_edge)
        self.assertTrue(d.needs_input)

        dat = d.decompress(b'')

        self.assertEqual(dat, b'')
        self.assertTrue(d.at_frame_edge)
        self.assertTrue(d.needs_input)

        # 2 frame2, b
        d = EndlessZstdDecompressor()
        dat = d.decompress(self.FRAME_42_60, 42)

        self.assertEqual(dat, self.DECOMPRESSED_42)
        self.assertFalse(d.at_frame_edge)
        self.assertFalse(d.needs_input)

        dat = d.decompress(b'')

        self.assertEqual(dat, self.DECOMPRESSED_60)
        self.assertTrue(d.at_frame_edge)
        self.assertTrue(d.needs_input)

        dat = d.decompress(b'')

        self.assertEqual(dat, b'')
        self.assertTrue(d.at_frame_edge)
        self.assertTrue(d.needs_input)

        # incomplete
        d = EndlessZstdDecompressor()
        dat = d.decompress(self.FRAME_42_60[:-2])

        self.assertEqual(dat, self.DECOMPRESSED_42 + self.DECOMPRESSED_60)
        self.assertFalse(d.at_frame_edge)
        self.assertTrue(d.needs_input)

        dat = d.decompress(b'')

        self.assertEqual(dat, b'')
        self.assertFalse(d.at_frame_edge)
        self.assertTrue(d.needs_input)

    def test_endlessdecompressor_skippable(self):
        # 1 skippable
        d = EndlessZstdDecompressor()
        dat = d.decompress(SKIPPABLE_FRAME)

        self.assertEqual(dat, b'')
        self.assertTrue(d.at_frame_edge)
        self.assertTrue(d.needs_input)

        # 1 skippable, max_length=0
        d = EndlessZstdDecompressor()
        dat = d.decompress(SKIPPABLE_FRAME, 0)

        self.assertEqual(dat, b'')
        self.assertTrue(d.at_frame_edge)
        self.assertTrue(d.needs_input)

        # 1 skippable, trail
        d = EndlessZstdDecompressor()
        with self.assertRaises(ZstdError):
            d.decompress(SKIPPABLE_FRAME + self.TRAIL)

        self.assertEqual(dat, b'')
        self.assertTrue(d.at_frame_edge)
        self.assertTrue(d.needs_input)

         # incomplete
        d = EndlessZstdDecompressor()
        dat = d.decompress(SKIPPABLE_FRAME[:-1], 0)

        self.assertEqual(dat, b'')
        self.assertFalse(d.at_frame_edge)
        self.assertFalse(d.needs_input)

        dat = d.decompress(b'')

        self.assertEqual(dat, b'')
        self.assertFalse(d.at_frame_edge)
        self.assertTrue(d.needs_input)

       # incomplete
        d = EndlessZstdDecompressor()
        dat = d.decompress(SKIPPABLE_FRAME + SKIPPABLE_FRAME[:-1])

        self.assertEqual(dat, b'')
        self.assertFalse(d.at_frame_edge)
        self.assertTrue(d.needs_input)

        dat = d.decompress(b'')
        self.assertEqual(dat, b'')
        self.assertFalse(d.at_frame_edge)
        self.assertTrue(d.needs_input)

    def test_EndlessZstdDecompressor_PEP489(self):
        class D(EndlessZstdDecompressor):
            def decompress(self, data):
                return super().decompress(data)

        d = D()
        self.assertEqual(d.decompress(self.FRAME_42_60), self.DECOMPRESSED_42_60)
        self.assertEqual(d.decompress(b''), b'')
        self.assertTrue(d.at_frame_edge)
        with self.assertRaises(ZstdError):
            d.decompress(b'123456789')

    def test_reset_session(self):
        D_DAT = SAMPLES[0]
        C_DAT = compress(D_DAT, zstd_dict=TRAINED_DICT)
        C_2DAT = C_DAT * 2
        TAIL = b'1234'

        # ZstdDecompressor
        d = ZstdDecompressor(zstd_dict=TRAINED_DICT)
        # part data
        dat = d.decompress(C_DAT+TAIL, 10)
        self.assertEqual(dat, D_DAT[:10])
        self.assertFalse(d.eof)
        self.assertFalse(d.needs_input)
        self.assertEqual(d.unused_data, b'')

        # reset
        self.assertIsNone(d._reset_session())
        self.assertFalse(d.eof)
        self.assertTrue(d.needs_input)
        self.assertEqual(d.unused_data, b'')

        # full
        self.assertEqual(d.decompress(C_DAT+TAIL), D_DAT)
        self.assertTrue(d.eof)
        self.assertFalse(d.needs_input)
        self.assertEqual(d.unused_data, TAIL)

        # reset
        self.assertIsNone(d._reset_session())
        self.assertFalse(d.eof)
        self.assertTrue(d.needs_input)
        self.assertEqual(d.unused_data, b'')

        # full
        self.assertEqual(d.decompress(C_2DAT), D_DAT)
        self.assertTrue(d.eof)
        self.assertFalse(d.needs_input)
        self.assertEqual(d.unused_data, C_DAT)

        # EndlessZstdDecompressor
        d = EndlessZstdDecompressor(zstd_dict=TRAINED_DICT)
        dat = d.decompress(C_2DAT, 10)
        self.assertEqual(dat, D_DAT[:10])
        self.assertFalse(d.at_frame_edge)
        self.assertFalse(d.needs_input)

        self.assertIsNone(d._reset_session()) # reset
        self.assertTrue(d.at_frame_edge)
        self.assertTrue(d.needs_input)
        self.assertEqual(d.decompress(C_2DAT), D_DAT*2)

class ZstdDictTestCase(unittest.TestCase):

    def test_is_raw(self):
        # content < 8
        b = b'1234567'
        with self.assertRaises(ValueError):
            ZstdDict(b)

        # content == 8
        b = b'12345678'
        zd = ZstdDict(b, is_raw=True)
        self.assertEqual(zd.dict_id, 0)

        temp = compress(b'aaa12345678', 3, zd)
        self.assertEqual(b'aaa12345678', decompress(temp, zd))

        # is_raw == False
        b = b'12345678abcd'
        with self.assertRaises(ValueError):
            ZstdDict(b)

        # read only attributes
        with self.assertRaises(AttributeError):
            zd.dict_content = b

        with self.assertRaises(AttributeError):
            zd.dict_id = 10000

        # ZstdDict arguments
        zd = ZstdDict(TRAINED_DICT.dict_content, is_raw=False)
        self.assertNotEqual(zd.dict_id, 0)

        zd = ZstdDict(TRAINED_DICT.dict_content, is_raw=True)
        self.assertNotEqual(zd.dict_id, 0) # note this assertion

        with self.assertRaises(TypeError):
            ZstdDict("12345678abcdef", is_raw=True)
        with self.assertRaises(TypeError):
            ZstdDict(TRAINED_DICT)

        # invalid parameter
        with self.assertRaises(TypeError):
            ZstdDict(desk333=345)

    def test_invalid_dict(self):
        DICT_MAGIC = 0xEC30A437.to_bytes(4, byteorder='little')
        dict_content = DICT_MAGIC + b'abcdefghighlmnopqrstuvwxyz'

        # corrupted
        zd = ZstdDict(dict_content, is_raw=False)
        with self.assertRaisesRegex(ZstdError, r'ZSTD_CDict.*?corrupted'):
            ZstdCompressor(zstd_dict=zd.as_digested_dict)
        with self.assertRaisesRegex(ZstdError, r'ZSTD_DDict.*?corrupted'):
            ZstdDecompressor(zd)

        # wrong type
        with self.assertRaisesRegex(TypeError, r'should be ZstdDict object'):
            ZstdCompressor(zstd_dict=(zd, b'123'))
        with self.assertRaisesRegex(TypeError, r'should be ZstdDict object'):
            ZstdCompressor(zstd_dict=(zd, 1, 2))
        with self.assertRaisesRegex(TypeError, r'should be ZstdDict object'):
            ZstdCompressor(zstd_dict=(zd, -1))
        with self.assertRaisesRegex(TypeError, r'should be ZstdDict object'):
            ZstdCompressor(zstd_dict=(zd, 3))

        with self.assertRaisesRegex(TypeError, r'should be ZstdDict object'):
            ZstdDecompressor(zstd_dict=(zd, b'123'))
        with self.assertRaisesRegex(TypeError, r'should be ZstdDict object'):
            ZstdDecompressor((zd, 1, 2))
        with self.assertRaisesRegex(TypeError, r'should be ZstdDict object'):
            ZstdDecompressor((zd, -1))
        with self.assertRaisesRegex(TypeError, r'should be ZstdDict object'):
            ZstdDecompressor((zd, 3))

    def test_train_dict(self):
        DICT_SIZE1 = 3*1024

        global TRAINED_DICT
        TRAINED_DICT = pyzstd.train_dict(SAMPLES, DICT_SIZE1)
        ZstdDict(TRAINED_DICT.dict_content, False)

        self.assertNotEqual(TRAINED_DICT.dict_id, 0)
        self.assertGreater(len(TRAINED_DICT.dict_content), 0)
        self.assertLessEqual(len(TRAINED_DICT.dict_content), DICT_SIZE1)
        self.assertTrue(re.match(r'^<ZstdDict dict_id=\d+ dict_size=\d+>$', str(TRAINED_DICT)))

        # compress/decompress
        c = ZstdCompressor(zstd_dict=TRAINED_DICT)
        for sample in SAMPLES:
            dat1 = compress(sample, zstd_dict=TRAINED_DICT)
            dat2 = decompress(dat1, TRAINED_DICT)
            self.assertEqual(sample, dat2)

            dat1 = c.compress(sample)
            dat1 += c.flush()
            dat2 = decompress(dat1, TRAINED_DICT)
            self.assertEqual(sample, dat2)

    def test_finalize_dict(self):
        if zstd_version_info < (1, 4, 5):
            return

        DICT_SIZE2 = 200*1024
        C_LEVEL = 6

        try:
            dic2 = finalize_dict(TRAINED_DICT, SAMPLES, DICT_SIZE2, C_LEVEL)
        except NotImplementedError:
            # < v1.4.5 at compile-time, >= v.1.4.5 at run-time
            return

        self.assertNotEqual(dic2.dict_id, 0)
        self.assertGreater(len(dic2.dict_content), 0)
        self.assertLessEqual(len(dic2.dict_content), DICT_SIZE2)

        # compress/decompress
        c = ZstdCompressor(C_LEVEL, dic2)
        for sample in SAMPLES:
            dat1 = compress(sample, C_LEVEL, dic2)
            dat2 = decompress(dat1, dic2)
            self.assertEqual(sample, dat2)

            dat1 = c.compress(sample)
            dat1 += c.flush()
            dat2 = decompress(dat1, dic2)
            self.assertEqual(sample, dat2)

        # dict mismatch
        self.assertNotEqual(TRAINED_DICT.dict_id, dic2.dict_id)

        dat1 = compress(SAMPLES[0], zstd_dict=TRAINED_DICT)
        with self.assertRaises(ZstdError):
            decompress(dat1, dic2)

    def test_train_dict_arguments(self):
        with self.assertRaises(ValueError):
            train_dict([], 100*KB)

        with self.assertRaises(ValueError):
            train_dict(SAMPLES, -100)

        with self.assertRaises(ValueError):
            train_dict(SAMPLES, 0)

    def test_finalize_dict_arguments(self):
        if zstd_version_info < (1, 4, 5):
            with self.assertRaises(NotImplementedError):
                finalize_dict({1:2}, [b'aaa', b'bbb'], 100*KB, 2)
            return

        try:
            finalize_dict(TRAINED_DICT, SAMPLES, 1*MB, 2)
        except NotImplementedError:
            # < v1.4.5 at compile-time, >= v.1.4.5 at run-time
            return

        with self.assertRaises(ValueError):
            finalize_dict(TRAINED_DICT, [], 100*KB, 2)

        with self.assertRaises(ValueError):
            finalize_dict(TRAINED_DICT, SAMPLES, -100, 2)

        with self.assertRaises(ValueError):
            finalize_dict(TRAINED_DICT, SAMPLES, 0, 2)

    @unittest.skipIf(PYZSTD_CONFIG[1] == 'cffi', 'cffi implementation')
    def test_train_dict_c(self):
        # argument wrong type
        with self.assertRaises(TypeError):
            _zstd._train_dict({}, [], 100)
        with self.assertRaises(TypeError):
            _zstd._train_dict(b'', 99, 100)
        with self.assertRaises(TypeError):
            _zstd._train_dict(b'', [], 100.1)

        # size > size_t
        with self.assertRaises(ValueError):
            _zstd._train_dict(b'', [2**64+1], 100)

        # dict_size <= 0
        with self.assertRaises(ValueError):
            _zstd._train_dict(b'', [], 0)

    @unittest.skipIf(PYZSTD_CONFIG[1] == 'cffi', 'cffi implementation')
    def test_finalize_dict_c(self):
        if zstd_version_info < (1, 4, 5):
            with self.assertRaises(NotImplementedError):
                _zstd._finalize_dict(1, 2, 3, 4, 5)
            return

        try:
            _zstd._finalize_dict(TRAINED_DICT.dict_content, b'123', [3,], 1*MB, 5)
        except NotImplementedError:
            # < v1.4.5 at compile-time, >= v.1.4.5 at run-time
            return

        # argument wrong type
        with self.assertRaises(TypeError):
            _zstd._finalize_dict({}, b'', [], 100, 5)
        with self.assertRaises(TypeError):
            _zstd._finalize_dict(TRAINED_DICT.dict_content, {}, [], 100, 5)
        with self.assertRaises(TypeError):
            _zstd._finalize_dict(TRAINED_DICT.dict_content, b'', 99, 100, 5)
        with self.assertRaises(TypeError):
            _zstd._finalize_dict(TRAINED_DICT.dict_content, b'', [], 100.1, 5)
        with self.assertRaises(TypeError):
            _zstd._finalize_dict(TRAINED_DICT.dict_content, b'', [], 100, 5.1)

        # size > size_t
        with self.assertRaises(ValueError):
            _zstd._finalize_dict(TRAINED_DICT.dict_content, b'', [2**64+1], 100, 5)

        # dict_size <= 0
        with self.assertRaises(ValueError):
            _zstd._finalize_dict(TRAINED_DICT.dict_content, b'', [], 0, 5)

    def test_train_buffer_protocal_samples(self):
        def _nbytes(dat):
            if isinstance(dat, (bytes, bytearray)):
                return len(dat)
            return memoryview(dat).nbytes

        # prepare samples
        chunk_lst = []
        wrong_size_lst = []
        correct_size_lst = []
        for _ in range(300):
            arr = array.array('Q', [random.randint(0, 20) for i in range(20)])
            chunk_lst.append(arr)
            correct_size_lst.append(_nbytes(arr))
            wrong_size_lst.append(len(arr))
        concatenation = b''.join(chunk_lst)

        # wrong size list
        with self.assertRaisesRegex(ValueError,
                "The samples size list doesn't match the concatenation's size"):
            pyzstd._train_dict(concatenation, wrong_size_lst, 100*1024)

        # correct size list
        pyzstd._train_dict(concatenation, correct_size_lst, 3*1024)

        # test _finalize_dict
        if zstd_version_info < (1, 4, 5):
            return

        # wrong size list
        with self.assertRaisesRegex(ValueError,
                "The samples size list doesn't match the concatenation's size"):
            pyzstd._finalize_dict(TRAINED_DICT.dict_content,
                                  concatenation, wrong_size_lst, 300*1024, 5)

        # correct size list
        pyzstd._finalize_dict(TRAINED_DICT.dict_content,
                              concatenation, correct_size_lst, 300*1024, 5)

    def test_as_prefix(self):
        # V1
        V1 = THIS_FILE_BYTES
        zd = ZstdDict(V1, True)

        # V2
        mid = len(V1) // 2
        V2 = V1[:mid] + \
             (b'a' if V1[mid] != b'a' else b'b') + \
             V1[mid+1:]

        # compress
        dat = richmem_compress(V2, zstd_dict=zd.as_prefix)
        self.assertEqual(get_frame_info(dat).dictionary_id, 0)

        # decompress
        self.assertEqual(decompress(dat, zd.as_prefix), V2)

        # use wrong prefix
        zd2 = ZstdDict(SAMPLES[0], True)
        try:
            decompressed = decompress(dat, zd2.as_prefix)
        except ZstdError: # expected
            pass
        else:
            self.assertNotEqual(decompressed, V2)

        # read only attribute
        with self.assertRaises(AttributeError):
            zd.as_prefix = b'1234'

    def test_as_digested_dict(self):
        zd = TRAINED_DICT

        # test .as_digested_dict
        dat = richmem_compress(SAMPLES[0], zstd_dict=zd.as_digested_dict)
        self.assertEqual(decompress(dat, zd.as_digested_dict), SAMPLES[0])
        with self.assertRaises(AttributeError):
            zd.as_digested_dict = b'1234'

        # test .as_undigested_dict
        dat = richmem_compress(SAMPLES[0], zstd_dict=zd.as_undigested_dict)
        self.assertEqual(decompress(dat, zd.as_undigested_dict), SAMPLES[0])
        with self.assertRaises(AttributeError):
            zd.as_undigested_dict = b'1234'

    def test_advanced_compression_parameters(self):
        option = {CParameter.compressionLevel: 6,
                  CParameter.windowLog: 20,
                  CParameter.enableLongDistanceMatching: 1}

        # automatically select
        dat = richmem_compress(SAMPLES[0], option, TRAINED_DICT)
        self.assertEqual(decompress(dat, TRAINED_DICT), SAMPLES[0])

        # explicitly select
        dat = richmem_compress(SAMPLES[0], option, TRAINED_DICT.as_digested_dict)
        self.assertEqual(decompress(dat, TRAINED_DICT), SAMPLES[0])

    def test_len(self):
        self.assertEqual(len(TRAINED_DICT), len(TRAINED_DICT.dict_content))
        self.assertIn(str(len(TRAINED_DICT)), str(TRAINED_DICT))

class OutputBufferTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        KB = 1024
        MB = 1024 * 1024

        # should be same as the definition in _zstdmodule.c
        cls.BLOCK_SIZE = \
             [ 32*KB, 64*KB, 256*KB, 1*MB, 4*MB, 8*MB, 16*MB, 16*MB,
               32*MB, 32*MB, 32*MB, 32*MB, 64*MB, 64*MB, 128*MB, 128*MB,
               256*MB ]

        # accumulated size
        cls.ACCUMULATED_SIZE = list(itertools.accumulate(cls.BLOCK_SIZE))

        cls.TEST_RANGE = 5

        cls.NO_SIZE_OPTION = {CParameter.compressionLevel: compressionLevel_values.min,
                              CParameter.contentSizeFlag: 0}

    def compress_unknown_size(self, size):
        return compress(b'a' * size, self.NO_SIZE_OPTION)

    def test_empty_input(self):
        dat1 = b''

        # decompress() function
        dat2 = decompress(dat1)
        self.assertEqual(len(dat2), 0)

        # ZstdDecompressor class
        d = ZstdDecompressor()
        dat2 = d.decompress(dat1)
        self.assertEqual(len(dat2), 0)
        self.assertFalse(d.eof)
        self.assertTrue(d.needs_input)

        # EndlessZstdDecompressor class
        d = EndlessZstdDecompressor()
        dat2 = d.decompress(dat1)
        self.assertEqual(len(dat2), 0)
        self.assertTrue(d.at_frame_edge)
        self.assertTrue(d.needs_input)

    def test_zero_size_output(self):
        dat1 = self.compress_unknown_size(0)

        # decompress() function
        dat2 = decompress(dat1)
        self.assertEqual(len(dat2), 0)

        # ZstdDecompressor class
        d = ZstdDecompressor()
        dat2 = d.decompress(dat1)
        self.assertEqual(len(dat2), 0)
        self.assertTrue(d.eof)
        self.assertFalse(d.needs_input)

        # EndlessZstdDecompressor class
        d = EndlessZstdDecompressor()
        dat2 = d.decompress(dat1)
        self.assertEqual(len(dat2), 0)
        self.assertTrue(d.at_frame_edge)
        self.assertTrue(d.needs_input)

    def test_edge_sizes(self):
        for index in range(self.TEST_RANGE):
            for extra in [-1, 0, 1]:
                SIZE = self.ACCUMULATED_SIZE[index] + extra
                dat1 = self.compress_unknown_size(SIZE)

                # decompress() function
                dat2 = decompress(dat1)
                self.assertEqual(len(dat2), SIZE)

                # ZstdDecompressor class
                d = ZstdDecompressor()
                dat2 = d.decompress(dat1)
                self.assertEqual(len(dat2), SIZE)
                self.assertTrue(d.eof)
                self.assertFalse(d.needs_input)

                # EndlessZstdDecompressor class
                d = EndlessZstdDecompressor()
                dat2 = d.decompress(dat1)
                self.assertEqual(len(dat2), SIZE)
                self.assertTrue(d.at_frame_edge)
                self.assertTrue(d.needs_input)

    def test_edge_sizes_stream(self):
        SIZE = self.ACCUMULATED_SIZE[self.TEST_RANGE]
        dat1 = self.compress_unknown_size(SIZE)

        # ZstdDecompressor class
        d = ZstdDecompressor()
        d.decompress(dat1, 0)

        for index in range(self.TEST_RANGE+1):
            B_SIZE = self.BLOCK_SIZE[index]
            dat2 = d.decompress(b'', B_SIZE)

            self.assertEqual(len(dat2), B_SIZE)
            self.assertFalse(d.needs_input)
            if index < self.TEST_RANGE:
                self.assertFalse(d.eof)
            else:
                self.assertTrue(d.eof)

        # EndlessZstdDecompressor class
        d = EndlessZstdDecompressor()
        d.decompress(dat1, 0)

        for index in range(self.TEST_RANGE+1):
            B_SIZE = self.BLOCK_SIZE[index]
            dat2 = d.decompress(b'', B_SIZE)

            self.assertEqual(len(dat2), B_SIZE)
            if index < self.TEST_RANGE:
                self.assertFalse(d.at_frame_edge)
                self.assertFalse(d.needs_input)
            else:
                self.assertTrue(d.at_frame_edge)
                self.assertTrue(d.needs_input)

    def test_endlessdecompressor_2_frames(self):
        self.assertGreater(self.TEST_RANGE - 2, 0)

        for extra in [-1, 0, 1]:
            # frame 1 size
            SIZE1 = self.ACCUMULATED_SIZE[self.TEST_RANGE - 2] + extra
            # frame 2 size
            SIZE2 = self.ACCUMULATED_SIZE[self.TEST_RANGE] - SIZE1

            FRAME1 = self.compress_unknown_size(SIZE1)
            FRAME2 = self.compress_unknown_size(SIZE2)

            # one step
            d = EndlessZstdDecompressor()

            dat2 = d.decompress(FRAME1 + FRAME2)
            self.assertEqual(len(dat2), SIZE1 + SIZE2)
            self.assertTrue(d.at_frame_edge)
            self.assertTrue(d.needs_input)

            # two step
            d = EndlessZstdDecompressor()

            # frame 1
            dat2 = d.decompress(FRAME1 + FRAME2, SIZE1)
            self.assertEqual(len(dat2), SIZE1)
            self.assertFalse(d.at_frame_edge) # input stream not at a frame edge
            self.assertFalse(d.needs_input)

            # frame 2
            dat2 = d.decompress(b'')
            self.assertEqual(len(dat2), SIZE2)
            self.assertTrue(d.at_frame_edge)
            self.assertTrue(d.needs_input)

    def test_known_size(self):
        # only decompress() function supports first frame with known size

        # 1 frame, the decompressed size is known
        SIZE1 = 123456
        known_size = compress(b'a' * SIZE1)

        dat = decompress(known_size)
        self.assertEqual(len(dat), SIZE1)

        # 2 frame, the second frame's decompressed size is unknown
        for extra in [-1, 0, 1]:
            SIZE2 = self.BLOCK_SIZE[1] + self.BLOCK_SIZE[2] + extra
            unkown_size = self.compress_unknown_size(SIZE2)

            dat = decompress(known_size + unkown_size)
            self.assertEqual(len(dat), SIZE1 + SIZE2)

    # def test_large_output(self):
    #     SIZE = self.ACCUMULATED_SIZE[-1] + self.BLOCK_SIZE[-1] + 100_000
    #     dat1 = self.compress_unknown_size(SIZE)

    #     try:
    #         dat2 = decompress(dat1)
    #     except MemoryError:
    #         return

    #     leng_dat2 = len(dat2)
    #     del dat2
    #     self.assertEqual(leng_dat2, SIZE)

    def test_endless_maxlength(self):
        DECOMPRESSED_SIZE = 100*KB
        dat1 = compress(b'a' * DECOMPRESSED_SIZE, -3)

        # -1
        d = EndlessZstdDecompressor()
        dat2 = d.decompress(dat1, -1)
        self.assertEqual(len(dat2), DECOMPRESSED_SIZE)
        self.assertTrue(d.needs_input)
        self.assertTrue(d.at_frame_edge)

        # DECOMPRESSED_SIZE
        d = EndlessZstdDecompressor()
        dat2 = d.decompress(dat1, DECOMPRESSED_SIZE)
        self.assertEqual(len(dat2), DECOMPRESSED_SIZE)
        self.assertTrue(d.needs_input)
        self.assertTrue(d.at_frame_edge)

        # DECOMPRESSED_SIZE + 1
        d = EndlessZstdDecompressor()
        dat2 = d.decompress(dat1, DECOMPRESSED_SIZE+1)
        self.assertEqual(len(dat2), DECOMPRESSED_SIZE)
        self.assertTrue(d.needs_input)
        self.assertTrue(d.at_frame_edge)

        # DECOMPRESSED_SIZE - 1
        d = EndlessZstdDecompressor()
        dat2 = d.decompress(dat1, DECOMPRESSED_SIZE-1)
        self.assertEqual(len(dat2), DECOMPRESSED_SIZE-1)
        self.assertFalse(d.needs_input)
        self.assertFalse(d.at_frame_edge)

        dat2 = d.decompress(b'')
        self.assertEqual(len(dat2), 1)
        self.assertTrue(d.needs_input)
        self.assertTrue(d.at_frame_edge)

class FileTestCase(unittest.TestCase):
    def setUp(self):
        self.DECOMPRESSED_42 = b'a'*42
        self.FRAME_42 = compress(self.DECOMPRESSED_42)

    def test_init(self):
        with ZstdFile(BytesIO(COMPRESSED_100_PLUS_32KB)) as f:
            pass
        with ZstdFile(BytesIO(), "w") as f:
            pass
        with ZstdFile(BytesIO(), "x") as f:
            pass
        with ZstdFile(BytesIO(), "a") as f:
            pass

        with ZstdFile(BytesIO(), "w", level_or_option=12) as f:
            pass
        with ZstdFile(BytesIO(), "w", level_or_option={CParameter.checksumFlag:1}) as f:
            pass
        with ZstdFile(BytesIO(), "w", level_or_option={}) as f:
            pass
        with ZstdFile(BytesIO(), "w", level_or_option=20, zstd_dict=TRAINED_DICT) as f:
            pass

        with ZstdFile(BytesIO(), "r", level_or_option={DParameter.windowLogMax:25}) as f:
            pass
        with ZstdFile(BytesIO(), "r", level_or_option={}, zstd_dict=TRAINED_DICT) as f:
            pass

    def test_init_with_PathLike_filename(self):
        with tempfile.NamedTemporaryFile(delete=False) as tmp_f:
            if sys.version_info >= (3, 6):
                filename = pathlib.Path(tmp_f.name)
            else:
                filename = tmp_f.name

        with ZstdFile(filename, "a") as f:
            f.write(DECOMPRESSED_100_PLUS_32KB)
        with ZstdFile(filename) as f:
            self.assertEqual(f.read(), DECOMPRESSED_100_PLUS_32KB)

        with ZstdFile(filename, "a") as f:
            f.write(DECOMPRESSED_100_PLUS_32KB)
        with ZstdFile(filename) as f:
            self.assertEqual(f.read(), DECOMPRESSED_100_PLUS_32KB * 2)

        os.remove(filename)

    def test_init_with_filename(self):
        with tempfile.NamedTemporaryFile(delete=False) as tmp_f:
            if sys.version_info >= (3, 6):
                filename = pathlib.Path(tmp_f.name)
            else:
                filename = tmp_f.name

        with ZstdFile(filename) as f:
            pass
        with ZstdFile(filename, "w") as f:
            pass
        with ZstdFile(filename, "a") as f:
            pass

        os.remove(filename)

    def test_init_mode(self):
        bi = BytesIO()

        with ZstdFile(bi, "r"):
            pass
        with ZstdFile(bi, "rb"):
            pass
        with ZstdFile(bi, "w"):
            pass
        with ZstdFile(bi, "wb"):
            pass
        with ZstdFile(bi, "a"):
            pass
        with ZstdFile(bi, "ab"):
            pass

    def test_init_with_x_mode(self):
        with tempfile.NamedTemporaryFile() as tmp_f:
            if sys.version_info >= (3, 6):
                filename = pathlib.Path(tmp_f.name)
            else:
                filename = tmp_f.name

        for mode in ("x", "xb"):
            with ZstdFile(filename, mode):
                pass
            with self.assertRaises(FileExistsError):
                with ZstdFile(filename, mode):
                    pass
            os.remove(filename)

    def test_init_bad_mode(self):
        with self.assertRaises(ValueError):
            ZstdFile(BytesIO(COMPRESSED_100_PLUS_32KB), (3, "x"))
        with self.assertRaises(ValueError):
            ZstdFile(BytesIO(COMPRESSED_100_PLUS_32KB), "")
        with self.assertRaises(ValueError):
            ZstdFile(BytesIO(COMPRESSED_100_PLUS_32KB), "xt")
        with self.assertRaises(ValueError):
            ZstdFile(BytesIO(COMPRESSED_100_PLUS_32KB), "x+")
        with self.assertRaises(ValueError):
            ZstdFile(BytesIO(COMPRESSED_100_PLUS_32KB), "rx")
        with self.assertRaises(ValueError):
            ZstdFile(BytesIO(COMPRESSED_100_PLUS_32KB), "wx")
        with self.assertRaises(ValueError):
            ZstdFile(BytesIO(COMPRESSED_100_PLUS_32KB), "rt")
        with self.assertRaises(ValueError):
            ZstdFile(BytesIO(COMPRESSED_100_PLUS_32KB), "r+")
        with self.assertRaises(ValueError):
            ZstdFile(BytesIO(COMPRESSED_100_PLUS_32KB), "wt")
        with self.assertRaises(ValueError):
            ZstdFile(BytesIO(COMPRESSED_100_PLUS_32KB), "w+")
        with self.assertRaises(ValueError):
            ZstdFile(BytesIO(COMPRESSED_100_PLUS_32KB), "rw")

        with self.assertRaisesRegex(TypeError, r"NOT be CParameter"):
            ZstdFile(BytesIO(), 'rb', level_or_option={CParameter.compressionLevel:5})
        with self.assertRaisesRegex(TypeError, r"NOT be DParameter"):
            ZstdFile(BytesIO(), 'wb', level_or_option={DParameter.windowLogMax:21})

        with self.assertRaises(TypeError):
            ZstdFile(BytesIO(COMPRESSED_100_PLUS_32KB), "r", level_or_option=12)

    def test_init_bad_check(self):
        with self.assertRaises(TypeError):
            ZstdFile(BytesIO(), "w", level_or_option='asd')
        # CHECK_UNKNOWN and anything above CHECK_ID_MAX should be invalid.
        with self.assertRaises(ZstdError):
            ZstdFile(BytesIO(), "w", level_or_option={999:9999})
        with self.assertRaises(ZstdError):
            ZstdFile(BytesIO(), "w", level_or_option={CParameter.windowLog:99})

        with self.assertRaises(TypeError):
            ZstdFile(BytesIO(COMPRESSED_100_PLUS_32KB), "r", level_or_option=33)

        with self.assertRaises(ValueError):
            ZstdFile(BytesIO(COMPRESSED_100_PLUS_32KB),
                             level_or_option={DParameter.windowLogMax:2**31})

        with self.assertRaises(ZstdError):
            ZstdFile(BytesIO(COMPRESSED_100_PLUS_32KB),
                             level_or_option={444:333})

        with self.assertRaises(TypeError):
            ZstdFile(BytesIO(COMPRESSED_100_PLUS_32KB), zstd_dict={1:2})

        with self.assertRaises(TypeError):
            ZstdFile(BytesIO(COMPRESSED_100_PLUS_32KB), zstd_dict=b'dict123456')

    def test_init_sizes_arg(self):
        with ZstdFile(BytesIO(), 'r', read_size=1):
            pass
        with self.assertRaises(ValueError):
            ZstdFile(BytesIO(), 'r', read_size=0)
        with self.assertRaises(ValueError):
            ZstdFile(BytesIO(), 'r', read_size=-1)
        with self.assertRaises(TypeError):
            ZstdFile(BytesIO(), 'r', read_size=(10,))
        with self.assertRaisesRegex(ValueError, 'read_size'):
            ZstdFile(BytesIO(), 'w', read_size=10)

        with ZstdFile(BytesIO(), 'w', write_size=1):
            pass
        with self.assertRaises(ValueError):
            ZstdFile(BytesIO(), 'w', write_size=0)
        with self.assertRaises(ValueError):
            ZstdFile(BytesIO(), 'w', write_size=-1)
        with self.assertRaises(TypeError):
            ZstdFile(BytesIO(), 'w', write_size=(10,))
        with self.assertRaisesRegex(ValueError, 'write_size'):
            ZstdFile(BytesIO(), 'r', write_size=10)

    def test_init_close_fp(self):
        # get a temp file name
        with tempfile.NamedTemporaryFile(delete=False) as tmp_f:
            tmp_f.write(DAT_130K_C)
            filename = tmp_f.name

        with self.assertRaises(ValueError):
            ZstdFile(filename, level_or_option={'a':'b'})

        # for PyPy
        gc.collect()

        os.remove(filename)

    def test_close(self):
        with BytesIO(COMPRESSED_100_PLUS_32KB) as src:
            f = ZstdFile(src)
            f.close()
            # ZstdFile.close() should not close the underlying file object.
            self.assertFalse(src.closed)
            # Try closing an already-closed ZstdFile.
            f.close()
            self.assertFalse(src.closed)

        # Test with a real file on disk, opened directly by ZstdFile.
        with tempfile.NamedTemporaryFile(delete=False) as tmp_f:
            if sys.version_info >= (3, 6):
                filename = pathlib.Path(tmp_f.name)
            else:
                filename = tmp_f.name

        f = ZstdFile(filename)
        fp = f._fp
        f.close()
        # Here, ZstdFile.close() *should* close the underlying file object.
        self.assertTrue(fp.closed)
        # Try closing an already-closed ZstdFile.
        f.close()

        os.remove(filename)

    def test_closed(self):
        f = ZstdFile(BytesIO(COMPRESSED_100_PLUS_32KB))
        try:
            self.assertFalse(f.closed)
            f.read()
            self.assertFalse(f.closed)
        finally:
            f.close()
        self.assertTrue(f.closed)

        f = ZstdFile(BytesIO(), "w")
        try:
            self.assertFalse(f.closed)
        finally:
            f.close()
        self.assertTrue(f.closed)

    def test_fileno(self):
        # 1
        f = ZstdFile(BytesIO(COMPRESSED_100_PLUS_32KB))
        try:
            self.assertRaises(UnsupportedOperation, f.fileno)
        finally:
            f.close()
        self.assertRaises(ValueError, f.fileno)

        # 2
        with tempfile.NamedTemporaryFile(delete=False) as tmp_f:
            if sys.version_info >= (3, 6):
                filename = pathlib.Path(tmp_f.name)
            else:
                filename = tmp_f.name

        f = ZstdFile(filename)
        try:
            self.assertEqual(f.fileno(), f._fp.fileno())
            self.assertIsInstance(f.fileno(), int)
        finally:
            f.close()
        self.assertRaises(ValueError, f.fileno)

        os.remove(filename)

        # 3, no .fileno() method
        class C:
            def read(self, size=-1):
                return b'123'
        with ZstdFile(C(), 'rb') as f:
            with self.assertRaisesRegex(AttributeError, r'fileno'):
                f.fileno()

    def test_seekable(self):
        f = ZstdFile(BytesIO(COMPRESSED_100_PLUS_32KB))
        try:
            self.assertTrue(f.seekable())
            f.read()
            self.assertTrue(f.seekable())
        finally:
            f.close()
        self.assertRaises(ValueError, f.seekable)

        f = ZstdFile(BytesIO(), "w")
        try:
            self.assertFalse(f.seekable())
        finally:
            f.close()
        self.assertRaises(ValueError, f.seekable)

        src = BytesIO(COMPRESSED_100_PLUS_32KB)
        src.seekable = lambda: False
        f = ZstdFile(src)
        try:
            self.assertFalse(f.seekable())
        finally:
            f.close()
        self.assertRaises(ValueError, f.seekable)

    def test_readable(self):
        f = ZstdFile(BytesIO(COMPRESSED_100_PLUS_32KB))
        try:
            self.assertTrue(f.readable())
            f.read()
            self.assertTrue(f.readable())
        finally:
            f.close()
        self.assertRaises(ValueError, f.readable)

        f = ZstdFile(BytesIO(), "w")
        try:
            self.assertFalse(f.readable())
        finally:
            f.close()
        self.assertRaises(ValueError, f.readable)

    def test_writable(self):
        f = ZstdFile(BytesIO(COMPRESSED_100_PLUS_32KB))
        try:
            self.assertFalse(f.writable())
            f.read()
            self.assertFalse(f.writable())
        finally:
            f.close()
        self.assertRaises(ValueError, f.writable)

        f = ZstdFile(BytesIO(), "w")
        try:
            self.assertTrue(f.writable())
        finally:
            f.close()
        self.assertRaises(ValueError, f.writable)

    def test_ZstdFileWriter(self):
        bo = BytesIO()

        # wrong arg
        with self.assertRaisesRegex(TypeError, 'level_or_option'):
            pyzstd.zstdfile.ZstdFileWriter(
                                fp=bo,
                                level_or_option=TRAINED_DICT,
                                zstd_dict=None,
                                write_size=131591)
        with self.assertRaisesRegex(TypeError, 'zstd_dict'):
            pyzstd.zstdfile.ZstdFileWriter(
                                fp=bo,
                                level_or_option=3,
                                zstd_dict={1:2},
                                write_size=131591)
        with self.assertRaisesRegex(ValueError, 'write_size'):
            pyzstd.zstdfile.ZstdFileWriter(
                                fp=bo,
                                level_or_option=3,
                                zstd_dict=TRAINED_DICT,
                                write_size=0)

        w = pyzstd.zstdfile.ZstdFileWriter(
                            fp=bo,
                            level_or_option=None,
                            zstd_dict=None,
                            write_size=131591)
        # write
        ret = w.write(DAT_130K_D)
        self.assertEqual(ret[0], len(DAT_130K_D))
        self.assertGreater(ret[1], 0)
        # flush block
        ret = w.flush(ZstdCompressor.FLUSH_BLOCK)
        self.assertEqual(ret[0], 0)
        self.assertGreaterEqual(ret[1], 0)
        # flush frame
        ret = w.flush(ZstdCompressor.FLUSH_FRAME)
        self.assertEqual(ret[0], 0)
        self.assertGreaterEqual(ret[1], 0)
        # flush .CONTINUE
        with self.assertRaisesRegex(ValueError,
                                    'mode argument wrong value'):
            w.flush(ZstdCompressor.CONTINUE)

        self.assertEqual(decompress(bo.getvalue()), DAT_130K_D)

    def test_ZstdFileReader(self):
        # wrong arg
        with self.assertRaisesRegex(TypeError, 'zstd_dict'):
            pyzstd.zstdfile.ZstdFileReader(
                                fp=BytesIO(self.FRAME_42),
                                zstd_dict={1:2}, option=None,
                                read_size=131075)
        with self.assertRaisesRegex(TypeError, 'option'):
            pyzstd.zstdfile.ZstdFileReader(
                                fp=BytesIO(self.FRAME_42),
                                zstd_dict=TRAINED_DICT, option=3,
                                read_size=131075)
        with self.assertRaisesRegex(ValueError, 'read_size'):
            pyzstd.zstdfile.ZstdFileReader(
                                fp=BytesIO(self.FRAME_42),
                                zstd_dict=TRAINED_DICT, option=3,
                                read_size=0)

        r = pyzstd.zstdfile.ZstdFileReader(
                            fp=BytesIO(self.FRAME_42),
                            zstd_dict=None, option=None,
                            read_size=131075)
        ba = bytearray(100)
        mv = memoryview(ba)

        # cffi implementation can't distinguish read-only buffer
        if PYZSTD_CONFIG[1] != 'cffi':
            with self.assertRaisesRegex(BufferError, 'not writable'):
                r.readinto(b'123')

        self.assertEqual(r.readinto(mv[0:0]), 0)
        self.assertEqual(r.readinto(mv[:42]), 42)
        self.assertEqual(mv[:42], self.DECOMPRESSED_42)
        self.assertFalse(r.eof)
        self.assertEqual(r.readinto(mv[:10]), 0)
        self.assertTrue(r.eof)

    def test_read(self):
        with ZstdFile(BytesIO(self.FRAME_42)) as f:
            self.assertEqual(f.read(), self.DECOMPRESSED_42)
            self.assertTrue(f._buffer.raw._decomp.eof)
            self.assertEqual(f.read(), b"")
            self.assertTrue(f._buffer.raw._decomp.eof)

        with ZstdFile(BytesIO(COMPRESSED_100_PLUS_32KB)) as f:
            self.assertEqual(f.read(), DECOMPRESSED_100_PLUS_32KB)
            self.assertTrue(f._buffer.raw._decomp.eof)
            self.assertEqual(f.read(), b"")
            self.assertTrue(f._buffer.raw._decomp.eof)

        with ZstdFile(BytesIO(DAT_130K_C),
                              read_size=64*1024) as f:
            self.assertEqual(f.read(), DAT_130K_D)

        with ZstdFile(BytesIO(COMPRESSED_100_PLUS_32KB),
                              level_or_option={DParameter.windowLogMax:20}) as f:
            self.assertEqual(f.read(), DECOMPRESSED_100_PLUS_32KB)
            self.assertEqual(f.read(), b"")
            self.assertEqual(f.read(10), b"")

    def test_read_0(self):
        with ZstdFile(BytesIO(COMPRESSED_100_PLUS_32KB)) as f:
            self.assertEqual(f.read(0), b"")
            self.assertEqual(f.read(), DECOMPRESSED_100_PLUS_32KB)
        with ZstdFile(BytesIO(COMPRESSED_100_PLUS_32KB),
                              level_or_option={DParameter.windowLogMax:20}) as f:
            self.assertEqual(f.read(0), b"")

        # empty file
        with ZstdFile(BytesIO(b'')) as f:
            self.assertEqual(f.read(0), b"")
            self.assertEqual(f.read(10), b"")

        with ZstdFile(BytesIO(b'')) as f:
            self.assertEqual(f.read(10), b"")

    def test_read_10(self):
        with ZstdFile(BytesIO(COMPRESSED_100_PLUS_32KB)) as f:
            chunks = []
            while True:
                result = f.read(10)
                if not result:
                    break
                self.assertLessEqual(len(result), 10)
                chunks.append(result)
            self.assertEqual(b"".join(chunks), DECOMPRESSED_100_PLUS_32KB)

    def test_read_multistream(self):
        with ZstdFile(BytesIO(COMPRESSED_100_PLUS_32KB * 5)) as f:
            self.assertEqual(f.read(), DECOMPRESSED_100_PLUS_32KB * 5)

        with ZstdFile(BytesIO(COMPRESSED_100_PLUS_32KB + SKIPPABLE_FRAME)) as f:
            self.assertEqual(f.read(), DECOMPRESSED_100_PLUS_32KB)

        with ZstdFile(BytesIO(COMPRESSED_100_PLUS_32KB + COMPRESSED_DAT)) as f:
            self.assertEqual(f.read(), DECOMPRESSED_100_PLUS_32KB + DECOMPRESSED_DAT)

    def test_read_incomplete(self):
        with ZstdFile(BytesIO(DAT_130K_C[:-200])) as f:
            self.assertRaises(EOFError, f.read)

        # Trailing data isn't a valid compressed stream
        with ZstdFile(BytesIO(self.FRAME_42 + b'12345')) as f:
            self.assertRaises(ZstdError, f.read)

        with ZstdFile(BytesIO(SKIPPABLE_FRAME + b'12345')) as f:
            self.assertRaises(ZstdError, f.read)

    def test_read_truncated(self):
        # Drop stream epilogue: 4 bytes checksum
        truncated = DAT_130K_C[:-4]
        with ZstdFile(BytesIO(truncated)) as f:
            self.assertRaises(EOFError, f.read)

        with ZstdFile(BytesIO(truncated)) as f:
            # this is an important test, make sure it doesn't raise EOFError.
            self.assertEqual(f.read(130*1024), DAT_130K_D)
            with self.assertRaises(EOFError):
                f.read(1)

        # Incomplete header
        for i in range(1, 20):
            with ZstdFile(BytesIO(truncated[:i])) as f:
                self.assertRaises(EOFError, f.read, 1)

    def test_read_bad_args(self):
        f = ZstdFile(BytesIO(COMPRESSED_DAT))
        f.close()
        self.assertRaises(ValueError, f.read)
        with ZstdFile(BytesIO(), "w") as f:
            self.assertRaises(ValueError, f.read)
        with ZstdFile(BytesIO(COMPRESSED_DAT)) as f:
            self.assertRaises(TypeError, f.read, float())

    def test_read_bad_data(self):
        with ZstdFile(BytesIO(COMPRESSED_BOGUS)) as f:
            self.assertRaises(ZstdError, f.read)

    def test_read_exception(self):
        class C:
            def read(self, size=-1):
                raise OSError
        with ZstdFile(C()) as f:
            with self.assertRaises(OSError):
                f.read(10)

    def test_read1(self):
        with ZstdFile(BytesIO(DAT_130K_C)) as f:
            blocks = []
            while True:
                result = f.read1()
                if not result:
                    break
                blocks.append(result)
            self.assertEqual(b"".join(blocks), DAT_130K_D)
            self.assertEqual(f.read1(), b"")

    def test_read1_0(self):
        with ZstdFile(BytesIO(COMPRESSED_DAT)) as f:
            self.assertEqual(f.read1(0), b"")

    def test_read1_10(self):
        with ZstdFile(BytesIO(COMPRESSED_DAT)) as f:
            blocks = []
            while True:
                result = f.read1(10)
                if not result:
                    break
                blocks.append(result)
            self.assertEqual(b"".join(blocks), DECOMPRESSED_DAT)
            self.assertEqual(f.read1(), b"")

    def test_read1_multistream(self):
        with ZstdFile(BytesIO(COMPRESSED_100_PLUS_32KB * 5)) as f:
            blocks = []
            while True:
                result = f.read1()
                if not result:
                    break
                blocks.append(result)
            self.assertEqual(b"".join(blocks), DECOMPRESSED_100_PLUS_32KB * 5)
            self.assertEqual(f.read1(), b"")

    def test_read1_bad_args(self):
        f = ZstdFile(BytesIO(COMPRESSED_100_PLUS_32KB))
        f.close()
        self.assertRaises(ValueError, f.read1)
        with ZstdFile(BytesIO(), "w") as f:
            self.assertRaises(ValueError, f.read1)
        with ZstdFile(BytesIO(COMPRESSED_100_PLUS_32KB)) as f:
            self.assertRaises(TypeError, f.read1, None)

    def test_readinto(self):
        arr = array.array("I", range(100))
        self.assertEqual(len(arr), 100)
        self.assertEqual(len(arr) * arr.itemsize, 400)
        ba = bytearray(300)
        with ZstdFile(BytesIO(COMPRESSED_100_PLUS_32KB)) as f:
            # 0 length output buffer
            self.assertEqual(f.readinto(ba[0:0]), 0)

            # use correct length for buffer protocol object
            self.assertEqual(f.readinto(arr), 400)
            self.assertEqual(arr.tobytes(), DECOMPRESSED_100_PLUS_32KB[:400])

            # normal readinto
            self.assertEqual(f.readinto(ba), 300)
            self.assertEqual(ba, DECOMPRESSED_100_PLUS_32KB[400:700])

    def test_peek(self):
        with ZstdFile(BytesIO(DAT_130K_C)) as f:
            result = f.peek()
            self.assertGreater(len(result), 0)
            self.assertTrue(DAT_130K_D.startswith(result))
            self.assertEqual(f.read(), DAT_130K_D)
        with ZstdFile(BytesIO(DAT_130K_C)) as f:
            result = f.peek(10)
            self.assertGreater(len(result), 0)
            self.assertTrue(DAT_130K_D.startswith(result))
            self.assertEqual(f.read(), DAT_130K_D)

    def test_peek_bad_args(self):
        with ZstdFile(BytesIO(), "w") as f:
            self.assertRaises(ValueError, f.peek)

    def test_iterator(self):
        with BytesIO(THIS_FILE_BYTES) as f:
            lines = f.readlines()
        compressed = compress(THIS_FILE_BYTES)

        # iter
        with ZstdFile(BytesIO(compressed)) as f:
            self.assertListEqual(list(iter(f)), lines)

        # readline
        with ZstdFile(BytesIO(compressed)) as f:
            for line in lines:
                self.assertEqual(f.readline(), line)
            self.assertEqual(f.readline(), b'')
            self.assertEqual(f.readline(), b'')

        # readlines
        with ZstdFile(BytesIO(compressed)) as f:
            self.assertListEqual(f.readlines(), lines)

    def test_decompress_limited(self):
        _ZSTD_DStreamInSize = 128*1024 + 3

        bomb = compress(b'\0' * int(2e6), level_or_option=10)
        self.assertLess(len(bomb), _ZSTD_DStreamInSize)

        decomp = ZstdFile(BytesIO(bomb))
        self.assertEqual(decomp.read(1), b'\0')

        # BufferedReader uses 128 KiB buffer in __init__.py
        max_decomp = 128*1024
        self.assertLessEqual(decomp._buffer.raw.tell(), max_decomp,
            "Excessive amount of data was decompressed")

    def test_write(self):
        with BytesIO() as dst:
            with ZstdFile(dst, "w") as f:
                f.write(THIS_FILE_BYTES)

            comp = ZstdCompressor()
            expected = comp.compress(THIS_FILE_BYTES) + comp.flush()
            self.assertEqual(dst.getvalue(), expected)

        with BytesIO() as dst:
            with ZstdFile(dst, "w", level_or_option=12) as f:
                f.write(THIS_FILE_BYTES)

            comp = ZstdCompressor(12)
            expected = comp.compress(THIS_FILE_BYTES) + comp.flush()
            self.assertEqual(dst.getvalue(), expected)

        with BytesIO() as dst:
            with ZstdFile(dst, "w", level_or_option={CParameter.checksumFlag:1}) as f:
                f.write(THIS_FILE_BYTES)

            comp = ZstdCompressor({CParameter.checksumFlag:1})
            expected = comp.compress(THIS_FILE_BYTES) + comp.flush()
            self.assertEqual(dst.getvalue(), expected)

        with BytesIO() as dst:
            option = {CParameter.compressionLevel:-5,
                      CParameter.checksumFlag:1}
            with ZstdFile(dst, "w",
                          level_or_option=option,
                          write_size=1024) as f:
                f.write(THIS_FILE_BYTES)

            comp = ZstdCompressor(option)
            expected = comp.compress(THIS_FILE_BYTES) + comp.flush()
            self.assertEqual(dst.getvalue(), expected)

    def test_write_empty_frame(self):
        # .FLUSH_FRAME generates an empty content frame
        c = ZstdCompressor()
        self.assertNotEqual(c.flush(c.FLUSH_FRAME), b'')
        self.assertNotEqual(c.flush(c.FLUSH_FRAME), b'')

        # don't generate empty content frame
        bo = BytesIO()
        with ZstdFile(bo, 'w') as f:
            pass
        self.assertEqual(bo.getvalue(), b'')

        bo = BytesIO()
        with ZstdFile(bo, 'w') as f:
            f.flush(f.FLUSH_FRAME)
        self.assertEqual(bo.getvalue(), b'')

        # if .write(b''), generate empty content frame
        bo = BytesIO()
        with ZstdFile(bo, 'w') as f:
            f.write(b'')
        self.assertNotEqual(bo.getvalue(), b'')

        # has an empty content frame
        bo = BytesIO()
        with ZstdFile(bo, 'w') as f:
            f.flush(f.FLUSH_BLOCK)
        self.assertNotEqual(bo.getvalue(), b'')

    def test_write_empty_block(self):
        # If no internal data, .FLUSH_BLOCK return b''.
        c = ZstdCompressor()
        self.assertEqual(c.flush(c.FLUSH_BLOCK), b'')
        self.assertNotEqual(c.compress(b'123', c.FLUSH_BLOCK),
                            b'')
        self.assertEqual(c.flush(c.FLUSH_BLOCK), b'')
        self.assertEqual(c.compress(b''), b'')
        self.assertEqual(c.compress(b''), b'')
        self.assertEqual(c.flush(c.FLUSH_BLOCK), b'')

        # mode = .last_mode
        bo = BytesIO()
        with ZstdFile(bo, 'w') as f:
            f.write(b'123')
            f.flush(f.FLUSH_BLOCK)
            fp_pos = f._fp.tell()
            self.assertNotEqual(fp_pos, 0)
            f.flush(f.FLUSH_BLOCK)
            self.assertEqual(f._fp.tell(), fp_pos)

        # mode != .last_mode
        bo = BytesIO()
        with ZstdFile(bo, 'w') as f:
            f.flush(f.FLUSH_BLOCK)
            self.assertEqual(f._fp.tell(), 0)
            f.write(b'')
            f.flush(f.FLUSH_BLOCK)
            self.assertEqual(f._fp.tell(), 0)

    def test_write_101(self):
        with BytesIO() as dst:
            with ZstdFile(dst, "w") as f:
                for start in range(0, len(THIS_FILE_BYTES), 101):
                    f.write(THIS_FILE_BYTES[start:start+101])

            comp = ZstdCompressor()
            expected = comp.compress(THIS_FILE_BYTES) + comp.flush()
            self.assertEqual(dst.getvalue(), expected)

    def test_write_append(self):
        def comp(data):
            comp = ZstdCompressor()
            return comp.compress(data) + comp.flush()

        part1 = THIS_FILE_BYTES[:1024]
        part2 = THIS_FILE_BYTES[1024:1536]
        part3 = THIS_FILE_BYTES[1536:]
        expected = b"".join(comp(x) for x in (part1, part2, part3))
        with BytesIO() as dst:
            with ZstdFile(dst, "w") as f:
                f.write(part1)
            with ZstdFile(dst, "a") as f:
                f.write(part2)
            with ZstdFile(dst, "a") as f:
                f.write(part3)
            self.assertEqual(dst.getvalue(), expected)

    def test_write_bad_args(self):
        f = ZstdFile(BytesIO(), "w")
        f.close()
        self.assertRaises(ValueError, f.write, b"foo")
        with ZstdFile(BytesIO(COMPRESSED_100_PLUS_32KB), "r") as f:
            self.assertRaises(ValueError, f.write, b"bar")
        with ZstdFile(BytesIO(), "w") as f:
            self.assertRaises(TypeError, f.write, None)
            self.assertRaises(TypeError, f.write, "text")
            self.assertRaises(TypeError, f.write, 789)

    def test_writelines(self):
        def comp(data):
            comp = ZstdCompressor()
            return comp.compress(data) + comp.flush()

        with BytesIO(THIS_FILE_BYTES) as f:
            lines = f.readlines()
        with BytesIO() as dst:
            with ZstdFile(dst, "w") as f:
                f.writelines(lines)
            expected = comp(THIS_FILE_BYTES)
            self.assertEqual(dst.getvalue(), expected)

    def test_seek_forward(self):
        with ZstdFile(BytesIO(COMPRESSED_100_PLUS_32KB)) as f:
            f.seek(555)
            self.assertEqual(f.read(), DECOMPRESSED_100_PLUS_32KB[555:])

    def test_seek_forward_across_streams(self):
        with ZstdFile(BytesIO(COMPRESSED_100_PLUS_32KB * 2)) as f:
            f.seek(len(DECOMPRESSED_100_PLUS_32KB) + 123)
            self.assertEqual(f.read(), DECOMPRESSED_100_PLUS_32KB[123:])

    def test_seek_forward_relative_to_current(self):
        with ZstdFile(BytesIO(COMPRESSED_100_PLUS_32KB)) as f:
            f.read(100)
            f.seek(1236, 1)
            self.assertEqual(f.read(), DECOMPRESSED_100_PLUS_32KB[1336:])

    def test_seek_forward_relative_to_end(self):
        with ZstdFile(BytesIO(COMPRESSED_100_PLUS_32KB)) as f:
            f.seek(-555, 2)
            self.assertEqual(f.read(), DECOMPRESSED_100_PLUS_32KB[-555:])

    def test_seek_backward(self):
        with ZstdFile(BytesIO(COMPRESSED_100_PLUS_32KB)) as f:
            f.read(1001)
            f.seek(211)
            self.assertEqual(f.read(), DECOMPRESSED_100_PLUS_32KB[211:])

    def test_seek_backward_across_streams(self):
        with ZstdFile(BytesIO(COMPRESSED_100_PLUS_32KB * 2)) as f:
            f.read(len(DECOMPRESSED_100_PLUS_32KB) + 333)
            f.seek(737)
            self.assertEqual(f.read(),
              DECOMPRESSED_100_PLUS_32KB[737:] + DECOMPRESSED_100_PLUS_32KB)

    def test_seek_backward_relative_to_end(self):
        with ZstdFile(BytesIO(COMPRESSED_100_PLUS_32KB)) as f:
            f.seek(-150, 2)
            self.assertEqual(f.read(), DECOMPRESSED_100_PLUS_32KB[-150:])

    def test_seek_past_end(self):
        with ZstdFile(BytesIO(COMPRESSED_100_PLUS_32KB)) as f:
            f.seek(len(DECOMPRESSED_100_PLUS_32KB) + 9001)
            self.assertEqual(f.tell(), len(DECOMPRESSED_100_PLUS_32KB))
            self.assertEqual(f.read(), b"")

    def test_seek_past_start(self):
        with ZstdFile(BytesIO(COMPRESSED_100_PLUS_32KB)) as f:
            f.seek(-88)
            self.assertEqual(f.tell(), 0)
            self.assertEqual(f.read(), DECOMPRESSED_100_PLUS_32KB)

    def test_seek_bad_args(self):
        f = ZstdFile(BytesIO(COMPRESSED_100_PLUS_32KB))
        f.close()
        self.assertRaises(ValueError, f.seek, 0)
        with ZstdFile(BytesIO(), "w") as f:
            self.assertRaises(ValueError, f.seek, 0)
        with ZstdFile(BytesIO(COMPRESSED_100_PLUS_32KB)) as f:
            self.assertRaises(ValueError, f.seek, 0, 3)
            # io.BufferedReader raises TypeError instead of ValueError
            self.assertRaises((TypeError, ValueError), f.seek, 9, ())
            self.assertRaises(TypeError, f.seek, None)
            self.assertRaises(TypeError, f.seek, b"derp")

    def test_seek_not_seekable(self):
        class C(BytesIO):
            def seekable(self):
                return False
        obj = C(COMPRESSED_100_PLUS_32KB)
        with ZstdFile(obj, 'r') as f:
            d = f.read(1)
            self.assertFalse(f.seekable())
            with self.assertRaisesRegex(io.UnsupportedOperation,
                                        'not seekable'):
                f.seek(0)
            d += f.read()
            self.assertEqual(d, DECOMPRESSED_100_PLUS_32KB)

    def test_tell(self):
        with ZstdFile(BytesIO(DAT_130K_C)) as f:
            pos = 0
            while True:
                self.assertEqual(f.tell(), pos)
                result = f.read(random.randint(171, 189))
                if not result:
                    break
                pos += len(result)
            self.assertEqual(f.tell(), len(DAT_130K_D))
        with ZstdFile(BytesIO(), "w") as f:
            for pos in range(0, len(DAT_130K_D), 143):
                self.assertEqual(f.tell(), pos)
                f.write(DAT_130K_D[pos:pos+143])
            self.assertEqual(f.tell(), len(DAT_130K_D))

    def test_tell_bad_args(self):
        f = ZstdFile(BytesIO(COMPRESSED_100_PLUS_32KB))
        f.close()
        self.assertRaises(ValueError, f.tell)

    def test_file_dict(self):
        # default
        bi = BytesIO()
        with ZstdFile(bi, 'w', zstd_dict=TRAINED_DICT) as f:
            f.write(SAMPLES[0])
        bi.seek(0)
        with ZstdFile(bi, zstd_dict=TRAINED_DICT) as f:
            dat = f.read()
        self.assertEqual(dat, SAMPLES[0])

        # .as_(un)digested_dict
        bi = BytesIO()
        with ZstdFile(bi, 'w', zstd_dict=TRAINED_DICT.as_digested_dict) as f:
            f.write(SAMPLES[0])
        bi.seek(0)
        with ZstdFile(bi, zstd_dict=TRAINED_DICT.as_undigested_dict) as f:
            dat = f.read()
        self.assertEqual(dat, SAMPLES[0])

    def test_file_prefix(self):
        bi = BytesIO()
        with ZstdFile(bi, 'w', zstd_dict=TRAINED_DICT.as_prefix) as f:
            f.write(SAMPLES[0])
        bi.seek(0)
        with ZstdFile(bi, zstd_dict=TRAINED_DICT.as_prefix) as f:
            dat = f.read()
        self.assertEqual(dat, SAMPLES[0])

    def test_UnsupportedOperation(self):
        # 1
        with ZstdFile(BytesIO(), 'r') as f:
            with self.assertRaises(io.UnsupportedOperation):
                f.write(b'1234')

        # 2
        class T:
            def read(self, size):
                return b'a' * size

        with self.assertRaises(AttributeError): # on close
            with ZstdFile(T(), 'w') as f:
                with self.assertRaises(AttributeError): # on write
                    f.write(b'1234')

        # 3
        with ZstdFile(BytesIO(), 'w') as f:
            with self.assertRaises(io.UnsupportedOperation):
                f.read(100)
            with self.assertRaises(io.UnsupportedOperation):
                f.seek(100)

        self.assertEqual(f.closed, True)
        with self.assertRaises(ValueError):
            f.readable()
        with self.assertRaises(ValueError):
            f.tell()
        with self.assertRaises(ValueError):
            f.read(100)

    def test_read_readinto_readinto1(self):
        lst = []
        with ZstdFile(BytesIO(COMPRESSED_THIS_FILE*5)) as f:
            while True:
                method = random.randint(0, 2)
                size = random.randint(0, 300)

                if method == 0:
                    dat = f.read(size)
                    if not dat and size:
                        break
                    lst.append(dat)
                elif method == 1:
                    ba = bytearray(size)
                    read_size = f.readinto(ba)
                    if read_size == 0 and size:
                        break
                    lst.append(bytes(ba[:read_size]))
                elif method == 2:
                    ba = bytearray(size)
                    read_size = f.readinto1(ba)
                    if read_size == 0 and size:
                        break
                    lst.append(bytes(ba[:read_size]))
        self.assertEqual(b''.join(lst), THIS_FILE_BYTES*5)

    def test_zstdfile_flush(self):
        # closed
        f = ZstdFile(BytesIO(), 'w')
        f.close()
        with self.assertRaises(ValueError):
            f.flush()

        # read
        with ZstdFile(BytesIO(), 'r') as f:
            # does nothing for read-only stream
            f.flush()

        # write
        DAT = b'abcd'
        bi = BytesIO()
        with ZstdFile(bi, 'w') as f:
            self.assertEqual(f.write(DAT), len(DAT))
            self.assertEqual(f.tell(), len(DAT))
            self.assertEqual(bi.tell(), 0) # not enough for a block

            self.assertEqual(f.flush(), None)
            self.assertEqual(f.tell(), len(DAT))
            self.assertGreater(bi.tell(), 0) # flushed

        # write, no .flush() method
        class C:
            def write(self, b):
                return len(b)
        with ZstdFile(C(), 'w') as f:
            self.assertEqual(f.write(DAT), len(DAT))
            self.assertEqual(f.tell(), len(DAT))

            self.assertEqual(f.flush(), None)
            self.assertEqual(f.tell(), len(DAT))

    def test_zstdfile_flush_mode(self):
        self.assertEqual(ZstdFile.FLUSH_BLOCK, ZstdCompressor.FLUSH_BLOCK)
        self.assertEqual(ZstdFile.FLUSH_FRAME, ZstdCompressor.FLUSH_FRAME)
        with self.assertRaises(AttributeError):
            ZstdFile.CONTINUE

        bo = BytesIO()
        with ZstdFile(bo, 'w') as f:
            # flush block
            f.write(b'123')
            self.assertIsNone(f.flush(f.FLUSH_BLOCK))
            p1 = bo.tell()
            # mode == .last_mode, should return
            self.assertIsNone(f.flush())
            p2 = bo.tell()
            self.assertEqual(p1, p2)
            # flush frame
            f.write(b'456')
            self.assertIsNone(f.flush(mode=f.FLUSH_FRAME))
            # flush frame
            f.write(b'789')
            self.assertIsNone(f.flush(f.FLUSH_FRAME))
            p1 = bo.tell()
            # mode == .last_mode, should return
            self.assertIsNone(f.flush(f.FLUSH_FRAME))
            p2 = bo.tell()
            self.assertEqual(p1, p2)
        self.assertEqual(decompress(bo.getvalue()), b'123456789')

        bo = BytesIO()
        with ZstdFile(bo, 'w') as f:
            f.write(b'123')
            with self.assertRaisesRegex(ValueError, r'\.FLUSH_.*?\.FLUSH_'):
                f.flush(ZstdCompressor.CONTINUE)
            with self.assertRaises(ValueError):
                f.flush(-1)
            with self.assertRaises(ValueError):
                f.flush(123456)
            with self.assertRaises(TypeError):
                f.flush(node=ZstdCompressor.CONTINUE)
            with self.assertRaises((TypeError, ValueError)):
                f.flush('FLUSH_FRAME')
            with self.assertRaises(TypeError):
                f.flush(b'456', f.FLUSH_BLOCK)

    def test_zstdfile_truncate(self):
        with ZstdFile(BytesIO(), 'w') as f:
            with self.assertRaises(io.UnsupportedOperation):
                f.truncate(200)

    def test_zstdfile_iter_issue45475(self):
        lines = [l for l in ZstdFile(BytesIO(COMPRESSED_THIS_FILE))]
        self.assertGreater(len(lines), 0)

class OpenTestCase(unittest.TestCase):

    def test_binary_modes(self):
        with open(BytesIO(COMPRESSED_100_PLUS_32KB), "rb") as f:
            self.assertEqual(f.read(), DECOMPRESSED_100_PLUS_32KB)
        with BytesIO() as bio:
            with open(bio, "wb") as f:
                f.write(DECOMPRESSED_100_PLUS_32KB)
            file_data = decompress(bio.getvalue())
            self.assertEqual(file_data, DECOMPRESSED_100_PLUS_32KB)
            with open(bio, "ab") as f:
                f.write(DECOMPRESSED_100_PLUS_32KB)
            file_data = decompress(bio.getvalue())
            self.assertEqual(file_data, DECOMPRESSED_100_PLUS_32KB * 2)

    def test_text_modes(self):
        # empty input
        with open(BytesIO(b''), "rt", encoding="utf-8", newline='\n') as reader:
            for _ in reader:
                pass

        # read
        uncompressed = THIS_FILE_STR.replace(os.linesep, "\n")
        with open(BytesIO(COMPRESSED_THIS_FILE), "rt", encoding="utf-8") as f:
            self.assertEqual(f.read(), uncompressed)

        with BytesIO() as bio:
            # write
            with open(bio, "wt", encoding="utf-8") as f:
                f.write(uncompressed)
            file_data = decompress(bio.getvalue()).decode("utf-8")
            self.assertEqual(file_data.replace(os.linesep, "\n"), uncompressed)
            # append
            with open(bio, "at", encoding="utf-8") as f:
                f.write(uncompressed)
            file_data = decompress(bio.getvalue()).decode("utf-8")
            self.assertEqual(file_data.replace(os.linesep, "\n"), uncompressed * 2)

    def test_bad_params(self):
        with tempfile.NamedTemporaryFile(delete=False) as tmp_f:
            if sys.version_info >= (3, 6):
                TESTFN = pathlib.Path(tmp_f.name)
            else:
                TESTFN = tmp_f.name

        with self.assertRaises(ValueError):
            open(TESTFN, "")
        with self.assertRaises(ValueError):
            open(TESTFN, "rbt")
        with self.assertRaises(ValueError):
            open(TESTFN, "rb", encoding="utf-8")
        with self.assertRaises(ValueError):
            open(TESTFN, "rb", errors="ignore")
        with self.assertRaises(ValueError):
            open(TESTFN, "rb", newline="\n")

        os.remove(TESTFN)

    def test_option(self):
        option = {DParameter.windowLogMax:25}
        with open(BytesIO(COMPRESSED_100_PLUS_32KB), "rb", level_or_option=option) as f:
            self.assertEqual(f.read(), DECOMPRESSED_100_PLUS_32KB)

        option = {CParameter.compressionLevel:12}
        with BytesIO() as bio:
            with open(bio, "wb", level_or_option=option) as f:
                f.write(DECOMPRESSED_100_PLUS_32KB)
            file_data = decompress(bio.getvalue())
            self.assertEqual(file_data, DECOMPRESSED_100_PLUS_32KB)

    def test_encoding(self):
        uncompressed = THIS_FILE_STR.replace(os.linesep, "\n")

        with BytesIO() as bio:
            with open(bio, "wt", encoding="utf-16-le") as f:
                f.write(uncompressed)
            file_data = decompress(bio.getvalue()).decode("utf-16-le")
            self.assertEqual(file_data.replace(os.linesep, "\n"), uncompressed)
            bio.seek(0)
            with open(bio, "rt", encoding="utf-16-le") as f:
                self.assertEqual(f.read().replace(os.linesep, "\n"), uncompressed)

    def test_encoding_error_handler(self):
        with BytesIO(compress(b"foo\xffbar")) as bio:
            with open(bio, "rt", encoding="ascii", errors="ignore") as f:
                self.assertEqual(f.read(), "foobar")

    def test_newline(self):
        # Test with explicit newline (universal newline mode disabled).
        text = THIS_FILE_STR.replace(os.linesep, "\n")
        with BytesIO() as bio:
            with open(bio, "wt", encoding="utf-8", newline="\n") as f:
                f.write(text)
            bio.seek(0)
            with open(bio, "rt", encoding="utf-8", newline="\r") as f:
                self.assertEqual(f.readlines(), [text])

    def test_x_mode(self):
        with tempfile.NamedTemporaryFile(delete=False) as tmp_f:
            if sys.version_info >= (3, 6):
                TESTFN = pathlib.Path(tmp_f.name)
            else:
                TESTFN = tmp_f.name

        for mode in ("x", "xb", "xt"):
            os.remove(TESTFN)

            if mode == "xt":
                encoding = "utf-8"
            else:
                encoding = None
            with open(TESTFN, mode, encoding=encoding):
                pass
            with self.assertRaises(FileExistsError):
                with open(TESTFN, mode):
                    pass

        os.remove(TESTFN)

    def test_open_dict(self):
        # default
        bi = BytesIO()
        with open(bi, 'w', zstd_dict=TRAINED_DICT) as f:
            f.write(SAMPLES[0])
        bi.seek(0)
        with open(bi, zstd_dict=TRAINED_DICT) as f:
            dat = f.read()
        self.assertEqual(dat, SAMPLES[0])

        # .as_(un)digested_dict
        bi = BytesIO()
        with open(bi, 'w', zstd_dict=TRAINED_DICT.as_digested_dict) as f:
            f.write(SAMPLES[0])
        bi.seek(0)
        with open(bi, zstd_dict=TRAINED_DICT.as_undigested_dict) as f:
            dat = f.read()
        self.assertEqual(dat, SAMPLES[0])

        # invalid dictionary
        bi = BytesIO()
        with self.assertRaisesRegex(TypeError, 'zstd_dict'):
            open(bi, 'w', zstd_dict={1:2, 2:3})

        with self.assertRaisesRegex(TypeError, 'zstd_dict'):
            open(bi, 'w', zstd_dict=b'1234567890')

    def test_open_prefix(self):
        bi = BytesIO()
        with open(bi, 'w', zstd_dict=TRAINED_DICT.as_prefix) as f:
            f.write(SAMPLES[0])
        bi.seek(0)
        with open(bi, zstd_dict=TRAINED_DICT.as_prefix) as f:
            dat = f.read()
        self.assertEqual(dat, SAMPLES[0])

    def test_buffer_protocol(self):
        # don't use len() for buffer protocol objects
        arr = array.array("i", range(1000))
        LENGTH = len(arr) * arr.itemsize

        with open(BytesIO(), "wb") as f:
            self.assertEqual(f.write(arr), LENGTH)
            self.assertEqual(f.tell(), LENGTH)

class StreamFunctionsTestCase(unittest.TestCase):

    def test_compress_stream(self):
        bi = BytesIO(THIS_FILE_BYTES)
        bo = BytesIO()
        ret = compress_stream(bi, bo,
                              level_or_option=1, zstd_dict=TRAINED_DICT,
                              pledged_input_size=2**64-1, # backward compatible
                              read_size=200*KB, write_size=200*KB)
        output = bo.getvalue()
        self.assertEqual(ret, (len(THIS_FILE_BYTES), len(output)))
        self.assertEqual(decompress(output, TRAINED_DICT), THIS_FILE_BYTES)
        bi.close()
        bo.close()

        # empty input
        bi = BytesIO()
        bo = BytesIO()
        ret = compress_stream(bi, bo, pledged_input_size=None)
        self.assertEqual(ret, (0, 0))
        self.assertEqual(bo.getvalue(), b'')
        bi.close()
        bo.close()

        # wrong pledged_input_size size
        bi = BytesIO(THIS_FILE_BYTES)
        bo = BytesIO()
        with self.assertRaises(ZstdError):
            compress_stream(bi, bo, pledged_input_size=len(THIS_FILE_BYTES)-1)
        bi.close()
        bo.close()

        bi = BytesIO(THIS_FILE_BYTES)
        bo = BytesIO()
        with self.assertRaises(ZstdError):
            compress_stream(bi, bo, pledged_input_size=len(THIS_FILE_BYTES)+1)
        bi.close()
        bo.close()

        # wrong arguments
        b1 = BytesIO()
        b2 = BytesIO()
        with self.assertRaisesRegex(TypeError, r'input_stream'):
            compress_stream(123, b1)
        with self.assertRaisesRegex(TypeError, r'output_stream'):
            compress_stream(b1, 123)
        with self.assertRaisesRegex(TypeError, r'level_or_option'):
            compress_stream(b1, b2, level_or_option='3')
        with self.assertRaisesRegex(TypeError, r'zstd_dict'):
            compress_stream(b1, b2, zstd_dict={})
        with self.assertRaisesRegex(TypeError, r'zstd_dict'):
            compress_stream(b1, b2, zstd_dict=b'1234567890')
        with self.assertRaisesRegex(ValueError, r'pledged_input_size'):
            compress_stream(b1, b2, pledged_input_size=-1)
        with self.assertRaisesRegex(ValueError, r'pledged_input_size'):
            compress_stream(b1, b2, pledged_input_size=2**64+1)
        with self.assertRaisesRegex(ValueError, r'read_size'):
            compress_stream(b1, b2, read_size=-1)
        with self.assertRaises(OverflowError):
            compress_stream(b1, b2, write_size=2**64+1)
        with self.assertRaisesRegex(TypeError, r'callback'):
            compress_stream(b1, None, callback=None)
        b1.close()
        b2.close()

    def test_compress_stream_callback(self):
        in_lst = []
        out_lst = []
        def func(total_input, total_output, read_data, write_data):
            in_lst.append(read_data.tobytes())
            out_lst.append(write_data.tobytes())

        bi = BytesIO(THIS_FILE_BYTES)
        bo = BytesIO()

        option = {CParameter.compressionLevel : 1,
                  CParameter.checksumFlag : 1}
        ret = compress_stream(bi, bo, level_or_option=option,
                              read_size=701, write_size=101,
                              callback=func)
        bi.close()
        bo.close()

        in_dat = b''.join(in_lst)
        out_dat = b''.join(out_lst)

        self.assertEqual(ret, (len(in_dat), len(out_dat)))
        self.assertEqual(in_dat, THIS_FILE_BYTES)
        self.assertEqual(decompress(out_dat), THIS_FILE_BYTES)

    @unittest.skipIf(not zstd_support_multithread,
                     "zstd build doesn't support multi-threaded compression")
    def test_compress_stream_multi_thread(self):
        size = 40*1024*1024
        b = THIS_FILE_BYTES * (size // len(THIS_FILE_BYTES))
        option = {CParameter.compressionLevel : 1,
                  CParameter.checksumFlag : 1,
                  CParameter.nbWorkers : 2}

        bi = BytesIO(b)
        bo = BytesIO()
        ret = compress_stream(bi, bo, level_or_option=option,
                              pledged_input_size=len(b))
        output = bo.getvalue()
        self.assertEqual(ret, (len(b), len(output)))
        self.assertEqual(decompress(output), b)
        bi.close()
        bo.close()

    def test_decompress_stream(self):
        bi = BytesIO(COMPRESSED_THIS_FILE)
        bo = BytesIO()
        ret = decompress_stream(bi, bo,
                                option={DParameter.windowLogMax:26},
                                read_size=200*KB, write_size=200*KB)
        self.assertEqual(ret, (len(COMPRESSED_THIS_FILE), len(THIS_FILE_BYTES)))
        self.assertEqual(bo.getvalue(), THIS_FILE_BYTES)
        bi.close()
        bo.close()

        # empty input
        bi = BytesIO()
        bo = BytesIO()
        ret = decompress_stream(bi, bo)
        self.assertEqual(ret, (0, 0))
        self.assertEqual(bo.getvalue(), b'')
        bi.close()
        bo.close()

        # wrong arguments
        b1 = BytesIO()
        b2 = BytesIO()
        with self.assertRaisesRegex(TypeError, r'input_stream'):
            decompress_stream(123, b1)
        with self.assertRaisesRegex(TypeError, r'output_stream'):
            decompress_stream(b1, 123)
        with self.assertRaisesRegex(TypeError, r'zstd_dict'):
            decompress_stream(b1, b2, zstd_dict={})
        with self.assertRaisesRegex(TypeError, r'zstd_dict'):
            decompress_stream(b1, b2, zstd_dict=b'1234567890')
        with self.assertRaisesRegex(TypeError, r'option'):
            decompress_stream(b1, b2, option=3)
        with self.assertRaisesRegex(ValueError, r'read_size'):
            decompress_stream(b1, b2, read_size=-1)
        with self.assertRaises(OverflowError):
            decompress_stream(b1, b2, write_size=2**64+1)
        with self.assertRaisesRegex(TypeError, r'callback'):
            decompress_stream(b1, None, callback=None)
        b1.close()
        b2.close()

    def test_decompress_stream_callback(self):
        in_lst = []
        out_lst = []
        def func(total_input, total_output, read_data, write_data):
            in_lst.append(read_data.tobytes())
            out_lst.append(write_data.tobytes())

        bi = BytesIO(COMPRESSED_THIS_FILE)
        bo = BytesIO()

        option = {DParameter.windowLogMax : 26}
        ret = decompress_stream(bi, bo, option=option,
                                read_size=701, write_size=401,
                                callback=func)
        bi.close()
        bo.close()

        in_dat = b''.join(in_lst)
        out_dat = b''.join(out_lst)

        self.assertEqual(ret, (len(in_dat), len(out_dat)))
        self.assertEqual(in_dat, COMPRESSED_THIS_FILE)
        self.assertEqual(out_dat, THIS_FILE_BYTES)

    def test_decompress_stream_multi_frames(self):
        dat = (COMPRESSED_100_PLUS_32KB + SKIPPABLE_FRAME) * 2
        bi = BytesIO(dat)
        bo = BytesIO()
        ret = decompress_stream(bi, bo, read_size=200*KB, write_size=50*KB)
        output = bo.getvalue()
        self.assertEqual(ret, (len(dat), len(output)))
        self.assertEqual(output, DECOMPRESSED_100_PLUS_32KB + DECOMPRESSED_100_PLUS_32KB)
        bi.close()
        bo.close()

        # incomplete frame
        bi = BytesIO(dat[:-1])
        bo = BytesIO()
        with self.assertRaisesRegex(ZstdError, 'incomplete'):
            decompress_stream(bi, bo)
        bi.close()
        bo.close()

    def test_stream_return_wrong_value(self):
        # wrong type
        class M:
            def readinto(self, b):
                return 'a'
            def write(self, b):
                return 'a'

        with self.assertRaises(TypeError):
            compress_stream(M(), BytesIO())
        with self.assertRaises(TypeError):
            decompress_stream(M(), BytesIO())
        with self.assertRaises(TypeError):
            compress_stream(BytesIO(THIS_FILE_BYTES), M())
        with self.assertRaises(TypeError):
            decompress_stream(BytesIO(COMPRESSED_100_PLUS_32KB), M())

        # wrong value
        class N:
            def __init__(self, ret_value):
                self.ret_value = ret_value
            def readinto(self, b):
                return self.ret_value
            def write(self, b):
                return self.ret_value

        # < 0
        with self.assertRaisesRegex(ValueError, r'input_stream.readinto.*?<= \d+'):
            compress_stream(N(-1), BytesIO())
        with self.assertRaisesRegex(ValueError, r'input_stream.readinto.*?<= \d+'):
            decompress_stream(N(-2), BytesIO())
        with self.assertRaisesRegex(ValueError, r'output_stream.write.*?<= \d+'):
            compress_stream(BytesIO(THIS_FILE_BYTES), N(-2))
        with self.assertRaisesRegex(ValueError, r'output_stream.write.*?<= \d+'):
            decompress_stream(BytesIO(COMPRESSED_100_PLUS_32KB), N(-1))

        # should > upper bound (~128 KiB)
        with self.assertRaisesRegex(ValueError, r'input_stream.readinto.*?<= \d+'):
            compress_stream(N(10000000), BytesIO())
        with self.assertRaisesRegex(ValueError, r'input_stream.readinto.*?<= \d+'):
            decompress_stream(N(10000000), BytesIO())
        with self.assertRaisesRegex(ValueError, r'output_stream.write.*?<= \d+'):
            compress_stream(BytesIO(THIS_FILE_BYTES), N(10000000))
        with self.assertRaisesRegex(ValueError, r'output_stream.write.*?<= \d+'):
            decompress_stream(BytesIO(COMPRESSED_100_PLUS_32KB), N(10000000))

    def test_empty_input_no_callback(self):
        def cb(a,b,c,d):
            self.fail('callback function should not be called')
        # callback function will not be called for empty input,
        # it's a promised behavior.
        compress_stream(io.BytesIO(b''), io.BytesIO(), callback=cb)
        decompress_stream(io.BytesIO(b''), io.BytesIO(), callback=cb)

    def test_stream_dict(self):
        zd = ZstdDict(THIS_FILE_BYTES, True)

        # default
        with BytesIO(THIS_FILE_BYTES) as bi, BytesIO() as bo:
            ret = compress_stream(bi, bo, zstd_dict=zd)
            compressed = bo.getvalue()
        self.assertEqual(ret, (len(THIS_FILE_BYTES), len(compressed)))

        with BytesIO(compressed) as bi, BytesIO() as bo:
            ret = decompress_stream(bi, bo, zstd_dict=zd)
            decompressed = bo.getvalue()
        self.assertEqual(ret, (len(compressed), len(decompressed)))
        self.assertEqual(decompressed, THIS_FILE_BYTES)

        # .as_(un)digested_dict
        with BytesIO(THIS_FILE_BYTES) as bi, BytesIO() as bo:
            ret = compress_stream(bi, bo, zstd_dict=zd.as_undigested_dict)
            compressed = bo.getvalue()
        self.assertEqual(ret, (len(THIS_FILE_BYTES), len(compressed)))

        with BytesIO(compressed) as bi, BytesIO() as bo:
            ret = decompress_stream(bi, bo, zstd_dict=zd.as_digested_dict)
            decompressed = bo.getvalue()
        self.assertEqual(ret, (len(compressed), len(decompressed)))
        self.assertEqual(decompressed, THIS_FILE_BYTES)

    def test_stream_prefix(self):
        zd = ZstdDict(THIS_FILE_BYTES, True)

        with BytesIO(THIS_FILE_BYTES) as bi, BytesIO() as bo:
            ret = compress_stream(bi, bo, zstd_dict=zd.as_prefix)
            compressed = bo.getvalue()
        self.assertEqual(ret, (len(THIS_FILE_BYTES), len(compressed)))

        with BytesIO(compressed) as bi, BytesIO() as bo:
            ret = decompress_stream(bi, bo, zstd_dict=zd.as_prefix)
            decompressed = bo.getvalue()
        self.assertEqual(ret, (len(compressed), len(decompressed)))
        self.assertEqual(decompressed, THIS_FILE_BYTES)

class CLITestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tempdir = tempfile.TemporaryDirectory()
        cls.dir_name = cls.tempdir.name

        cls.samples_path = os.path.join(cls.dir_name, 'samples').rstrip(os.sep)
        os.mkdir(cls.samples_path)

        for i, sample in enumerate(SAMPLES):
            file_path = os.path.join(cls.samples_path, str(i) + '.dat')
            with open(file_path, 'wb') as f:
                f.write(sample)

    @classmethod
    def tearDownClass(cls):
        cls.tempdir.cleanup()
        assert not os.path.isdir(cls.dir_name)

    def test_help(self):
        cmd = [sys.executable, '-m', 'pyzstd', '-h']
        result = subprocess.run(cmd, stdout=subprocess.PIPE)
        self.assertIn(b'CLI of pyzstd module', result.stdout)

    def test_sequence(self):
        # train dict
        DICT_PATH = os.path.join(self.dir_name, 'dict')
        DICT_SIZE = 3*1024
        cmd = [sys.executable, '-m', 'pyzstd', '--train',
               self.samples_path + os.sep + '*.dat',
               '-o', DICT_PATH, '--dictID', '1234567',
               '--maxdict', str(DICT_SIZE)]
        result = subprocess.run(cmd, stdout=subprocess.PIPE)
        self.assertRegex(result.stdout,
                         rb'(?s)Training succeeded.*?dict_id=1234567')
        self.assertLessEqual(os.path.getsize(DICT_PATH), DICT_SIZE)

        # compress
        cmd = [sys.executable, '-m', 'pyzstd', '--compress',
               os.path.join(self.samples_path, '1.dat'),
               '--level', '1', '-D', DICT_PATH]
        result = subprocess.run(cmd, stdout=subprocess.PIPE)
        self.assertRegex(result.stdout,
                         rb'output file:.*?1\.dat\.zst[\s\S]*?Compression succeeded')

        # decompress
        cmd = [sys.executable, '-m', 'pyzstd', '--decompress',
               os.path.join(self.samples_path, '1.dat.zst'), '-f',
               '-D', DICT_PATH]
        result = subprocess.run(cmd, stdout=subprocess.PIPE)
        self.assertRegex(result.stdout,
                         rb'output file:.*?1\.dat[\s\S]*?Decompression succeeded')

        # test
        cmd = [sys.executable, '-m', 'pyzstd', '--test',
               os.path.join(self.samples_path, '1.dat.zst'),
               '-D', DICT_PATH]
        result = subprocess.run(cmd, stdout=subprocess.PIPE)
        self.assertRegex(result.stdout,
                         rb'output file: None[\s\S]*?Decompression succeeded')

        # create tar archive
        cmd = [sys.executable, '-m', 'pyzstd',
               '--tar-input-dir', self.samples_path,
               '--level', '1']
        result = subprocess.run(cmd, stdout=subprocess.PIPE)
        self.assertRegex(result.stdout,
                         rb'output file:.*?samples\.tar\.zst[\s\S]*?Archiving succeeded')

        # extract tar archive
        OUTPUT_DIR = os.path.join(self.dir_name, 'tar_output')
        os.mkdir(OUTPUT_DIR)
        cmd = [sys.executable, '-m', 'pyzstd', '--decompress',
               os.path.join(self.dir_name, 'samples.tar.zst'),
               '--tar-output-dir', OUTPUT_DIR]
        result = subprocess.run(cmd, stdout=subprocess.PIPE)
        self.assertIn(b'Extraction succeeded', result.stdout)

    def test_level_range(self):
        OUTPUT_FILE = os.path.join(self.dir_name, 'level_range')
        # default
        cmd = [sys.executable, '-m', 'pyzstd', '--compress',
               os.path.join(self.samples_path, '1.dat'),
               '--output', OUTPUT_FILE, '-f']
        result = subprocess.run(cmd, stdout=subprocess.PIPE)
        self.assertIn(b' - compression level: 3', result.stdout)

        # out of range
        cmd = [sys.executable, '-m', 'pyzstd', '--compress',
               os.path.join(self.samples_path, '1.dat'),
               '--level', str(compressionLevel_values.min - 1),
               '--output', OUTPUT_FILE, '-f']
        result = subprocess.run(cmd,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        self.assertIn(b'--level value should:', result.stderr)

    def test_long_range(self):
        OUTPUT_FILE = os.path.join(self.dir_name, 'long_range')
        # default
        cmd = [sys.executable, '-m', 'pyzstd', '--compress',
               os.path.join(self.samples_path, '1.dat'), '--long',
               '--output', OUTPUT_FILE, '-f']
        result = subprocess.run(cmd, stdout=subprocess.PIPE)
        self.assertIn(b' - long mode: yes, windowLog is 27', result.stdout)

        # out of range
        cmd = [sys.executable, '-m', 'pyzstd', '--compress',
               os.path.join(self.samples_path, '1.dat'),
               '--long', str(CParameter.windowLog.bounds()[1] + 1),
               '--output', OUTPUT_FILE, '-f']
        result = subprocess.run(cmd,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        self.assertRegex(result.stderr,
                         rb'(32|64)-bit build, --long value should:')

    def test_dictID_range(self):
        OUTPUT_FILE = os.path.join(self.dir_name, 'dictid_range')
        cmd = [sys.executable, '-m', 'pyzstd', '--train',
               self.samples_path + os.sep + '*.dat',
               '-o', OUTPUT_FILE, '--dictID', str(0xFFFFFFFF+1)]
        result = subprocess.run(cmd,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        self.assertIn(b'--dictID value should:', result.stderr)

if __name__ == "__main__":
    unittest.main()
