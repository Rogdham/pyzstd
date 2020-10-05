import _compression
from io import BytesIO, UnsupportedOperation, DEFAULT_BUFFER_SIZE
import os
import pathlib
import pickle
import random
import sys
import tempfile
from test import support
import unittest

from test.support import (
    _4G, bigmemtest, run_unittest
)
# from test.support.import_helper import import_module

import pyzstd as zstd
from pyzstd import ZstdCompressor, RichMemZstdCompressor, ZstdDecompressor, ZstdError, \
                 CParameter, DParameter, Strategy, compress, richmem_compress, decompress, \
                 ZstdDict, train_dict, finalize_dict, zstd_version, zstd_version_info, \
                 compressionLevel_values, get_frame_info, get_frame_size, ZstdFile

COMPRESSED_DAT = compress(b'abcdefg123456' * 1000)
DAT_100_PLUS_32KB = compress(b'a' * (100 + 32*1024))
DECOMPRESSED_DAT_100_PLUS_32KB = b'a' * (100 + 32*1024)
SKIPPABLE_FRAME = (0x184D2A50).to_bytes(4, byteorder='little') + \
                  (100).to_bytes(4, byteorder='little') + \
                  b'a' * 100

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
        raw_dat = b'12345678abcd'
        default, minv, maxv = compressionLevel_values

        for level in range(max(-20, minv), maxv+1):
            dat1 = compress(raw_dat, level)
            dat2 = decompress(dat1)
            self.assertEqual(dat2, raw_dat)
    
    def test_get_frame_info(self):
        info = get_frame_info(DAT_100_PLUS_32KB[:20])

        self.assertEqual(info.decompressed_size, 32*1024+100)
        self.assertEqual(info.dictionary_id, 0)

    def test_get_frame_size(self):
        size = get_frame_size(DAT_100_PLUS_32KB)

        self.assertEqual(size, len(DAT_100_PLUS_32KB))


class ClassShapeTestCase(unittest.TestCase):

    def test_ZstdCompressor(self):
        # class attributes
        ZstdCompressor.CONTINUE
        ZstdCompressor.FLUSH_BLOCK
        ZstdCompressor.FLUSH_FRAME

        # method & member
        c = ZstdCompressor()
        c.compress(b'123456')
        c.flush()
        c.last_mode
        
        with self.assertRaises(AttributeError):
            c.decompress(b'')

        # read only attribute
        with self.assertRaisesRegex(AttributeError, 'readonly attribute'):
            c.last_mode = ZstdCompressor.FLUSH_BLOCK

        # name
        self.assertIn('.ZstdCompressor', str(type(c)))

        # doesn't support pickle
        with self.assertRaises(TypeError):
            pickle.dumps(c)

        # doesn't support subclass
        with self.assertRaises(TypeError):
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
        c = RichMemZstdCompressor()
        c.compress(b'123456')

        with self.assertRaises(TypeError):
            c.compress(b'123456', ZstdCompressor.FLUSH_FRAME)

        with self.assertRaises(AttributeError):
            c.flush()

        with self.assertRaises(AttributeError):
            c.last_mode

        # name
        self.assertIn('.RichMemZstdCompressor', str(type(c)))

        # doesn't support pickle
        with self.assertRaises(TypeError):
            pickle.dumps(c)

        # doesn't support subclass
        with self.assertRaises(TypeError):
            class SubClass(RichMemZstdCompressor):
                pass

    def test_Decompressor(self):
        # class attributes
        with self.assertRaises(AttributeError):
            ZstdDecompressor.CONTINUE

        with self.assertRaises(AttributeError):
            ZstdDecompressor.FLUSH_BLOCK

        with self.assertRaises(AttributeError):
            ZstdDecompressor.FLUSH_FRAME

        # method & member
        d = ZstdDecompressor()
        d.decompress(b'')
        d.needs_input
        d.at_frame_edge
        
        with self.assertRaises(AttributeError):
            d.compress(b'')

        # read only attributes
        with self.assertRaisesRegex(AttributeError, 'readonly attribute'):
            d.needs_input = True

        with self.assertRaisesRegex(AttributeError, 'readonly attribute'):
            d.at_frame_edge = True

        # name
        self.assertIn('.ZstdDecompressor', str(type(d)))

        # doesn't support pickle
        with self.assertRaises(TypeError):
            pickle.dumps(d)

        # doesn't support subclass
        with self.assertRaises(TypeError):
            class SubClass(ZstdDecompressor):
                pass

    def test_ZstdDict(self):
        zd = ZstdDict(b'12345678')
        self.assertEqual(type(zd.dict_content), bytes)
        self.assertEqual(zd.dict_id, 0)

        # name
        self.assertIn('.ZstdDict', str(type(zd)))

        # doesn't support pickle
        with self.assertRaises(TypeError):
            pickle.dumps(zd)

        # supports subclass
        class SubClass(ZstdDict):
            pass


