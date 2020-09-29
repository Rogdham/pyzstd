import _compression
from io import BytesIO, UnsupportedOperation, DEFAULT_BUFFER_SIZE
import os
import pathlib
import pickle
import random
import sys
from test import support
import unittest

from test.support import (
    _4G, bigmemtest, run_unittest
)
# from test.support.import_helper import import_module

import pyzstd as zstd
from pyzstd import ZstdCompressor, RichMemZstdCompressor, ZstdDecompressor, ZstdError, \
                 CParameter, DParameter, Strategy, compress, decompress, ZstdDict

COMPRESSED_DAT = compress(b'abcdefg123456' * 1000)
DAT_100_PLUS_32KB = compress(b'a' * (100 + 32*1024))
SKIPPABLE_FRAME = (0x184D2A50).to_bytes(4, byteorder='little') + \
                  (100).to_bytes(4, byteorder='little') + \
                  b'a' * 100

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


def test_main():
    run_unittest(
        CompressorDecompressorTestCase,
        DecompressorFlagsTestCase,
        ClassShapeTestCase,
    )

if __name__ == "__main__":
    test_main()
