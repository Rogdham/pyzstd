from io import BytesIO
from math import ceil
import io
import os
import tempfile
import unittest

from pyzstd import compress, ZstdCompressor, \
                   SeekableZstdFile, SeekableFormatError
from pyzstd.seekable_zstdfile import SeekTable

DECOMPRESSED = b'1234567890'
assert len(DECOMPRESSED) == 10
COMPRESSED = compress(DECOMPRESSED)

class SeekTableCase(unittest.TestCase):
    def create_table(self, sizes_lst):
        table = SeekTable()
        for item in sizes_lst:
            table.append_entry(*item)
        return table

    def test_case1(self):
        lst = [(9, 10), (9, 10), (9, 10)]
        t = self.create_table(lst)

        self.assertEqual(len(t._frames), len(lst))
        self.assertEqual(t._cumulated_c_size, [9, 18, 27])
        self.assertEqual(t._cumulated_d_size, [10, 20, 30])

        self.assertEqual(t.get_full_c_size(), 27)
        self.assertEqual(t.get_full_d_size(), 30)
        self.assertEqual(t.get_frame_sizes(0), (0, 0))
        self.assertEqual(t.get_frame_sizes(2), (18, 20))

        # find frame index
        self.assertEqual(t.find_seek_frame(-1), 0)
        self.assertEqual(t.find_seek_frame(0), 0)
        self.assertEqual(t.find_seek_frame(1), 0)

        self.assertEqual(t.find_seek_frame(9), 0)
        self.assertEqual(t.find_seek_frame(10), 1)
        self.assertEqual(t.find_seek_frame(11), 1)

        self.assertEqual(t.find_seek_frame(29), 2)
        self.assertEqual(t.find_seek_frame(30), None)
        self.assertEqual(t.find_seek_frame(31), None)

    def test_add_00_entry(self):
        # don't add (0, 0) entry to internal table
        lst = [(9, 10), (0, 0), (0, 0), (9, 10)]
        t = self.create_table(lst)

        self.assertEqual(t._frames, [(9, 10, None), (9, 10, None)])
        self.assertEqual(t._cumulated_c_size, [9, 18])
        self.assertEqual(t._cumulated_d_size, [10, 20])

        self.assertEqual(t.get_full_c_size(), 18)
        self.assertEqual(t.get_full_d_size(), 20)
        self.assertEqual(t.get_frame_sizes(0), (0, 0))
        self.assertEqual(t.get_frame_sizes(1), (9, 10))

        # find frame index
        self.assertEqual(t.find_seek_frame(-1), 0)
        self.assertEqual(t.find_seek_frame(0), 0)
        self.assertEqual(t.find_seek_frame(1), 0)

        self.assertEqual(t.find_seek_frame(9), 0)
        self.assertEqual(t.find_seek_frame(10), 1)
        self.assertEqual(t.find_seek_frame(11), 1)

        self.assertEqual(t.find_seek_frame(19), 1)
        self.assertEqual(t.find_seek_frame(20), None)
        self.assertEqual(t.find_seek_frame(21), None)

    def test_case_empty(self):
        # empty
        lst = []
        t = self.create_table(lst)

        self.assertEqual(len(t._frames), len(lst))
        self.assertEqual(t._cumulated_c_size, [])
        self.assertEqual(t._cumulated_d_size, [])

        self.assertEqual(t.get_full_c_size(), 0)
        self.assertEqual(t.get_full_d_size(), 0)
        self.assertEqual(t.get_frame_sizes(0), (0, 0))

        # find frame index
        self.assertEqual(t.find_seek_frame(-1), None)
        self.assertEqual(t.find_seek_frame(0), None)
        self.assertEqual(t.find_seek_frame(1), None)

    def test_case_0_decompressed_size(self):
        # 0 d_size
        lst = [(9, 10), (9, 0), (9, 10)]
        t = self.create_table(lst)

        self.assertEqual(len(t._frames), len(lst))
        self.assertEqual(t._cumulated_c_size, [9, 18, 27])
        self.assertEqual(t._cumulated_d_size, [10, 10, 20])

        self.assertEqual(t.get_full_c_size(), 27)
        self.assertEqual(t.get_full_d_size(), 20)
        self.assertEqual(t.get_frame_sizes(1), (9, 10))
        self.assertEqual(t.get_frame_sizes(2), (18, 10))

        # find frame index
        self.assertEqual(t.find_seek_frame(9), 0)
        self.assertEqual(t.find_seek_frame(10), 2)
        self.assertEqual(t.find_seek_frame(11), 2)

        self.assertEqual(t.find_seek_frame(19), 2)
        self.assertEqual(t.find_seek_frame(20), None)
        self.assertEqual(t.find_seek_frame(21), None)

    def test_case_0_size_middle(self):
        # 0 size
        lst = [(9, 10), (9, 0), (9, 0), (9, 10)]
        t = self.create_table(lst)

        self.assertEqual(len(t._frames), len(lst))
        self.assertEqual(t._cumulated_c_size, [9, 18, 27, 36])
        self.assertEqual(t._cumulated_d_size, [10, 10, 10, 20])

        self.assertEqual(t.get_full_c_size(), 36)
        self.assertEqual(t.get_full_d_size(), 20)
        self.assertEqual(t.get_frame_sizes(2), (18, 10))

        # find frame index
        self.assertEqual(t.find_seek_frame(9), 0)
        self.assertEqual(t.find_seek_frame(10), 3)
        self.assertEqual(t.find_seek_frame(11), 3)

        self.assertEqual(t.find_seek_frame(19), 3)
        self.assertEqual(t.find_seek_frame(20), None)
        self.assertEqual(t.find_seek_frame(21), None)

    def test_case_0_size_at_begin(self):
        # 0 size at begin
        lst = [(9, 0), (9, 0), (9, 10), (9, 10)]
        t = self.create_table(lst)

        self.assertEqual(len(t._frames), len(lst))
        self.assertEqual(t._cumulated_c_size, [9, 18, 27, 36])
        self.assertEqual(t._cumulated_d_size, [0, 0, 10, 20])

        self.assertEqual(t.get_full_c_size(), 36)
        self.assertEqual(t.get_full_d_size(), 20)
        self.assertEqual(t.get_frame_sizes(0), (0, 0))
        self.assertEqual(t.get_frame_sizes(1), (9, 0))
        self.assertEqual(t.get_frame_sizes(2), (18, 0))
        self.assertEqual(t.get_frame_sizes(3), (27, 10))

        # find frame index
        self.assertEqual(t.find_seek_frame(-1), 2)
        self.assertEqual(t.find_seek_frame(0), 2)
        self.assertEqual(t.find_seek_frame(1), 2)

        self.assertEqual(t.find_seek_frame(9), 2)
        self.assertEqual(t.find_seek_frame(10), 3)
        self.assertEqual(t.find_seek_frame(11), 3)

        self.assertEqual(t.find_seek_frame(19), 3)
        self.assertEqual(t.find_seek_frame(20), None)
        self.assertEqual(t.find_seek_frame(21), None)

    def test_case_0_size_at_end(self):
        # 0 size at end
        lst = [(9, 10), (9, 10), (9, 0), (9, 0)]
        t = self.create_table(lst)

        self.assertEqual(len(t._frames), len(lst))
        self.assertEqual(t._cumulated_c_size, [9, 18, 27, 36])
        self.assertEqual(t._cumulated_d_size, [10, 20, 20, 20])

        self.assertEqual(t.get_full_c_size(), 36)
        self.assertEqual(t.get_full_d_size(), 20)
        self.assertEqual(t.get_frame_sizes(1), (9, 10))
        self.assertEqual(t.get_frame_sizes(2), (18, 20))
        self.assertEqual(t.get_frame_sizes(3), (27, 20))

        # find frame index
        self.assertEqual(t.find_seek_frame(9), 0)
        self.assertEqual(t.find_seek_frame(10), 1)
        self.assertEqual(t.find_seek_frame(11), 1)

        self.assertEqual(t.find_seek_frame(19), 1)
        self.assertEqual(t.find_seek_frame(20), None)
        self.assertEqual(t.find_seek_frame(21), None)

    def test_case_0_size_all(self):
        # 0 size frames
        lst = [(1, 0), (1, 0), (1, 0)]
        t = self.create_table(lst)

        self.assertEqual(len(t._frames), len(lst))
        self.assertEqual(t._cumulated_c_size, [1, 2, 3])
        self.assertEqual(t._cumulated_d_size, [0, 0, 0])

        self.assertEqual(t.get_full_c_size(), 3)
        self.assertEqual(t.get_full_d_size(), 0)
        self.assertEqual(t.get_frame_sizes(0), (0, 0))
        self.assertEqual(t.get_frame_sizes(1), (1, 0))
        self.assertEqual(t.get_frame_sizes(2), (2, 0))

        # find frame index
        self.assertEqual(t.find_seek_frame(-1), None)
        self.assertEqual(t.find_seek_frame(0), None)
        self.assertEqual(t.find_seek_frame(1), None)

    def test_merge_frames1(self):
        lst = [(9, 10), (9, 10), (9, 10),
               (9, 10), (9, 10)]

        t = self.create_table(lst)
        t._merge_frames(1)
        self.assertEqual(len(t), 1)
        self.assertEqual(t._frames[0], (45, 50, None))

        t = self.create_table(lst)
        t._merge_frames(2)
        self.assertEqual(len(t), 2)
        self.assertEqual(t._frames[0], (27, 30, None))
        self.assertEqual(t._frames[1], (18, 20, None))

        t = self.create_table(lst)
        t._merge_frames(3)
        self.assertEqual(len(t), 3)
        self.assertEqual(t._frames[0], (18, 20, None))
        self.assertEqual(t._frames[1], (18, 20, None))
        self.assertEqual(t._frames[2], (9, 10, None))

        t = self.create_table(lst)
        t._merge_frames(4)
        self.assertEqual(len(t), 3)
        self.assertEqual(t._frames[0], (18, 20, None))
        self.assertEqual(t._frames[1], (18, 20, None))
        self.assertEqual(t._frames[2], (9, 10, None))

    def test_merge_frames2(self):
        lst = [(9, 10), (9, 10), (9, 10),
               (9, 10), (9, 10), (9, 10)]

        t = self.create_table(lst)
        t._merge_frames(1)
        self.assertEqual(len(t), 1)
        self.assertEqual(t._frames[0], (54, 60, None))

        t = self.create_table(lst)
        t._merge_frames(2)
        self.assertEqual(len(t), 2)
        self.assertEqual(t._frames[0], (27, 30, None))
        self.assertEqual(t._frames[1], (27, 30, None))

        t = self.create_table(lst)
        t._merge_frames(3)
        self.assertEqual(len(t), 3)
        self.assertEqual(t._frames[0], (18, 20, None))
        self.assertEqual(t._frames[1], (18, 20, None))
        self.assertEqual(t._frames[2], (18, 20, None))

        t = self.create_table(lst)
        t._merge_frames(4)
        self.assertEqual(len(t), 3)
        self.assertEqual(t._frames[0], (18, 20, None))
        self.assertEqual(t._frames[1], (18, 20, None))
        self.assertEqual(t._frames[2], (18, 20, None))

        t = self.create_table(lst)
        t._merge_frames(5)
        self.assertEqual(len(t), 3)
        self.assertEqual(t._frames[0], (18, 20, None))
        self.assertEqual(t._frames[1], (18, 20, None))
        self.assertEqual(t._frames[2], (18, 20, None))

    def test_load_empty(self):
        # empty
        b = BytesIO()
        t = SeekTable()
        t.load_seek_table(b)
        self.assertEqual(len(t), 0)
        self.assertEqual(b.tell(), 0)

    def test_save_load(self):
        # save
        CSIZE = len(COMPRESSED)
        DSIZE = len(DECOMPRESSED)
        lst = [(CSIZE, DSIZE)] * 3
        t = self.create_table(lst)

        b = BytesIO()
        b.write(COMPRESSED*3)
        t.write_seek_table(b)
        b.seek(0)

        # load, seek_to_0=True
        t = SeekTable()
        t.load_seek_table(b, seek_to_0=True)
        self.assertEqual(b.tell(), 0)

        self.assertEqual(len(t._frames), len(lst))
        self.assertEqual(t._cumulated_c_size, [CSIZE, 2*CSIZE, 3*CSIZE])
        self.assertEqual(t._cumulated_d_size, [DSIZE, 2*DSIZE, 3*DSIZE])

        self.assertEqual(t.get_full_c_size(), 3*CSIZE)
        self.assertEqual(t.get_full_d_size(), 3*DSIZE)
        self.assertEqual(t.get_frame_sizes(0), (0, 0))
        self.assertEqual(t.get_frame_sizes(2), (2*CSIZE, 2*DSIZE))

        # load, seek_to_0=False
        t = SeekTable()
        t.load_seek_table(b, seek_to_0=False)
        self.assertEqual(b.tell(), len(b.getvalue()))

    def test_load_bad1(self):
        # 0 < length < 17
        b = BytesIO(b'len<17')
        t = SeekTable()
        with self.assertRaisesRegex(SeekableFormatError,
                                    'size is less than'):
            t.load_seek_table(b)

        # wrong Seekable_Magic_Number
        b = BytesIO()
        b.write(b'a'*18)
        b.write(SeekTable._s_3uint32.pack(1, 0, 0x8F92EAB2))
        b.seek(0)
        t = SeekTable()
        with self.assertRaisesRegex(SeekableFormatError,
                                    'Format Magic Number'):
            t.load_seek_table(b)

        # wrong Seek_Table_Descriptor
        b = BytesIO()
        b.write(b'a'*18)
        b.write(SeekTable._s_footer.pack(1, 0b00010000, 0x8F92EAB1))
        b.seek(0)
        t = SeekTable()
        with self.assertRaisesRegex(SeekableFormatError,
                                    'Reserved_Bits'):
            t.load_seek_table(b)

        # wrong expected size
        b = BytesIO()
        b.write(b'a'*18)
        b.write(SeekTable._s_footer.pack(100, 0b10000000, 0x8F92EAB1))
        b.seek(0)
        t = SeekTable()
        with self.assertRaisesRegex(SeekableFormatError,
                                    'less than expected seek table size'):
            t.load_seek_table(b)

        # wrong Magic_Number
        b = BytesIO()
        b.write(b'a'*18)
        b.write(SeekTable._s_2uint32.pack(0x184D2A5F, 9))
        b.write(SeekTable._s_footer.pack(0, 0b10000000, 0x8F92EAB1))
        b.seek(0)
        t = SeekTable()
        with self.assertRaisesRegex(SeekableFormatError,
                                    'Magic_Number'):
            t.load_seek_table(b)

        # wrong Frame_Size
        b = BytesIO()
        b.write(b'a'*18)
        b.write(SeekTable._s_2uint32.pack(0x184D2A5E, 10))
        b.write(SeekTable._s_footer.pack(0, 0b10000000, 0x8F92EAB1))
        b.seek(0)
        t = SeekTable()
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
        t = SeekTable()
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
        t = SeekTable()
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
        t = SeekTable()
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
        t = SeekTable()
        with self.assertRaisesRegex(SeekableFormatError,
                                    'cumulated compressed size'):
            t.load_seek_table(b)

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

    def test_init_argument(self):
        # not readable
        class C:
            def readable(self):
                return False
            def seekable(self):
                return True
        obj = C()
        with self.assertRaisesRegex(TypeError, 'readable'):
            SeekableZstdFile(obj, 'r')

        # not seekable
        class C:
            def readable(self):
                return True
            def seekable(self):
                return False
        obj = C()
        with self.assertRaisesRegex(TypeError, 'readable'):
            SeekableZstdFile(obj, 'r')

        # append mode
        b = BytesIO(self.two_frames)
        with self.assertRaisesRegex(TypeError,
                                    "Can't accept file object"):
            SeekableZstdFile(b, 'ab')

        # specify max_frame_content_size in reading mode
        with self.assertRaisesRegex(ValueError,
                                    'only valid in writing mode'):
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

    def test_seek(self):
        with SeekableZstdFile(BytesIO(self.two_frames), 'r') as f:
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

    def test_write(self):
        # write
        b = BytesIO()
        with SeekableZstdFile(b, 'w') as f:
            f.write(DECOMPRESSED)
            f.flush(f.FLUSH_BLOCK)
            f.flush(f.FLUSH_FRAME)
            f.write(b'xyz')
            f.flush(f.FLUSH_FRAME)

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
        TAIL = b'XYZ123'

        b = BytesIO()
        with SeekableZstdFile(b, 'w',
                              max_frame_content_size=3) as f:
            f.write(DECOMPRESSED)
            f.flush(f.FLUSH_BLOCK)
            f.flush(f.FLUSH_FRAME)
            f.write(TAIL)
            f.flush(f.FLUSH_FRAME)
        frames_number = ceil(len(DECOMPRESSED + TAIL) / 3)

        b.seek(0)
        with SeekableZstdFile(b, 'r') as f:
            self.assertEqual(f.read(), DECOMPRESSED + TAIL)
            self.assertEqual(len(f._buffer.raw._seek_table), frames_number)

    def test_append_mode(self):
        with tempfile.NamedTemporaryFile(delete=False) as tmp_f:
            filename = tmp_f.name

        # two frames seekable format file
        with open(filename, 'wb') as f:
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

        os.remove(filename)

    def test_bad_append(self):
        # can't accept file object
        with self.assertRaisesRegex(TypeError,
                                    "Can't accept file object"):
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
        obj = C()
        with self.assertRaisesRegex(TypeError, 'readable'):
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

        # seek back for file object
        b = BytesIO(COMPRESSED*3)
        POS = 5
        self.assertEqual(b.seek(POS), POS)
        self.assertEqual(b.tell(), POS)
        self.assertEqual(SeekableZstdFile.is_seekable_format_file(b), False)
        self.assertEqual(b.tell(), POS)

if __name__ == "__main__":
    unittest.main()