class CompressorDecompressorTestCase(unittest.TestCase):

    def test_simple_bad_args(self):
        # ZstdCompressor
        self.assertRaises(TypeError, ZstdCompressor, [])
        self.assertRaises(TypeError, ZstdCompressor, level_or_option=3.14)
        self.assertRaises(TypeError, ZstdCompressor, level_or_option='abc')
        self.assertRaises(TypeError, ZstdCompressor, level_or_option=b'abc')

        self.assertRaises(TypeError, ZstdCompressor, zstd_dict=123)
        self.assertRaises(TypeError, ZstdCompressor, zstd_dict=b'abc')
        self.assertRaises(TypeError, ZstdCompressor, zstd_dict={1:2, 3:4})

        self.assertRaises(TypeError, ZstdCompressor, rich_mem='8GB')
        self.assertRaises(TypeError, ZstdCompressor, rich_mem=None)
        self.assertRaises(TypeError, ZstdCompressor, rich_mem={1:2})

        with self.assertRaises(ValueError):
            ZstdCompressor(2**31)
        with self.assertRaises(ValueError):
            ZstdCompressor({2**31 : 100})

        with self.assertRaises(ZstdError):
            ZstdCompressor({CParameter.windowLog:100})
        with self.assertRaises(ZstdError):
            ZstdCompressor({3333 : 100})

        # ZstdDecompressor
        self.assertRaises(TypeError, ZstdDecompressor, ())
        self.assertRaises(TypeError, ZstdDecompressor, zstd_dict=123)
        self.assertRaises(TypeError, ZstdDecompressor, zstd_dict=b'abc')
        self.assertRaises(TypeError, ZstdDecompressor, zstd_dict={1:2, 3:4})

        self.assertRaises(TypeError, ZstdDecompressor, option=123)
        self.assertRaises(TypeError, ZstdDecompressor, option='abc')
        self.assertRaises(TypeError, ZstdDecompressor, option=b'abc')

        with self.assertRaises(ValueError):
            ZstdDecompressor(option={2**31 : 100})

        with self.assertRaises(ZstdError):
            ZstdDecompressor(option={DParameter.windowLogMax:100})
        with self.assertRaises(ZstdError):
            ZstdDecompressor(option={3333 : 100})

        # Method bad arguments
        zc = ZstdCompressor()
        self.assertRaises(TypeError, zc.compress)
        self.assertRaises(TypeError, zc.compress, b"foo", b"bar")
        self.assertRaises(TypeError, zc.compress, "str")
        self.assertRaises(TypeError, zc.flush, b"blah", 1)
        self.assertRaises(ValueError, zc.compress, b"foo", 3)
        empty = zc.flush()

        lzd = ZstdDecompressor()
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

             CParameter.nbWorkers : 0,
             CParameter.jobSize : 50_000,
             CParameter.overlapLog : 9,
             }
        ZstdCompressor(level_or_option=d)

        # larger than signed int, ValueError
        d1 = d.copy()
        d1[CParameter.ldmBucketSizeLog] = 2**31
        self.assertRaises(ValueError, ZstdCompressor, d1)

        # value out of bounds, ZstdError
        d2 = d.copy()
        d2[CParameter.ldmBucketSizeLog] = 10
        self.assertRaises(ZstdError, ZstdCompressor, d2)

    def test_decompress_parameters(self):
        d = {DParameter.windowLogMax : 15}
        ZstdDecompressor(option=d)

        # larger than signed int, ValueError
        d1 = d.copy()
        d1[DParameter.windowLogMax] = 2**31
        self.assertRaises(ValueError, ZstdDecompressor, None, d1)

        # value out of bounds, ZstdError
        d2 = d.copy()
        d2[DParameter.windowLogMax] = 32
        self.assertRaises(ZstdError, ZstdDecompressor, None, d2)

    def test_zstd_multithread_compress(self):
        b = b'test_multithread_123456' * 1_000_000

        dat1 = compress(b, {CParameter.nbWorkers : 2})
        dat2 = decompress(dat1)
        self.assertEqual(dat2, b)

    def test_rich_mem_compress(self):
        b = b'test_rich_mem_123456' * 5_000

        dat1 = richmem_compress(b)
        dat2 = decompress(dat1)
        self.assertEqual(dat2, b)

    def test_rich_mem_compress_warn(self):
        b = b'test_rich_mem_123456' * 5_000

        # warning when multi-threading compression
        with self.assertWarns(ResourceWarning):
            dat1 = richmem_compress(b, {CParameter.nbWorkers:2})

        dat2 = decompress(dat1)
        self.assertEqual(dat2, b)


