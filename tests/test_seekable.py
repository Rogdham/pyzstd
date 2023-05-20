import array
import io
import os
import pathlib
import random
import re
import sys
import tempfile
import unittest

from io import BytesIO
from math import ceil
from unittest.mock import patch

from pyzstd import *
from pyzstd import PYZSTD_CONFIG # type: ignore
from pyzstd.seekable_zstdfile import SeekTable

BIT_BUILD = PYZSTD_CONFIG[0]
DECOMPRESSED = b'1234567890'
assert len(DECOMPRESSED) == 10
COMPRESSED = compress(DECOMPRESSED)
DICT = ZstdDict(b'a'*1024, is_raw=True)

class SeekTableCase(unittest.TestCase):
    def create_table(self, sizes_lst, read_mode=True):
        table = SeekTable(read_mode)
        for item in sizes_lst:
            table.append_entry(*item)
        return table

    def test_array_append(self):
        # test array('I')
        t = SeekTable(read_mode=False)

        t.append_entry(0xFFFFFFFF, 0)
        # impossible frame
        with self.assertRaises(ValueError):
            t.append_entry(0, 0xFFFFFFFF)

        with self.assertRaises(OverflowError):
            t.append_entry(0xFFFFFFFF+1, 123)
        with self.assertRaises(OverflowError):
            t.append_entry(123, 0xFFFFFFFF+1)
        with self.assertRaises(OverflowError):
            t.append_entry(-1, 123)
        with self.assertRaises(OverflowError):
            t.append_entry(123, -1)

        # test array('Q')
        arr = array.array('Q')
        arr.append(0)
        arr.append(2**64-1)
        self.assertEqual(arr[0], 0)
        self.assertEqual(arr[1], 2**64-1)
        with self.assertRaises(OverflowError):
            arr.append(-1)
        with self.assertRaises(OverflowError):
            arr.append(2**64)

    def test_case1(self):
        lst = [(9, 10), (9, 10), (9, 10)]
        t = self.create_table(lst)

        with self.assertRaises(AttributeError):
            t._frames
        self.assertEqual(t._frames_count, len(lst))
        self.assertEqual(list(t._cumulated_c_size), [0, 9, 18, 27])
        self.assertEqual(list(t._cumulated_d_size), [0, 10, 20, 30])

        self.assertEqual(t.get_full_c_size(), 27)
        self.assertEqual(t.get_full_d_size(), 30)
        self.assertEqual(t.get_frame_sizes(1), (0, 0))
        self.assertEqual(t.get_frame_sizes(2), (9, 10))
        self.assertEqual(t.get_frame_sizes(3), (18, 20))

        # find frame index
        self.assertEqual(t.index_by_dpos(-1), 1)
        self.assertEqual(t.index_by_dpos(0), 1)
        self.assertEqual(t.index_by_dpos(1), 1)

        self.assertEqual(t.index_by_dpos(9), 1)
        self.assertEqual(t.index_by_dpos(10), 2)
        self.assertEqual(t.index_by_dpos(11), 2)

        self.assertEqual(t.index_by_dpos(19), 2)
        self.assertEqual(t.index_by_dpos(20), 3)
        self.assertEqual(t.index_by_dpos(21), 3)

        self.assertEqual(t.index_by_dpos(29), 3)
        self.assertEqual(t.index_by_dpos(30), None)
        self.assertEqual(t.index_by_dpos(31), None)

    def test_add_00_entry(self):
        # don't add (0, 0) entry to internal table
        lst = [(9, 10), (0, 0), (0, 0), (9, 10)]
        t = self.create_table(lst)

        with self.assertRaises(AttributeError):
            t._frames
        self.assertEqual(t._frames_count, 2)
        self.assertEqual(list(t._cumulated_c_size), [0, 9, 18])
        self.assertEqual(list(t._cumulated_d_size), [0, 10, 20])

        self.assertEqual(t.get_full_c_size(), 18)
        self.assertEqual(t.get_full_d_size(), 20)
        self.assertEqual(t.get_frame_sizes(1), (0, 0))
        self.assertEqual(t.get_frame_sizes(2), (9, 10))

        # find frame index
        self.assertEqual(t.index_by_dpos(-1), 1)
        self.assertEqual(t.index_by_dpos(0), 1)
        self.assertEqual(t.index_by_dpos(1), 1)

        self.assertEqual(t.index_by_dpos(9), 1)
        self.assertEqual(t.index_by_dpos(10), 2)
        self.assertEqual(t.index_by_dpos(11), 2)

        self.assertEqual(t.index_by_dpos(19), 2)
        self.assertEqual(t.index_by_dpos(20), None)
        self.assertEqual(t.index_by_dpos(21), None)

    def test_case_empty(self):
        # empty
        lst = []
        t = self.create_table(lst)

        with self.assertRaises(AttributeError):
            t._frames
        self.assertEqual(t._frames_count, 0)
        self.assertEqual(list(t._cumulated_c_size), [0])
        self.assertEqual(list(t._cumulated_d_size), [0])

        self.assertEqual(t.get_full_c_size(), 0)
        self.assertEqual(t.get_full_d_size(), 0)
        self.assertEqual(t.get_frame_sizes(1), (0, 0))

        # find frame index
        self.assertEqual(t.index_by_dpos(-1), None)
        self.assertEqual(t.index_by_dpos(0), None)
        self.assertEqual(t.index_by_dpos(1), None)

    def test_case_0_decompressed_size(self):
        # 0 d_size
        lst = [(9, 10), (9, 0), (9, 10)]
        t = self.create_table(lst)

        with self.assertRaises(AttributeError):
            t._frames
        self.assertEqual(t._frames_count, len(lst))
        self.assertEqual(list(t._cumulated_c_size), [0, 9, 18, 27])
        self.assertEqual(list(t._cumulated_d_size), [0, 10, 10, 20])

        self.assertEqual(t.get_full_c_size(), 27)
        self.assertEqual(t.get_full_d_size(), 20)
        self.assertEqual(t.get_frame_sizes(1), (0, 0))
        self.assertEqual(t.get_frame_sizes(2), (9, 10))
        self.assertEqual(t.get_frame_sizes(3), (18, 10))

        # find frame index
        self.assertEqual(t.index_by_dpos(9), 1)
        self.assertEqual(t.index_by_dpos(10), 3)
        self.assertEqual(t.index_by_dpos(11), 3)

        self.assertEqual(t.index_by_dpos(19), 3)
        self.assertEqual(t.index_by_dpos(20), None)
        self.assertEqual(t.index_by_dpos(21), None)

    def test_case_0_size_middle(self):
        # 0 size
        lst = [(9, 10), (9, 0), (9, 0), (9, 10)]
        t = self.create_table(lst)

        with self.assertRaises(AttributeError):
            t._frames
        self.assertEqual(t._frames_count, len(lst))
        self.assertEqual(list(t._cumulated_c_size), [0, 9, 18, 27, 36])
        self.assertEqual(list(t._cumulated_d_size), [0, 10, 10, 10, 20])

        self.assertEqual(t.get_full_c_size(), 36)
        self.assertEqual(t.get_full_d_size(), 20)
        self.assertEqual(t.get_frame_sizes(1), (0, 0))
        self.assertEqual(t.get_frame_sizes(2), (9, 10))
        self.assertEqual(t.get_frame_sizes(4), (27, 10))

        # find frame index
        self.assertEqual(t.index_by_dpos(9), 1)
        self.assertEqual(t.index_by_dpos(10), 4)
        self.assertEqual(t.index_by_dpos(11), 4)

        self.assertEqual(t.index_by_dpos(19), 4)
        self.assertEqual(t.index_by_dpos(20), None)
        self.assertEqual(t.index_by_dpos(21), None)

    def test_case_0_size_at_begin(self):
        # 0 size at begin
        lst = [(9, 0), (9, 0), (9, 10), (9, 10)]
        t = self.create_table(lst)

        with self.assertRaises(AttributeError):
            t._frames
        self.assertEqual(t._frames_count, len(lst))
        self.assertEqual(list(t._cumulated_c_size), [0, 9, 18, 27, 36])
        self.assertEqual(list(t._cumulated_d_size), [0, 0, 0, 10, 20])

        self.assertEqual(t.get_full_c_size(), 36)
        self.assertEqual(t.get_full_d_size(), 20)
        self.assertEqual(t.get_frame_sizes(1), (0, 0))
        self.assertEqual(t.get_frame_sizes(2), (9, 0))
        self.assertEqual(t.get_frame_sizes(3), (18, 0))
        self.assertEqual(t.get_frame_sizes(4), (27, 10))

        # find frame index
        self.assertEqual(t.index_by_dpos(-1), 3)
        self.assertEqual(t.index_by_dpos(0), 3)
        self.assertEqual(t.index_by_dpos(1), 3)

        self.assertEqual(t.index_by_dpos(9), 3)
        self.assertEqual(t.index_by_dpos(10), 4)
        self.assertEqual(t.index_by_dpos(11), 4)

        self.assertEqual(t.index_by_dpos(19), 4)
        self.assertEqual(t.index_by_dpos(20), None)
        self.assertEqual(t.index_by_dpos(21), None)

    def test_case_0_size_at_end(self):
        # 0 size at end
        lst = [(9, 10), (9, 10), (9, 0), (9, 0)]
        t = self.create_table(lst)

        with self.assertRaises(AttributeError):
            t._frames
        self.assertEqual(t._frames_count, len(lst))
        self.assertEqual(list(t._cumulated_c_size), [0, 9, 18, 27, 36])
        self.assertEqual(list(t._cumulated_d_size), [0, 10, 20, 20, 20])

        self.assertEqual(t.get_full_c_size(), 36)
        self.assertEqual(t.get_full_d_size(), 20)
        self.assertEqual(t.get_frame_sizes(1), (0, 0))
        self.assertEqual(t.get_frame_sizes(2), (9, 10))
        self.assertEqual(t.get_frame_sizes(3), (18, 20))
        self.assertEqual(t.get_frame_sizes(4), (27, 20))

        # find frame index
        self.assertEqual(t.index_by_dpos(9), 1)
        self.assertEqual(t.index_by_dpos(10), 2)
        self.assertEqual(t.index_by_dpos(11), 2)

        self.assertEqual(t.index_by_dpos(19), 2)
        self.assertEqual(t.index_by_dpos(20), None)
        self.assertEqual(t.index_by_dpos(21), None)

    def test_case_0_size_all(self):
        # 0 size frames
        lst = [(1, 0), (1, 0), (1, 0)]
        t = self.create_table(lst)

        with self.assertRaises(AttributeError):
            t._frames
        self.assertEqual(t._frames_count, len(lst))
        self.assertEqual(list(t._cumulated_c_size), [0, 1, 2, 3])
        self.assertEqual(list(t._cumulated_d_size), [0, 0, 0, 0])

        self.assertEqual(t.get_full_c_size(), 3)
        self.assertEqual(t.get_full_d_size(), 0)
        self.assertEqual(t.get_frame_sizes(1), (0, 0))
        self.assertEqual(t.get_frame_sizes(2), (1, 0))
        self.assertEqual(t.get_frame_sizes(3), (2, 0))

        # find frame index
        self.assertEqual(t.index_by_dpos(-1), None)
        self.assertEqual(t.index_by_dpos(0), None)
        self.assertEqual(t.index_by_dpos(1), None)

    def test_merge_frames1(self):
        lst = [(9, 10), (9, 10), (9, 10),
               (9, 10), (9, 10)]

        t = self.create_table(lst, read_mode=False)
        t._merge_frames(1)
        self.assertEqual(len(t), 1)
        self.assertEqual(list(t._frames), [45, 50])

        t = self.create_table(lst, read_mode=False)
        t._merge_frames(2)
        self.assertEqual(len(t), 2)
        self.assertEqual(list(t._frames), [27, 30,
                                           18, 20])

        t = self.create_table(lst, read_mode=False)
        t._merge_frames(3)
        self.assertEqual(len(t), 3)
        self.assertEqual(list(t._frames), [18, 20,
                                           18, 20,
                                           9, 10])

        t = self.create_table(lst, read_mode=False)
        t._merge_frames(4)
        self.assertEqual(len(t), 4)
        self.assertEqual(list(t._frames), [18, 20,
                                           9, 10,
                                           9, 10,
                                           9, 10])

    def test_merge_frames2(self):
        lst = [(9, 10), (9, 10), (9, 10),
               (9, 10), (9, 10), (9, 10)]

        t = self.create_table(lst, read_mode=False)
        t._merge_frames(1)
        self.assertEqual(len(t), 1)
        self.assertEqual(list(t._frames), [54, 60])

        t = self.create_table(lst, read_mode=False)
        t._merge_frames(2)
        self.assertEqual(len(t), 2)
        self.assertEqual(list(t._frames), [27, 30,
                                           27, 30])

        t = self.create_table(lst, read_mode=False)
        t._merge_frames(3)
        self.assertEqual(len(t), 3)
        self.assertEqual(list(t._frames), [18, 20,
                                           18, 20,
                                           18, 20])

        t = self.create_table(lst, read_mode=False)
        t._merge_frames(4)
        self.assertEqual(len(t), 4)
        self.assertEqual(list(t._frames), [18, 20,
                                           18, 20,
                                           9, 10,
                                           9, 10])

        t = self.create_table(lst, read_mode=False)
        t._merge_frames(5)
        self.assertEqual(len(t), 5)
        self.assertEqual(list(t._frames), [18, 20,
                                           9, 10,
                                           9, 10,
                                           9, 10,
                                           9, 10])

    def test_load_empty(self):
        # empty
        b = BytesIO()
        t = SeekTable(read_mode=True)
        t.load_seek_table(b)
        self.assertEqual(len(t), 0)
        self.assertEqual(b.tell(), 0)

    def test_save_load(self):
        # save
        CSIZE = len(COMPRESSED)
        DSIZE = len(DECOMPRESSED)
        lst = [(CSIZE, DSIZE)] * 3
        t = self.create_table(lst, read_mode=False)

        b = BytesIO()
        b.write(COMPRESSED*3)
        t.write_seek_table(b)
        b.seek(0)

        # load, seek_to_0=True
        t = SeekTable(read_mode=True)
        t.load_seek_table(b, seek_to_0=True)
        self.assertEqual(b.tell(), 0)

        with self.assertRaises(AttributeError):
            t._frames
        self.assertEqual(t._frames_count, len(lst))
        self.assertEqual(list(t._cumulated_c_size), [0, CSIZE, 2*CSIZE, 3*CSIZE])
        self.assertEqual(list(t._cumulated_d_size), [0, DSIZE, 2*DSIZE, 3*DSIZE])

        self.assertEqual(t.get_full_c_size(), 3*CSIZE)
        self.assertEqual(t.get_full_d_size(), 3*DSIZE)
        self.assertEqual(t.get_frame_sizes(1), (0, 0))
        self.assertEqual(t.get_frame_sizes(3), (2*CSIZE, 2*DSIZE))

        # load, seek_to_0=False
        t = SeekTable(read_mode=True)
        t.load_seek_table(b, seek_to_0=False)
        self.assertEqual(b.tell(), len(b.getvalue()))

    def test_load_has_checksum(self):
        b = BytesIO()
        b.write(COMPRESSED)
        b.write(COMPRESSED)
        b.write(SeekTable._s_2uint32.pack(0x184D2A5E, 9+2*(4+4+4)))
        b.write(SeekTable._s_3uint32.pack(len(COMPRESSED), len(DECOMPRESSED), 123))
        b.write(SeekTable._s_3uint32.pack(len(COMPRESSED), len(DECOMPRESSED), 456))
        b.write(SeekTable._s_footer.pack(2, 0b10000000, 0x8F92EAB1))

        t = SeekTable(read_mode=True)
        t.load_seek_table(b)
        self.assertTrue(t._has_checksum)
        self.assertEqual(len(t), 2)

    def test_load_bad1(self):
        # 0 < length < 17
        b = BytesIO(b'len<17')
        t = SeekTable(read_mode=True)
        with self.assertRaisesRegex(SeekableFormatError,
                                    'size is less than'):
            t.load_seek_table(b)

        # wrong Seekable_Magic_Number
        b = BytesIO()
        b.write(b'a'*18)
        b.write(SeekTable._s_3uint32.pack(1, 0, 0x8F92EAB2))
        b.seek(0)
        t = SeekTable(read_mode=True)
        with self.assertRaisesRegex(SeekableFormatError,
                                    'Format Magic Number'):
            t.load_seek_table(b)

        # wrong Seek_Table_Descriptor
        b = BytesIO()
        b.write(b'a'*18)
        b.write(SeekTable._s_footer.pack(1, 0b00010000, 0x8F92EAB1))
        b.seek(0)
        t = SeekTable(read_mode=True)
        with self.assertRaisesRegex(SeekableFormatError,
                                    'Reserved_Bits'):
            t.load_seek_table(b)

        # wrong expected size
        b = BytesIO()
        b.write(b'a'*18)
        b.write(SeekTable._s_footer.pack(100, 0b10000000, 0x8F92EAB1))
        b.seek(0)
        t = SeekTable(read_mode=True)
        with self.assertRaisesRegex(SeekableFormatError,
                                    'less than expected seek table size'):
            t.load_seek_table(b)

        # wrong Magic_Number
        b = BytesIO()
        b.write(b'a'*18)
        b.write(SeekTable._s_2uint32.pack(0x184D2A5F, 9))
        b.write(SeekTable._s_footer.pack(0, 0b10000000, 0x8F92EAB1))
        b.seek(0)
        t = SeekTable(read_mode=True)
        with self.assertRaisesRegex(SeekableFormatError,
                                    'Magic_Number'):
            t.load_seek_table(b)

        # wrong Frame_Size
        b = BytesIO()
        b.write(b'a'*18)
        b.write(SeekTable._s_2uint32.pack(0x184D2A5E, 10))
        b.write(SeekTable._s_footer.pack(0, 0b10000000, 0x8F92EAB1))
        b.seek(0)
        t = SeekTable(read_mode=True)
        with self.assertRaisesRegex(SeekableFormatError,
                                    'Frame_Size'):
            t.load_seek_table(b)

    def test_load_bad2(self):
        # wrong Frame_Size
        b = BytesIO()
        b.write(COMPRESSED)
        b.write(SeekTable._s_2uint32.pack(0x184D2A5E, 9+8))
        b.write(SeekTable._s_2uint32.pack(0, len(DECOMPRESSED)))
        b.write(SeekTable._s_footer.pack(1, 0, 0x8F92EAB1))
        b.seek(0)
        t = SeekTable(read_mode=True)
        with self.assertRaisesRegex(SeekableFormatError,
                                    'impossible'):
            t.load_seek_table(b)

        # cumulated compressed size 1
        b = BytesIO()
        b.write(COMPRESSED)
        b.write(SeekTable._s_2uint32.pack(0x184D2A5E, 9+8))
        b.write(SeekTable._s_2uint32.pack(200, len(DECOMPRESSED)))
        b.write(SeekTable._s_footer.pack(1, 0, 0x8F92EAB1))
        b.seek(0)
        t = SeekTable(read_mode=True)
        with self.assertRaisesRegex(SeekableFormatError,
                                    'cumulated compressed size'):
            t.load_seek_table(b)

        # cumulated compressed size 2
        b = BytesIO()
        b.write(COMPRESSED)
        b.write(COMPRESSED)
        b.write(SeekTable._s_2uint32.pack(0x184D2A5E, 9+2*8))
        b.write(SeekTable._s_2uint32.pack(len(COMPRESSED)+1, len(DECOMPRESSED)))
        b.write(SeekTable._s_2uint32.pack(len(COMPRESSED)+1, len(DECOMPRESSED)))
        b.write(SeekTable._s_footer.pack(2, 0, 0x8F92EAB1))
        b.seek(0)
        t = SeekTable(read_mode=True)
        with self.assertRaisesRegex(SeekableFormatError,
                                    'cumulated compressed size'):
            t.load_seek_table(b)

        # cumulated compressed size 3
        b = BytesIO()
        b.write(COMPRESSED)
        b.write(COMPRESSED)
        b.write(SeekTable._s_2uint32.pack(0x184D2A5E, 9+2*8))
        b.write(SeekTable._s_2uint32.pack(len(COMPRESSED)-1, len(DECOMPRESSED)))
        b.write(SeekTable._s_2uint32.pack(len(COMPRESSED)-1, len(DECOMPRESSED)))
        b.write(SeekTable._s_footer.pack(2, 0, 0x8F92EAB1))
        b.seek(0)
        t = SeekTable(read_mode=True)
        with self.assertRaisesRegex(SeekableFormatError,
                                    'cumulated compressed size'):
            t.load_seek_table(b)

    @unittest.skipIf(BIT_BUILD == 32, 'skip in 32-bit build')
    def test_write_table(self):
        class MockError(Exception):
            pass
        class Mock:
            def __len__(self):
                return 0xFFFFFFFF + 1
            def __getitem__(self, key):
                raise MockError
        t = self.create_table([])
        t._frames = Mock()
        try:
            with self.assertWarnsRegex(RuntimeWarning,
                                       '4294967296 entries'):
                t.write_seek_table(BytesIO())
        except MockError:
            pass
        else:
            self.assertTrue(False, 'impossible code path')

class SeekableZstdFileCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        b = BytesIO()
        with SeekableZstdFile(b, 'w') as f:
            pass
        cls.zero_frame = b.getvalue()

        b = BytesIO()
        with SeekableZstdFile(b, 'w') as f:
            f.write(DECOMPRESSED)
        cls.one_frame = b.getvalue()

        b = BytesIO()
        with SeekableZstdFile(b, 'w') as f:
            f.write(DECOMPRESSED)
            f.flush(f.FLUSH_FRAME)
            f.write(DECOMPRESSED)
        cls.two_frames = b.getvalue()

    @staticmethod
    def get_decompressed_sizes_list(dat):
        pos = 0
        lst = []
        while pos < len(dat):
            frame_len = get_frame_size(dat[pos:])
            size = len(decompress(dat[pos:pos+frame_len]))
            lst.append(size)
            pos += frame_len
        return lst

    def test_class_shape(self):
        self.assertEqual(SeekableZstdFile.FLUSH_BLOCK,
                         ZstdCompressor.FLUSH_BLOCK)
        self.assertEqual(SeekableZstdFile.FLUSH_FRAME,
                         ZstdCompressor.FLUSH_FRAME)
        with self.assertRaises(AttributeError):
            SeekableZstdFile.CONTINUE

        self.assertEqual(SeekableZstdFile.FRAME_MAX_C_SIZE,
                         2*1024*1024*1024)
        self.assertEqual(SeekableZstdFile.FRAME_MAX_D_SIZE,
                         1*1024*1024*1024)

        with SeekableZstdFile(BytesIO(self.two_frames), 'r') as f:
            self.assertEqual(type(f.seek_table_info), str)
            self.assertIn('items: 2', f.seek_table_info)
        with SeekableZstdFile(BytesIO(self.two_frames), 'w') as f:
            self.assertEqual(f.write(DECOMPRESSED), len(DECOMPRESSED))
            self.assertEqual(f.flush(f.FLUSH_FRAME), None)
            self.assertEqual(f.write(DECOMPRESSED), len(DECOMPRESSED))
            self.assertEqual(f.flush(f.FLUSH_FRAME), None)
            self.assertIn('items: 2', f.seek_table_info)

    def test_init(self):
        with SeekableZstdFile(BytesIO(self.two_frames)) as f:
            pass
        with SeekableZstdFile(BytesIO(), "w") as f:
            pass
        with SeekableZstdFile(BytesIO(), "x") as f:
            pass
        with self.assertRaisesRegex(TypeError, 'file path'):
            with SeekableZstdFile(BytesIO(), "a") as f:
                pass

        with SeekableZstdFile(BytesIO(), "w", level_or_option=12) as f:
            pass
        with SeekableZstdFile(BytesIO(), "w", level_or_option={CParameter.checksumFlag:1}) as f:
            pass
        with SeekableZstdFile(BytesIO(), "w", level_or_option={}) as f:
            pass
        with SeekableZstdFile(BytesIO(), "w", level_or_option=20, zstd_dict=DICT) as f:
            pass

        with SeekableZstdFile(BytesIO(), "r", level_or_option={DParameter.windowLogMax:25}) as f:
            pass
        with SeekableZstdFile(BytesIO(), "r", level_or_option={}, zstd_dict=DICT) as f:
            pass

    def test_init_with_PathLike_filename(self):
        with tempfile.NamedTemporaryFile(delete=False) as tmp_f:
            if sys.version_info >= (3, 6):
                filename = pathlib.Path(tmp_f.name)
            else:
                filename = tmp_f.name

        with SeekableZstdFile(filename, "a") as f:
            f.write(DECOMPRESSED)
        with SeekableZstdFile(filename) as f:
            self.assertEqual(f.read(), DECOMPRESSED)

        with SeekableZstdFile(filename, "a") as f:
            f.write(DECOMPRESSED)
        with SeekableZstdFile(filename) as f:
            self.assertEqual(f.read(), DECOMPRESSED * 2)

        os.remove(filename)

    def test_init_with_filename(self):
        with tempfile.NamedTemporaryFile(delete=False) as tmp_f:
            if sys.version_info >= (3, 6):
                filename = pathlib.Path(tmp_f.name)
            else:
                filename = tmp_f.name

        with SeekableZstdFile(filename) as f:
            pass
        with SeekableZstdFile(filename, "w") as f:
            pass
        with SeekableZstdFile(filename, "a") as f:
            pass

        os.remove(filename)

    def test_init_mode(self):
        bi = BytesIO()

        with SeekableZstdFile(bi, "r"):
            pass
        with SeekableZstdFile(bi, "rb"):
            pass
        with SeekableZstdFile(bi, "w"):
            pass
        with SeekableZstdFile(bi, "wb"):
            pass
        with self.assertRaisesRegex(TypeError, 'file path'):
            SeekableZstdFile(bi, "a")
        with self.assertRaisesRegex(TypeError, 'file path'):
            SeekableZstdFile(bi, "ab")

    def test_init_with_x_mode(self):
        with tempfile.NamedTemporaryFile() as tmp_f:
            if sys.version_info >= (3, 6):
                filename = pathlib.Path(tmp_f.name)
            else:
                filename = tmp_f.name

        for mode in ("x", "xb"):
            with SeekableZstdFile(filename, mode):
                pass
            with self.assertRaises(FileExistsError):
                with SeekableZstdFile(filename, mode):
                    pass
            os.remove(filename)

    def test_init_bad_mode(self):
        with self.assertRaises(ValueError):
            SeekableZstdFile(BytesIO(COMPRESSED), (3, "x"))
        with self.assertRaises(ValueError):
            SeekableZstdFile(BytesIO(COMPRESSED), "")
        with self.assertRaises(ValueError):
            SeekableZstdFile(BytesIO(COMPRESSED), "xt")
        with self.assertRaises(ValueError):
            SeekableZstdFile(BytesIO(COMPRESSED), "x+")
        with self.assertRaises(ValueError):
            SeekableZstdFile(BytesIO(COMPRESSED), "rx")
        with self.assertRaises(ValueError):
            SeekableZstdFile(BytesIO(COMPRESSED), "wx")
        with self.assertRaises(ValueError):
            SeekableZstdFile(BytesIO(COMPRESSED), "rt")
        with self.assertRaises(ValueError):
            SeekableZstdFile(BytesIO(COMPRESSED), "r+")
        with self.assertRaises(ValueError):
            SeekableZstdFile(BytesIO(COMPRESSED), "wt")
        with self.assertRaises(ValueError):
            SeekableZstdFile(BytesIO(COMPRESSED), "w+")
        with self.assertRaises(ValueError):
            SeekableZstdFile(BytesIO(COMPRESSED), "rw")

        with self.assertRaisesRegex(TypeError, r"NOT be CParameter"):
            SeekableZstdFile(BytesIO(), 'rb', level_or_option={CParameter.compressionLevel:5})
        with self.assertRaisesRegex(TypeError, r"NOT be DParameter"):
            SeekableZstdFile(BytesIO(), 'wb', level_or_option={DParameter.windowLogMax:21})

        with self.assertRaises(TypeError):
            SeekableZstdFile(BytesIO(COMPRESSED), "r", level_or_option=12)

    def test_init_bad_check(self):
        with self.assertRaises(TypeError):
            SeekableZstdFile(BytesIO(), "w", level_or_option='asd')
        # CHECK_UNKNOWN and anything above CHECK_ID_MAX should be invalid.
        with self.assertRaises(ZstdError):
            SeekableZstdFile(BytesIO(), "w", level_or_option={999:9999})
        with self.assertRaises(ZstdError):
            SeekableZstdFile(BytesIO(), "w", level_or_option={CParameter.windowLog:99})

        with self.assertRaises(TypeError):
            SeekableZstdFile(BytesIO(self.two_frames), "r", level_or_option=33)

        with self.assertRaises(ValueError):
            SeekableZstdFile(BytesIO(self.two_frames),
                             level_or_option={DParameter.windowLogMax:2**31})

        with self.assertRaises(ZstdError):
            SeekableZstdFile(BytesIO(self.two_frames),
                             level_or_option={444:333})

        with self.assertRaises(TypeError):
            SeekableZstdFile(BytesIO(self.two_frames), zstd_dict={1:2})

        with self.assertRaises(TypeError):
            SeekableZstdFile(BytesIO(self.two_frames), zstd_dict=b'dict123456')

    def test_init_argument(self):
        # not readable
        class C:
            def readable(self):
                return False
            def seekable(self):
                return True
            def read(self, size=-1):
                return b''
        obj = C()
        with self.assertRaisesRegex(TypeError, 'readable'):
            SeekableZstdFile(obj, 'r')

        # not seekable
        class C:
            def readable(self):
                return True
            def seekable(self):
                return False
            def read(self, size=-1):
                return b''
        obj = C()
        with self.assertRaisesRegex(TypeError, 'seekable'):
            SeekableZstdFile(obj, 'r')

        # append mode
        b = BytesIO(self.two_frames)
        with self.assertRaisesRegex(TypeError,
                                    "can't accept file object"):
            SeekableZstdFile(b, 'ab')

        # specify max_frame_content_size in reading mode
        with self.assertRaisesRegex(ValueError,
                                    'only valid in write modes'):
            SeekableZstdFile(b, 'r', max_frame_content_size=100)

    def test_load(self):
        # empty
        b = BytesIO()
        with SeekableZstdFile(b, 'r') as f:
            self.assertEqual(f.read(10), b'')

        # not a seekable format
        b = BytesIO(COMPRESSED*10)
        with self.assertRaisesRegex(SeekableFormatError,
                                    'Format Magic Number'):
            SeekableZstdFile(b, 'r')

    def test_read(self):
        with SeekableZstdFile(BytesIO(self.zero_frame), 'r') as f:
            self.assertEqual(f.read(), b'')
        with SeekableZstdFile(BytesIO(self.one_frame), 'r') as f:
            self.assertEqual(f.read(), DECOMPRESSED)
        with SeekableZstdFile(BytesIO(self.two_frames), 'r') as f:
            self.assertEqual(f.read(), DECOMPRESSED*2)

        # bad file
        with self.assertRaisesRegex(SeekableFormatError,
                                    'size is less than'):
            SeekableZstdFile(BytesIO(b'1'), 'r')
        with self.assertRaisesRegex(SeekableFormatError,
                                    'The last 4 bytes'):
            SeekableZstdFile(BytesIO(COMPRESSED*30), 'r')

        # write mode
        with SeekableZstdFile(BytesIO(), 'w') as f:
            f.write(DECOMPRESSED)
            with self.assertRaisesRegex(io.UnsupportedOperation,
                                        "File not open for reading"):
                f.read(100)
        # closed
        with self.assertRaisesRegex(ValueError,
                                    "I/O operation on closed file"):
            f.read(100)

    def test_read_empty(self):
        with SeekableZstdFile(BytesIO(b''), 'r') as f:
            self.assertEqual(f.read(), b'')
            self.assertEqual(f.tell(), 0)

            self.assertEqual(f.seek(2), 0)
            self.assertEqual(f.read(), b'')
            self.assertEqual(f.tell(), 0)

            self.assertEqual(f.seek(-2), 0)
            self.assertEqual(f.read(), b'')
            self.assertEqual(f.tell(), 0)

    def test_seek(self):
        with SeekableZstdFile(BytesIO(self.two_frames), 'r') as f:
            # get d size
            self.assertEqual(f.seek(0, io.SEEK_END), len(DECOMPRESSED)*2)
            self.assertEqual(f.tell(), len(DECOMPRESSED)*2)

            self.assertEqual(f.seek(1), 1)
            self.assertEqual(f.read(), DECOMPRESSED[1:]+DECOMPRESSED)
            self.assertEqual(f.seek(-1), 0)
            self.assertEqual(f.read(), DECOMPRESSED*2)
            self.assertEqual(f.seek(9), 9)
            self.assertEqual(f.read(), DECOMPRESSED[9:]+DECOMPRESSED)
            self.assertEqual(f.seek(21), 20)
            self.assertEqual(f.read(), b'')
            self.assertEqual(f.seek(0), 0)
            self.assertEqual(f.read(), DECOMPRESSED*2)
            self.assertEqual(f.seek(20), 20)
            self.assertEqual(f.read(), b'')

    def test_read_not_seekable(self):
        class C:
            def readable(self):
                return True
            def seekable(self):
                return False
            def read(self, size=-1):
                return b''
        obj = C()
        with self.assertRaisesRegex(TypeError, 'using ZstdFile class'):
            SeekableZstdFile(obj, 'r')

    def test_read_fp_not_at_0(self):
        b = BytesIO(self.two_frames)
        b.seek(3)
        # it will seek b to 0
        with SeekableZstdFile(b, 'r') as f:
            self.assertEqual(b.tell(), 0)
            self.assertEqual(f.read(), DECOMPRESSED*2)

    def test_write(self):
        # write
        b = BytesIO()
        with SeekableZstdFile(b, 'w') as f:
            self.assertEqual(f.write(DECOMPRESSED), len(DECOMPRESSED))
            self.assertIsNone(f.flush(f.FLUSH_BLOCK))
            self.assertIsNone(f.flush(f.FLUSH_FRAME))
            self.assertEqual(f.write(b'xyz'), 3)
            f.flush(f.FLUSH_FRAME)
        dat = b.getvalue()
        lst = self.get_decompressed_sizes_list(dat)
        self.assertEqual(lst, [10, 3, 0])

        # read mode
        with SeekableZstdFile(BytesIO(self.two_frames), 'r') as f:
            with self.assertRaisesRegex(io.UnsupportedOperation,
                                        'File not open for writing'):
                f.write(b'1234')
        # closed file
        with self.assertRaisesRegex(ValueError,
                                    'I/O operation on closed file'):
            f.write(b'1234')

        # read
        b.seek(0)
        with SeekableZstdFile(b, 'r') as f:
            self.assertEqual(f.read(), DECOMPRESSED + b'xyz')
            self.assertEqual(len(f._buffer.raw._seek_table), 2)
            with self.assertRaisesRegex(io.UnsupportedOperation,
                                        'File not open for writing'):
                f.write(b'1234')

    def test_write_arg(self):
        b = BytesIO()
        with SeekableZstdFile(b, 'w') as f:
            f.write(DECOMPRESSED)
            f.write(data=b'123')

            with self.assertRaises(TypeError):
                f.write()
            with self.assertRaises(TypeError):
                f.write(0)
            with self.assertRaises(TypeError):
                f.write('123')
            with self.assertRaises(TypeError):
                f.write(b'123', f.FLUSH_BLOCK)
            with self.assertRaises(TypeError):
                f.write(dat=b'123')

    def test_write_empty_frame(self):
        bo = BytesIO()
        with SeekableZstdFile(bo, 'w') as f:
            f.flush(f.FLUSH_FRAME)
        # 17 is a seek table without entry, 4+4+9
        self.assertEqual(len(bo.getvalue()), 17)

        bo = BytesIO()
        with SeekableZstdFile(bo, 'w') as f:
            f.flush(f.FLUSH_FRAME)
            f.flush(f.FLUSH_FRAME)
        # 17 is a seek table without entry, 4+4+9
        self.assertEqual(len(bo.getvalue()), 17)

        # if .write(b''), generate empty content frame
        bo = BytesIO()
        with SeekableZstdFile(bo, 'w') as f:
            f.write(b'')
        # SeekableZstdFile.write() do nothing if length is 0
        self.assertEqual(len(bo.getvalue()), 17)

        # has an empty content frame
        bo = BytesIO()
        with SeekableZstdFile(bo, 'w') as f:
            f.flush(f.FLUSH_BLOCK)
        self.assertGreater(len(bo.getvalue()), 17)

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
        with SeekableZstdFile(bo, 'w') as f:
            f.write(b'123')
            f.flush(f.FLUSH_BLOCK)
            fp_pos = f._fp.tell()
            self.assertNotEqual(fp_pos, 0)
            f.flush(f.FLUSH_BLOCK)
            self.assertEqual(f._fp.tell(), fp_pos)

        # mode != .last_mode
        bo = BytesIO()
        with SeekableZstdFile(bo, 'w') as f:
            f.flush(f.FLUSH_BLOCK)
            self.assertEqual(f._fp.tell(), 0)
            f.write(b'')
            f.flush(f.FLUSH_BLOCK)
            self.assertEqual(f._fp.tell(), 0)

    def test_flush(self):
        b = BytesIO()
        with SeekableZstdFile(b, 'w') as f:
            f.write(DECOMPRESSED)
            self.assertEqual(f.flush(f.FLUSH_BLOCK), None)
            self.assertIn('items: 0\n', f.seek_table_info)
            self.assertEqual(f.flush(mode=f.FLUSH_FRAME), None)
            self.assertIn('items: 1\n', f.seek_table_info)
            f.write(DECOMPRESSED)
            self.assertIn('items: 1\n', f.seek_table_info)
            f.flush(f.FLUSH_FRAME)
            self.assertIn('items: 2\n', f.seek_table_info)

        # closed file
        with self.assertRaisesRegex(ValueError, 'I/O operation'):
            f.flush()
        with self.assertRaisesRegex(ValueError, 'I/O operation'):
            f.flush(f.FLUSH_FRAME)

        # do nothing in reading mode
        b.seek(0)
        with SeekableZstdFile(b, 'r') as f:
            f.flush()
            f.flush(f.FLUSH_FRAME)

    def test_flush_arg(self):
        b = BytesIO()
        with SeekableZstdFile(b, 'w') as f:
            f.flush()
            f.flush(f.FLUSH_BLOCK)
            f.flush(f.FLUSH_FRAME)
            f.flush(mode=f.FLUSH_FRAME)

            with self.assertRaises((TypeError, ValueError)):
                f.flush(b'123')
            with self.assertRaises(TypeError):
                f.flush(b'123', f.FLUSH_BLOCK)
            with self.assertRaises(ValueError):
                f.flush(0) # CONTINUE
            with self.assertRaises(TypeError):
                f.flush(node=f.FLUSH_FRAME)

    def test_close(self):
        with BytesIO(self.two_frames) as src:
            f = SeekableZstdFile(src)
            f.close()
            # SeekableSeekableZstdFile.close() should not close the underlying file object.
            self.assertFalse(src.closed)
            # Try closing an already-closed SeekableZstdFile.
            f.close()
            self.assertFalse(src.closed)

        # Test with a real file on disk, opened directly by SeekableZstdFile.
        with tempfile.NamedTemporaryFile(delete=False) as tmp_f:
            if sys.version_info >= (3, 6):
                filename = pathlib.Path(tmp_f.name)
            else:
                filename = tmp_f.name

        f = SeekableZstdFile(filename)
        fp = f._fp
        f.close()
        # Here, SeekableZstdFile.close() *should* close the underlying file object.
        self.assertTrue(fp.closed)
        # Try closing an already-closed SeekableZstdFile.
        f.close()

        os.remove(filename)

    def test_wrong_max_frame_content_size(self):
        with self.assertRaises(TypeError):
            SeekableZstdFile(BytesIO(), 'w',
                             max_frame_content_size=None)
        with self.assertRaisesRegex(ValueError,
                                    'max_frame_content_size'):
            SeekableZstdFile(BytesIO(), 'w',
                             max_frame_content_size=0)
        with self.assertRaisesRegex(ValueError,
                                    'max_frame_content_size'):
            SeekableZstdFile(BytesIO(), 'w',
                             max_frame_content_size=1*1024*1024*1024+1)

    def test_write_max_content_size(self):
        TAIL = b'12345'

        b = BytesIO()
        with SeekableZstdFile(b, 'w',
                              max_frame_content_size=4) as f:
            self.assertEqual(f.write(DECOMPRESSED), len(DECOMPRESSED))
            self.assertIsNone(f.flush(f.FLUSH_BLOCK))
            self.assertIsNone(f.flush(f.FLUSH_FRAME))
            self.assertEqual(f.write(TAIL), len(TAIL))
            self.assertIsNone(f.flush(f.FLUSH_FRAME))
            self.assertEqual(f.write(DECOMPRESSED+TAIL),
                             len(DECOMPRESSED+TAIL))
        frames = [4, 4, 2,
                  4, 1,
                  4, 4, 4, 3,
                  0]
        self.assertEqual(self.get_decompressed_sizes_list(b.getvalue()),
                         frames)

        b.seek(0)
        with SeekableZstdFile(b, 'r') as f:
            self.assertEqual(f.read(),
                             DECOMPRESSED + TAIL + DECOMPRESSED + TAIL)
            # 1 is the skip table
            self.assertEqual(len(f._buffer.raw._seek_table), len(frames)-1)

    def test_append_mode(self):
        with tempfile.NamedTemporaryFile(delete=False) as tmp_f:
            filename = tmp_f.name

        # two frames seekable format file
        with io.open(filename, 'wb') as f:
            f.write(self.two_frames)

        # append
        with SeekableZstdFile(filename, 'a') as f:
            f.write(DECOMPRESSED)
            f.flush()
            f.write(DECOMPRESSED)
            f.flush(f.FLUSH_FRAME)

        # verify
        with SeekableZstdFile(filename, 'r') as f:
            self.assertEqual(len(f._buffer.raw._seek_table), 3)

            self.assertEqual(f.read(), DECOMPRESSED*4)
            fsize = f.tell()
            self.assertEqual(fsize, 40)

            self.assertEqual(f.seek(fsize-7), fsize-7)
            self.assertEqual(f.read(), DECOMPRESSED[-7:])

            self.assertEqual(f.seek(fsize-15), fsize-15)
            self.assertEqual(f.read(), (DECOMPRESSED*4)[-15:])

        # [frame1, frame2, frame3, seek_table]
        with io.open(filename, 'rb') as f:
            dat = f.read()
        lst = self.get_decompressed_sizes_list(dat)
        self.assertEqual(lst, [10, 10, 20, 0])
        self.assertEqual(decompress(dat), DECOMPRESSED*4)

        os.remove(filename)

    def test_append_not_seekable(self):
        # in append mode, and the file is not seekable, the
        # current seek table frame can't be overwritten.

        # get a temp file name
        with tempfile.NamedTemporaryFile(delete=False) as tmp_f:
            filename = tmp_f.name

        # mock io.open, return False in append mode.
        def mock_open(io_open):
            def get_file(*args, **kwargs):
                f = io_open(*args, **kwargs)
                if len(args) > 1 and args[1] == 'ab':
                    def seekable(*args, **kwargs):
                        return False
                    f.seekable = seekable
                return f
            return get_file

        # append 1
        with patch("io.open", mock_open(io.open)):
            with self.assertWarnsRegex(RuntimeWarning,
                                       (r"at the end of the file "
                                        r"can't be overwritten"
                                        r".*?, 0 bytes")):
                f = SeekableZstdFile(filename, 'a')
            f.write(DECOMPRESSED)
            f.flush(f.FLUSH_FRAME)
            f.write(DECOMPRESSED)
            f.close()

        # append 2
        with patch("io.open", mock_open(io.open)):
            with self.assertWarnsRegex(RuntimeWarning,
                                       (r"at the end of the file "
                                        r"can't be overwritten"
                                        r".*?\d\d+ bytes")):
                f = SeekableZstdFile(filename, 'a')
            f.write(DECOMPRESSED)
            f.close()

        # verify content
        with SeekableZstdFile(filename, 'r') as f:
            self.assertEqual(f.read(), DECOMPRESSED*3)

        # [frame1, frame2, seek_table, frame3, seek_table]
        with io.open(filename, 'rb') as f:
            dat = f.read()
        lst = self.get_decompressed_sizes_list(dat)
        self.assertEqual(lst, [10, 10, 0, 10, 0])
        self.assertEqual(decompress(dat), DECOMPRESSED*3)

        os.remove(filename)

    def test_bad_append(self):
        # can't accept file object
        with self.assertRaisesRegex(TypeError,
                                    "can't accept file object"):
            SeekableZstdFile(BytesIO(self.two_frames), 'ab')

        # two frames NOT seekable format file
        with tempfile.NamedTemporaryFile(delete=False) as tmp_f:
            filename = tmp_f.name
        with open(filename, 'wb') as f:
            f.write(COMPRESSED*2)
        with self.assertRaisesRegex(SeekableFormatError,
                                    'Format Magic Number'):
            SeekableZstdFile(filename, 'a')
        os.remove(filename)

    def test_x_mode(self):
        with tempfile.NamedTemporaryFile() as tmp_f:
            filename = tmp_f.name

        for mode in ("x", "xb"):
            with SeekableZstdFile(filename, mode):
                pass
            with self.assertRaises(FileExistsError):
                with SeekableZstdFile(filename, mode):
                    pass
            os.remove(filename)

    def test_is_seekable_format_file(self):
        # file object
        self.assertEqual(
            SeekableZstdFile.is_seekable_format_file(BytesIO(b'')),
            True)
        self.assertEqual(
            SeekableZstdFile.is_seekable_format_file(BytesIO(self.two_frames)),
            True)
        self.assertEqual(
            SeekableZstdFile.is_seekable_format_file(BytesIO(COMPRESSED)),
            False)
        self.assertEqual(
            SeekableZstdFile.is_seekable_format_file(BytesIO(COMPRESSED*100)),
            False)

        # file path
        with tempfile.NamedTemporaryFile(delete=False) as tmp_f:
            filename = tmp_f.name
        with io.open(filename, 'wb') as f:
            f.write(self.two_frames)

        self.assertEqual(
            SeekableZstdFile.is_seekable_format_file(filename),
            True)
        os.remove(filename)

        # not readable
        class C:
            def readable(self):
                return False
            def seekable(self):
                return True
        obj = C()
        with self.assertRaisesRegex(TypeError, 'readable'):
            SeekableZstdFile.is_seekable_format_file(obj)

        # not seekable
        class C:
            def readable(self):
                return True
            def seekable(self):
                return False
            def read(self, size=-1):
                return b''
        obj = C()
        with self.assertRaisesRegex(TypeError, 'seekable'):
            SeekableZstdFile.is_seekable_format_file(obj)

        # raise exception
        class C:
            def readable(self):
                return True
            def seekable(self):
                return True
            def read(self, size=-1):
                raise OSError
            def seek(self, offset, whence=io.SEEK_SET):
                raise OSError
            def tell(self):
                return 1
        obj = C()
        with self.assertRaises(OSError):
            SeekableZstdFile.is_seekable_format_file(obj)

        # seek back
        b = BytesIO(COMPRESSED*3)
        POS = 5
        self.assertEqual(b.seek(POS), POS)
        self.assertEqual(b.tell(), POS)
        self.assertEqual(SeekableZstdFile.is_seekable_format_file(b), False)
        self.assertEqual(b.tell(), POS)

    def test_skip_large_skippable_frame(self):
        # generate test file, has a 10 MiB skippable frame
        CSIZE = len(COMPRESSED)
        DSIZE = len(DECOMPRESSED)
        _10MiB = 10*1024*1024
        sf = (0x184D2A50).to_bytes(4, byteorder='little') + \
                (_10MiB).to_bytes(4, byteorder='little') + \
                b'a' * _10MiB
        t = SeekTable(read_mode=False)
        t.append_entry(CSIZE, DSIZE)
        t.append_entry(len(sf), 0)
        t.append_entry(CSIZE, DSIZE)

        content = BytesIO()
        content.write(COMPRESSED)
        content.write(sf)
        content.write(COMPRESSED)
        t.write_seek_table(content)
        b = content.getvalue()
        self.assertGreater(len(b), 2*CSIZE + _10MiB)

        # read all
        content.seek(0)
        with ZstdFile(content, 'r') as f:
            self.assertEqual(f.read(), DECOMPRESSED*2)
        with SeekableZstdFile(content, 'r') as f:
            self.assertEqual(f.read(), DECOMPRESSED*2)

        class B(BytesIO):
            def read(self, size=-1):
                if CSIZE + 1024*1024 < self.tell() < CSIZE + _10MiB:
                    raise Exception('should skip the skippable frame')
                return super().read(size)

        # |--data1--|--skippable--|--data2--|
        #           ^P1             ^P2
        with SeekableZstdFile(B(b)) as f:
            t = f._buffer.raw._seek_table
            # to P1
            self.assertEqual(f.read(DSIZE), DECOMPRESSED)
            self.assertEqual(f.tell(), DSIZE)
            self.assertEqual(t.index_by_dpos(DSIZE), 3)
            self.assertLess(f._fp.tell(), 5*1024*1024)

            # new position
            # if new_frame == old_frame and offset >= self._pos and \
            #    c_pos - self._fp.tell() < 1*1024*1024:
            #     pass
            # else:
            #     do_jump
            NEW_POS = DSIZE + 3
            self.assertEqual(t.index_by_dpos(NEW_POS), 3)
            self.assertGreaterEqual(NEW_POS, f.tell())
            c_pos, d_pos = t.get_frame_sizes(3)
            self.assertGreaterEqual(c_pos, _10MiB)
            self.assertEqual(d_pos, DSIZE)
            self.assertGreaterEqual(c_pos - f._fp.tell(),
                                    1024*1024)

            # cross the skippable frame
            self.assertEqual(f.seek(NEW_POS), NEW_POS)
            self.assertGreater(f._fp.tell(), _10MiB)
            self.assertEqual(f.read(), DECOMPRESSED[3:])

    def test_real_data(self):
        _100KiB = 100*1024
        _1MiB = 1*1024*1024
        b = bytes([random.randint(0, 255) for _ in range(128*1024)])
        b *= 8
        self.assertEqual(len(b), _1MiB)

        # write
        bo = BytesIO()
        with SeekableZstdFile(bo, 'w',
                              level_or_option=
                                    {CParameter.compressionLevel:-100000,
                                     CParameter.checksumFlag:1},
                              max_frame_content_size=_100KiB) as f:
            self.assertEqual(f.write(b), len(b))

        # frames
        self.assertEqual(self.get_decompressed_sizes_list(bo.getvalue()),
                         [102400, 102400, 102400, 102400, 102400, 102400,
                          102400, 102400, 102400, 102400, 24576, 0])

        # ZstdFile
        bo.seek(0)
        with ZstdFile(bo, 'r') as f:
            self.assertEqual(f.read(), b)

        # read, automatically seek to 0.
        with SeekableZstdFile(bo, 'r') as f:
            # frames number
            self.assertEqual(len(f._buffer.raw._seek_table), ceil(_1MiB/_100KiB))
            # read 1
            OFFSET1 = 23
            OFFSET2 = 3 * _100KiB + 1234
            self.assertEqual(f.seek(OFFSET1), OFFSET1)
            self.assertEqual(f.seek(OFFSET2, 1), OFFSET1+OFFSET2)
            self.assertEqual(f.tell(), OFFSET1+OFFSET2)
            self.assertEqual(f.read(300),
                             b[OFFSET1+OFFSET2:OFFSET1+OFFSET2+300])
            # > EOF
            self.assertEqual(f.seek(_1MiB+_100KiB), _1MiB)
            self.assertEqual(f.tell(), _1MiB)
            self.assertEqual(f.read(), b'')
            # read 2
            self.assertEqual(f.seek(-123), 0)
            self.assertEqual(f.tell(), 0)
            self.assertEqual(f.read(300), b[:300])
            # readlines
            self.assertEqual(f.seek(-_100KiB, 2), _1MiB-_100KiB)
            self.assertEqual(f.tell(), _1MiB-_100KiB)
            self.assertEqual(f.readlines(),
                             BytesIO(b[-_100KiB:]).readlines())
            # read 3
            self.assertEqual(f.seek(123), 123)
            self.assertEqual(f.tell(), 123)
            self.assertEqual(f.read(_100KiB*2), b[123:123+_100KiB*2])
            # read 4
            self.assertEqual(f.seek(0), 0)
            self.assertEqual(f.tell(), 0)
            self.assertEqual(f.read(), b)

if __name__ == "__main__":
    unittest.main()