class DecompressorFlagsTestCase(unittest.TestCase):

    def test_empty_input(self):
        d = ZstdDecompressor()
        self.assertTrue(d.at_frame_edge)
        self.assertTrue(d.needs_input)

        for _ in range(3):
            d.decompress(b'')
            self.assertTrue(d.at_frame_edge)
            self.assertTrue(d.needs_input)

    def test_empty_input_after_frame(self):
        d = ZstdDecompressor()

        # decompress a frame
        d.decompress(COMPRESSED_DAT)
        self.assertTrue(d.at_frame_edge)
        self.assertTrue(d.needs_input)

        # empty input
        d.decompress(b'')
        self.assertTrue(d.at_frame_edge)
        self.assertTrue(d.needs_input)

    def test_empty_input_after_32K_dat(self):
        d = ZstdDecompressor()

        # decompress first 100 bytes
        d.decompress(DAT_100_PLUS_32KB, 100)
        self.assertFalse(d.at_frame_edge)
        self.assertFalse(d.needs_input)

        # decompress the rest
        d.decompress(b'', -1)
        self.assertTrue(d.at_frame_edge)
        self.assertTrue(d.needs_input)

    def test_empty_input_after_32K_dat2(self):
        d = ZstdDecompressor()

        # decompress first 100 bytes
        d.decompress(DAT_100_PLUS_32KB, 100)
        self.assertFalse(d.at_frame_edge)
        self.assertFalse(d.needs_input)

        # decompress the rest
        d.decompress(b'', 32*1024)      # different from above test
        self.assertTrue(d.at_frame_edge)
        self.assertFalse(d.needs_input) # different from above test

        # empty input
        d.decompress(b'')
        self.assertTrue(d.at_frame_edge)
        self.assertTrue(d.needs_input)

    def test_frame_with_epilogue(self):
        # with a checksumFlag
        option = {CParameter.checksumFlag:1}
        data = compress(b'a'*42, option)

        # maxlength = 42
        d = ZstdDecompressor()
        d.decompress(data, 42)
        self.assertTrue(d.at_frame_edge)
        self.assertFalse(d.needs_input)

        d.decompress(b'', 1)
        self.assertTrue(d.at_frame_edge)
        self.assertTrue(d.needs_input)

    def test_multi_frames(self):
        # with a checksumFlag
        option = {CParameter.checksumFlag:1}
        c = ZstdCompressor(option)

        data = c.compress(b'a'*42, c.FLUSH_FRAME)
        data += c.compress(b'b'*60, c.FLUSH_FRAME)

        d = ZstdDecompressor()

        # first frame, two steps
        d.decompress(data, 21)
        self.assertFalse(d.at_frame_edge)
        self.assertFalse(d.needs_input)

        d.decompress(data, 21)
        self.assertTrue(d.at_frame_edge)
        self.assertFalse(d.needs_input)

        # second frame
        d.decompress(b'', 60)
        self.assertTrue(d.at_frame_edge)
        self.assertFalse(d.needs_input)

        d.decompress(b'')
        self.assertTrue(d.at_frame_edge)
        self.assertTrue(d.needs_input)

    def test_skippable_frames(self):
        d = ZstdDecompressor()

        # skippable frame
        output = d.decompress(SKIPPABLE_FRAME)
        self.assertEqual(len(output), 0)
        self.assertTrue(d.at_frame_edge)
        self.assertTrue(d.needs_input)

        # normal frame
        output = d.decompress(DAT_100_PLUS_32KB, 100)
        self.assertEqual(len(output), 100)
        self.assertFalse(d.at_frame_edge)
        self.assertFalse(d.needs_input)

        output = d.decompress(b'', 32*1024)
        self.assertEqual(len(output), 32*1024)
        self.assertTrue(d.at_frame_edge)
        self.assertFalse(d.needs_input)

        # skippable frame
        output = d.decompress(SKIPPABLE_FRAME)
        self.assertEqual(len(output), 0)
        self.assertTrue(d.at_frame_edge)
        self.assertTrue(d.needs_input)

        # skippable frame, two steps
        output = d.decompress(SKIPPABLE_FRAME, len(SKIPPABLE_FRAME)//2)
        self.assertEqual(len(output), 0)
        self.assertTrue(d.at_frame_edge)
        self.assertTrue(d.needs_input)

        output = d.decompress(b'')
        self.assertEqual(len(output), 0)
        self.assertTrue(d.at_frame_edge)
        self.assertTrue(d.needs_input)

        # skippable frame + normal frame
        output = d.decompress(SKIPPABLE_FRAME + COMPRESSED_DAT)
        self.assertGreater(len(output), 0)
        self.assertTrue(d.at_frame_edge)
        self.assertTrue(d.needs_input)


class ZstdDictTestCase(unittest.TestCase):

    def test_dict(self):
        b = b'12345678abcd'
        zd = ZstdDict(b)
        self.assertEqual(zd.dict_content, b)
        self.assertEqual(zd.dict_id, 0)

        # read only attributes
        with self.assertRaisesRegex(AttributeError, 'readonly attribute'):
            zd.dict_content = b

        with self.assertRaisesRegex(AttributeError, 'readonly attribute'):
            zd.dict_id = 10000
    
    def test_train_dict(self):
        # prepare data -----------------------------
        colors = [b'red', b'green', b'yellow', b'black', b'withe', b'blue',
                  b'lilac', b'purple', b'navy', b'glod', b'silver', b'olive']
        lst = []
        for i in range(1200):
            sample  = b'%s = %d\n' % (random.choice(colors), random.randrange(100))
            sample += b'%s = %d\n' % (random.choice(colors), random.randrange(100))
            sample += b'%s = %d\n' % (random.choice(colors), random.randrange(100))
            sample += b'%s = %d' % (random.choice(colors), random.randrange(100))
            lst.append(sample)

        # train zstd dict -----------------------------
        DICT_SIZE1 = 100*1024
        dic1 = zstd.train_dict(lst, DICT_SIZE1)

        self.assertGreater(len(dic1.dict_content), 0)
        self.assertLessEqual(len(dic1.dict_content), DICT_SIZE1)

        # compress/decompress
        for sample in lst:
            dat1 = compress(sample, zstd_dict=dic1)
            dat2 = decompress(dat1, dic1)
            self.assertEqual(sample, dat2)

        # finalize_dict -----------------------------
        if zstd_version_info < (1, 4, 5):
            return

        DICT_SIZE2 = 80*1024
        dic2 = finalize_dict(dic1, lst, DICT_SIZE2, 10)

        self.assertGreater(len(dic2.dict_content), 0)
        self.assertLessEqual(len(dic2.dict_content), DICT_SIZE2)

        # compress/decompress
        for sample in lst:
            dat1 = compress(sample, zstd_dict=dic2)
            dat2 = decompress(dat1, dic2)
            self.assertEqual(sample, dat2)


class FileTestCase(unittest.TestCase):

    def test_init(self):
        with ZstdFile(BytesIO(DAT_100_PLUS_32KB)) as f:
            pass
        with ZstdFile(BytesIO(), "w") as f:
            pass
        with ZstdFile(BytesIO(), "x") as f:
            pass
        with ZstdFile(BytesIO(), "a") as f:
            pass

    def test_init_with_PathLike_filename(self):
        with tempfile.NamedTemporaryFile(delete=False) as tmp_f:
            filename = pathlib.Path(tmp_f.name)

        with ZstdFile(filename, "a") as f:
            f.write(DECOMPRESSED_DAT_100_PLUS_32KB)
        with ZstdFile(filename) as f:
            self.assertEqual(f.read(), DECOMPRESSED_DAT_100_PLUS_32KB)

        with ZstdFile(filename, "a") as f:
            f.write(DECOMPRESSED_DAT_100_PLUS_32KB)
        with ZstdFile(filename) as f:
            self.assertEqual(f.read(), DECOMPRESSED_DAT_100_PLUS_32KB * 2)

        os.remove(filename)

    def test_init_with_filename(self):
        with tempfile.NamedTemporaryFile(delete=False) as tmp_f:
            filename = pathlib.Path(tmp_f.name)
            
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
            filename = pathlib.Path(tmp_f.name)
            
        for mode in ("x", "xb"):
            with ZstdFile(filename, mode):
                pass
            with self.assertRaises(FileExistsError):
                with ZstdFile(filename, mode):
                    pass
            os.remove(filename)

    def test_init_bad_mode(self):
        with self.assertRaises(ValueError):
            ZstdFile(BytesIO(DAT_100_PLUS_32KB), (3, "x"))
        with self.assertRaises(ValueError):
            ZstdFile(BytesIO(DAT_100_PLUS_32KB), "")
        with self.assertRaises(ValueError):
            ZstdFile(BytesIO(DAT_100_PLUS_32KB), "xt")
        with self.assertRaises(ValueError):
            ZstdFile(BytesIO(DAT_100_PLUS_32KB), "x+")
        with self.assertRaises(ValueError):
            ZstdFile(BytesIO(DAT_100_PLUS_32KB), "rx")
        with self.assertRaises(ValueError):
            ZstdFile(BytesIO(DAT_100_PLUS_32KB), "wx")
        with self.assertRaises(ValueError):
            ZstdFile(BytesIO(DAT_100_PLUS_32KB), "rt")
        with self.assertRaises(ValueError):
            ZstdFile(BytesIO(DAT_100_PLUS_32KB), "r+")
        with self.assertRaises(ValueError):
            ZstdFile(BytesIO(DAT_100_PLUS_32KB), "wt")
        with self.assertRaises(ValueError):
            ZstdFile(BytesIO(DAT_100_PLUS_32KB), "w+")
        with self.assertRaises(ValueError):
            ZstdFile(BytesIO(DAT_100_PLUS_32KB), "rw")

    def test_init_bad_check(self):
        with self.assertRaises(TypeError):
            ZstdFile(BytesIO(), "w", level_or_option='asd')
        # CHECK_UNKNOWN and anything above CHECK_ID_MAX should be invalid.
        with self.assertRaises(ZstdError):
            ZstdFile(BytesIO(), "w", level_or_option={999:9999})
        with self.assertRaises(ZstdError):
            ZstdFile(BytesIO(), "w", level_or_option={CParameter.windowLog:99})

        with self.assertRaises(TypeError):
            ZstdFile(BytesIO(DAT_100_PLUS_32KB), "r", level_or_option=33)

        with self.assertRaises(ValueError):
            ZstdFile(BytesIO(DAT_100_PLUS_32KB),
                             level_or_option={DParameter.windowLogMax:2**31})

        with self.assertRaises(ZstdError):
            ZstdFile(BytesIO(DAT_100_PLUS_32KB), 
                             level_or_option={444:333})

        with self.assertRaises(TypeError):
            ZstdFile(BytesIO(DAT_100_PLUS_32KB), zstd_dict={1:2})

        with self.assertRaises(TypeError):
            ZstdFile(BytesIO(DAT_100_PLUS_32KB), zstd_dict=b'dict123456')


    def test_close(self):
        with BytesIO(DAT_100_PLUS_32KB) as src:
            f = ZstdFile(src)
            f.close()
            # ZstdFile.close() should not close the underlying file object.
            self.assertFalse(src.closed)
            # Try closing an already-closed ZstdFile.
            f.close()
            self.assertFalse(src.closed)

        # Test with a real file on disk, opened directly by LZMAFile.
        with tempfile.NamedTemporaryFile(delete=False) as tmp_f:
            filename = pathlib.Path(tmp_f.name)

        f = ZstdFile(filename)
        fp = f._fp
        f.close()
        # Here, ZstdFile.close() *should* close the underlying file object.
        self.assertTrue(fp.closed)
        # Try closing an already-closed ZstdFile.
        f.close()

        os.remove(filename)

    def test_closed(self):
        f = ZstdFile(BytesIO(DAT_100_PLUS_32KB))
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
        f = ZstdFile(BytesIO(DAT_100_PLUS_32KB))
        try:
            self.assertRaises(UnsupportedOperation, f.fileno)
        finally:
            f.close()
        self.assertRaises(ValueError, f.fileno)

        # 2
        with tempfile.NamedTemporaryFile(delete=False) as tmp_f:
            filename = pathlib.Path(tmp_f.name)

        f = ZstdFile(filename)
        try:
            self.assertEqual(f.fileno(), f._fp.fileno())
            self.assertIsInstance(f.fileno(), int)
        finally:
            f.close()
        self.assertRaises(ValueError, f.fileno)

        os.remove(filename)


def test_main():
    run_unittest(
        FunctionsTestCase,
        ClassShapeTestCase,
        CompressorDecompressorTestCase,
        DecompressorFlagsTestCase,
        ZstdDictTestCase,
        FileTestCase,
    )

if __name__ == "__main__":
    test_main()
