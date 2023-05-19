from array import array
from bisect import bisect_right
from struct import Struct
from warnings import warn

from pyzstd.zstdfile import ZstdDecompressReader, ZstdFile, \
                            _MODE_CLOSED, _MODE_READ, _MODE_WRITE, \
                            PathLike, io

__all__ = ('SeekableFormatError', 'SeekableZstdFile')

class SeekableFormatError(Exception):
    '''An error related to Zstandard Seekable Format.'''
    pass

__doc__ = '''\
Zstandard Seekable Format (Ver 0.1.0, 2017 Apr)
All numeric fields are little-endian unless specified otherwise.
A. Seek table is a skippable frame at the end of file:
     Magic_Number  Frame_Size  [Seek_Table_Entries]  Seek_Table_Footer
     4 bytes       4 bytes     8-12 bytes each       9 bytes
     Magic_Number must be 0x184D2A5E.
B. Seek_Table_Entries:
     Compressed_Size  Decompressed_Size  [Checksum]
     4 bytes          4 bytes            4 bytes
     Checksum is optional.
C. Seek_Table_Footer:
     Number_Of_Frames  Seek_Table_Descriptor  Seekable_Magic_Number
     4 bytes           1 byte                 4 bytes
     Seekable_Magic_Number must be 0x8F92EAB1.
D. Seek_Table_Descriptor:
     Bit_number  Field_name
     7           Checksum_Flag
     6-2         Reserved_Bits  (should ensure they are set to 0)
     1-0         Unused_Bits    (should not interpret these bits)'''
__format_version__ = '0.1.0'

class SeekTable:
    _s_footer  = Struct('<IBI')
    _s_2uint32 = Struct('<II')
    _s_3uint32 = Struct('<III')

    def __init__(self, read_mode=True):
        self._read_mode = read_mode
        self._clear_seek_table()

    def _clear_seek_table(self):
        self._has_checksum = False
        # Size of the seek table frame, used for append mode.
        self._seek_frame_size = 0

        self._frames_count = 0
        self._full_c_size = 0
        self._full_d_size = 0

        if self._read_mode:
            # Array item: cumulated_size. Q is uint64_t.
            self._cumulated_c_size = array('Q')
            self._cumulated_d_size = array('Q')
        else:
            # Array item: (c_size1, d_size1,
            #              c_size2, d_size2,
            #              c_size3, d_size2,
            #              ...)
            # I is uint32_t.
            self._frames = array('I')

    def append_entry(self, compressed_size, decompressed_size):
        if compressed_size == 0:
            if decompressed_size == 0:
                # (0, 0) frame is no sense
                return
            else:
                # Impossible frame
                raise ValueError

        if self._read_mode:
            # Cumulated compressed size
            if self._cumulated_c_size:
                self._cumulated_c_size.append(self._cumulated_c_size[-1] + \
                                              compressed_size)
            else:
                self._cumulated_c_size.append(compressed_size)

            # Cumulated decompressed size
            if self._cumulated_d_size:
                self._cumulated_d_size.append(self._cumulated_d_size[-1] + \
                                              decompressed_size)
            else:
                self._cumulated_d_size.append(decompressed_size)
        else:
            self._frames.append(compressed_size)
            self._frames.append(decompressed_size)

        self._frames_count += 1
        self._full_c_size += compressed_size
        self._full_d_size += decompressed_size

    def load_seek_table(self, fp, seek_to_0=True):
        # Check fp readable/seekable
        if not (hasattr(fp, 'readable') and hasattr(fp, "seekable")):
            raise TypeError(
                'The file object should have .readable()/.seekable() methods.')
        if not fp.readable():
            raise TypeError(
                ('To load the seek table of "Zstandard Seekable Format", '
                 'the file object should be readable.'))
        if not fp.seekable():
            raise TypeError(
                ("To load the seek table of \"Zstandard Seekable Format\", "
                 "the file object should be seekable. In SeekableZstdFile's "
                 "reading mode, the file object must be seekable. If the "
                 "file object is not seekable, it can be read sequentially "
                 "using ZstdFile class."))

        # Get file size
        fsize = fp.seek(0, 2) # 2 is SEEK_END
        if fsize == 0:
            return
        elif fsize < 17: # 17=4+4+9
            msg = ('File size is less than the minimal size '
                   '(17 bytes) of "Zstandard Seekable Format".')
            raise SeekableFormatError(msg)

        # Read footer
        fp.seek(-9, 2) # 2 is SEEK_END
        footer = fp.read(9)
        frames_number, descriptor, magic_number = self._s_footer.unpack(footer)
        # Check format
        if magic_number != 0x8F92EAB1:
            msg = (r'The last 4 bytes of the file is not Zstandard '
                   r'Seekable Format Magic Number (b"\xb1\xea\x92\x8f)". '
                   r'SeekableZstdFile class only supports "Zstandard '
                   r'Seekable Format" file or 0-size file. To read a '
                   r'zstd file that is not in "Zstandard Seekable '
                   r'Format", use ZstdFile class.')
            raise SeekableFormatError(msg)

        # Seek_Table_Descriptor
        self._has_checksum = \
           descriptor & 0b10000000
        if descriptor & 0b01111100:
            msg = ('In "Zstandard Seekable Format" version %s, the '
                   'Reserved_Bits in Seek_Table_Descriptor must be 0.') \
                    % __format_version__
            raise SeekableFormatError(msg)

        # Frame size
        entry_size = 12 if self._has_checksum else 8
        skippable_frame_size = 17 + frames_number * entry_size
        if skippable_frame_size > fsize:
            msg = 'File size is less than expected seek table size.'
            raise SeekableFormatError(msg)

        # Read seek table
        fp.seek(-skippable_frame_size, 2) # 2 is SEEK_END
        skippable_frame = fp.read(skippable_frame_size)
        skippable_magic_number, content_size = \
                self._s_2uint32.unpack_from(skippable_frame, 0)

        # Check format
        if skippable_magic_number != 0x184D2A5E:
            msg = "Seek table frame's Magic_Number is wrong."
            raise SeekableFormatError(msg)
        if content_size != skippable_frame_size - 8:
            msg = "Seek table frame's Frame_Size is wrong."
            raise SeekableFormatError(msg)

        # For reading mode, seeking to 0 is necessary.
        # No more fp operations.
        if seek_to_0:
            fp.seek(0)

        # Parse seek table
        offset = 8
        for idx in range(frames_number):
            if self._has_checksum:
                compressed_size, decompressed_size, checksum = \
                    self._s_3uint32.unpack_from(skippable_frame, offset)
                offset += 12
            else:
                compressed_size, decompressed_size = \
                    self._s_2uint32.unpack_from(skippable_frame, offset)
                offset += 8

            # Check format
            if compressed_size == 0 and decompressed_size != 0:
                msg = ('Wrong seek table. The index %d frame (0-based) '
                       'is 0 size, but decompressed size is non-zero, '
                       'this is impossible.') % idx
                raise SeekableFormatError(msg)

            # Append to seek table
            self.append_entry(compressed_size, decompressed_size)

            # Check format
            if self._full_c_size > fsize - skippable_frame_size:
                msg = ('Wrong seek table. Since index %d frame (0-based), '
                       'the cumulated compressed size is greater than '
                       'file size.') % idx
                raise SeekableFormatError(msg)

        # Check format
        if self._full_c_size != fsize - skippable_frame_size:
            raise SeekableFormatError('The cumulated compressed size is wrong')

        # Parsed successfully, save for future use.
        self._seek_frame_size = skippable_frame_size

    # Find frame index by decompressed position
    def index_by_dpos(self, pos):
        # This is necessary when 0 decompressed_size
        # frames are at the beginning
        if pos < 0:
            pos = 0

        i = bisect_right(self._cumulated_d_size, pos)
        if i != self._frames_count:
            return i
        else:
            # None means at EOF
            return None

    def get_full_c_size(self):
        return self._full_c_size

    def get_full_d_size(self):
        return self._full_d_size

    def get_frame_sizes(self, i):
        if i > 0:
            return (self._cumulated_c_size[i-1],
                    self._cumulated_d_size[i-1])
        else:
            return 0, 0

    def _merge_frames(self, max_frames):
        if self._frames_count <= max_frames:
            return

        # Clear the table
        arr = self._frames
        a, b = divmod(self._frames_count, max_frames)
        self._clear_seek_table()

        # Merge frames
        pos = 0
        for i in range(max_frames):
            # Slice length
            length = (a + (1 if i < b else 0)) * 2

            # Merge
            c_size = 0
            d_size = 0
            for j in range(pos, pos+length, 2):
                c_size += arr[j]
                d_size += arr[j+1]
            self.append_entry(c_size, d_size)

            pos += length

    def write_seek_table(self, fp):
        # Exceeded format limit
        if self._frames_count > 0xFFFFFFFF:
            # Emit a warning
            warn(('The seek table of "Zstandard Seekable Format" '
                  'has %d entries, which exceeds the maximum value '
                  'allowed by the format (0xFFFFFFFF). The entries '
                  'will be merged into 0xFFFFFFFF entries, this may '
                  'reduce seeking performance.') % self._frames_count,
                 RuntimeWarning, 3)

            # Merge frames
            self._merge_frames(0xFFFFFFFF)

        # The skippable frame
        offset = 0
        size = 17 + 8 * self._frames_count
        ba = bytearray(size)

        # Header
        self._s_2uint32.pack_into(ba, offset, 0x184D2A5E, size-8)
        offset += 8
        # Entries
        for i in range(0, len(self._frames), 2):
            self._s_2uint32.pack_into(ba, offset,
                                      self._frames[i],
                                      self._frames[i+1])
            offset += 8
        # Footer
        self._s_footer.pack_into(ba, offset,
                                 self._frames_count, 0, 0x8F92EAB1)

        # Write
        fp.write(ba)

    @property
    def seek_frame_size(self):
        return self._seek_frame_size

    def __len__(self):
        return self._frames_count

    def get_info(self):
        return ('Seek table:\n'
                ' - items: {}\n'
                ' - has checksum: {}').format(
                    self._frames_count,
                    bool(self._has_checksum))

_32_KiB = 32*1024
class SeekableDecompressReader(ZstdDecompressReader):
    def __init__(self, fp, decomp_factory, trailing_error=(), **decomp_args):
        self._seek_table = SeekTable(read_mode=True)
        self._seek_table.load_seek_table(fp, seek_to_0=True)

        super().__init__(fp, decomp_factory, trailing_error, **decomp_args)
        self._size = self._seek_table.get_full_d_size()

    # The parent class returns self._fp.seekable().
    # In .__init__() method, seekable has been checked in load_seek_table().
    # BufferedReader.seek() checks this in each invoke, if self._fp.seekable()
    # becomes False at runtime, .seek() method just raise OSError instead of
    # io.UnsupportedOperation.
    def seekable(self):
        return True

    # If the new position is within BufferedReader's buffer,
    # this method may not be called.
    def seek(self, offset, whence=0):
        # Recalculate offset as an absolute file position.
        # If offset < 0 or offset >= EOF, the code can handle them correctly.
        if whence == 0:   # SEEK_SET
            pass
        elif whence == 1: # SEEK_CUR
            offset = self._pos + offset
        elif whence == 2: # SEEK_END
            offset = self._size + offset
        else:
            raise ValueError("Invalid value for whence: {}".format(whence))

        # Get new frame index
        new_frame = self._seek_table.index_by_dpos(offset)
        # offset >= EOF
        if new_frame is None:
            self._eof = True
            self._pos = self._size
            self._fp.seek(self._seek_table.get_full_c_size())
            return self._pos

        # Prepare to jump
        old_frame = self._seek_table.index_by_dpos(self._pos)
        c_pos, d_pos = self._seek_table.get_frame_sizes(new_frame)

        # If at P1, and the skippable frame is large, seeking to P2
        # will unnecessarily read the entire skippable frame. So skip
        # large skippable frame (>= 1 MiB).
        #       |--data1--|--skippable--|--data2--|
        # cpos:             ^P1
        # dpos:           ^P1             ^P2
        if new_frame == old_frame and offset >= self._pos and \
           c_pos - self._fp.tell() < 1*1024*1024:
            pass
        else:
            self._eof = False
            self._pos = d_pos
            self._fp.seek(c_pos)
            self._decompressor = self._decomp_factory(**self._decomp_args)

        # Read and discard data until we reach the desired position.
        # If offset < 0, do nothing.
        offset -= self._pos
        while offset > 0:
            data = self.read(min(_32_KiB, offset))
            if not data:
                break
            offset -= len(data)

        return self._pos

    def get_seek_table_info(self):
        return self._seek_table.get_info()

# Compared to ZstdFile class, it's important to handle the seekable
# of underlying file object carefully. Need to check seekable in
# each situation. For example, there may be a CD-R file system that
# is seekable when reading, but not seekable when appending.
class SeekableZstdFile(ZstdFile):
    """This class can only create/write/read Zstandard Seekable Format file,
    or read 0-size file.
    It provides relatively fast seeking ability in read mode.
    """
    # The format uses uint32_t for compressed/decompressed sizes. If flush
    # block a lot, compressed_size may exceed the limit, so set a max size.
    FRAME_MAX_C_SIZE = 2*1024*1024*1024
    # Zstd seekable format's example code also use 1GiB as max content size.
    FRAME_MAX_D_SIZE = 1*1024*1024*1024

    _READER_CLASS = SeekableDecompressReader

    def __init__(self, filename, mode="r", *,
                 level_or_option=None, zstd_dict=None,
                 max_frame_content_size=1024*1024*1024):
        """Open a Zstandard Seekable Format file or 0-size file in binary mode.

        filename can be either an actual file name (given as a str, bytes, or
        PathLike object), in which case the named file is opened, or it can be
        an existing file object to read from or write to.

        In appending mode ("a" or "ab"), filename can't be a file object, use
        file path in this mode.

        mode can be "r" for reading (default), "w" for (over)writing, "x" for
        creating exclusively, or "a" for appending. These can equivalently be
        given as "rb", "wb", "xb" and "ab" respectively.

        Parameters
        level_or_option: When it's an int object, it represents compression
            level. When it's a dict object, it contains advanced compression
            parameters. Note, in read mode (decompression), it can only be a
            dict object, that represents decompression option. It doesn't
            support int type compression level in this case.
        zstd_dict: A ZstdDict object, pre-trained dictionary for compression /
            decompression.
        max_frame_content_size: In writing/appending modes (compression), when
            the uncompressed data size reaches max_frame_content_size, a frame
            is generated. If the size is small, it will increase seeking speed
            but reduce compression ratio. If the size is large, it will reduce
            seeking speed but increase compression ratio. You can also manually
            generate a frame using f.flush(f.FLUSH_FRAME).
        """
        self._fp = None
        self._closefp = False
        self._mode = _MODE_CLOSED

        if mode in ("r", "rb"):
            # Specified max_frame_content_size argument
            if max_frame_content_size != 1024*1024*1024:
                raise ValueError(('max_frame_content_size argument '
                                  'is only valid in writing mode.'))
        elif mode in ("w", "wb", "a", "ab", "x", "xb"):
            self._seek_table = SeekTable(read_mode=False)

            # Load seek table in appending mode
            if mode in ("a", "ab"):
                if isinstance(filename, (str, bytes, PathLike)):
                    with io.open(filename, "rb") as f:
                        self._seek_table.load_seek_table(f, seek_to_0=False)
                else:
                    raise TypeError(
                            ("SeekableZstdFile's appending mode "
                             "('a'/'ab') only accepts file path "
                             "(str/bytes/PathLike) as filename "
                             "argument. Can't accept file object."))

            # For seekable format
            if not (0 < max_frame_content_size <= self.FRAME_MAX_D_SIZE):
                raise ValueError(
                    ('max_frame_content_size argument should be '
                     '0 < value <= %d, provided value is %d.') % \
                    (self.FRAME_MAX_D_SIZE, max_frame_content_size))
            self._max_frame_content_size = max_frame_content_size
            self._reset_frame_sizes()

        super().__init__(filename, mode,
                         level_or_option=level_or_option,
                         zstd_dict=zstd_dict)

        # Overwrite seek table in appending mode
        if mode in ("a", "ab"):
            if self._fp.seekable():
                self._fp.seek(self._seek_table.get_full_c_size())
                # Necessary if the current table has many (0, 0) entries
                self._fp.truncate()
            else:
                # Add the seek table frame
                self._seek_table.append_entry(
                        self._seek_table.seek_frame_size, 0)
                # Emit a warning
                warn(("SeekableZstdFile is opened in appending mode "
                      "('a'/'ab'), but the underlying file object is "
                      "not seekable. Therefore the seek table (a zstd "
                      "skippable frame) at the end of the file can't "
                      "be overwritten. Each time open such file in "
                      "appending mode, it will waste some storage "
                      "space, %d bytes were wasted this time.") % \
                        self._seek_table.seek_frame_size,
                     RuntimeWarning, 2)

    def _reset_frame_sizes(self):
        self._current_c_size = 0
        self._current_d_size = 0
        self._left_d_size = self._max_frame_content_size

    def close(self):
        """Flush and close the file.

        May be called more than once without error. Once the file is
        closed, any other operation on it will raise a ValueError.
        """
        try:
            if self._mode == _MODE_WRITE:
                self.flush(self.FLUSH_FRAME)
                self._seek_table.write_seek_table(self._fp)
        finally:
            super().close()

    def write(self, data):
        """Write a bytes-like object to the file.

        Returns the number of uncompressed bytes written, which is
        always the length of data in bytes. Note that due to buffering,
        the file on disk may not reflect the data written until close()
        is called.
        """
        if self._mode != _MODE_WRITE:
            self._check_mode(_MODE_WRITE)

        # Accept any data that supports the buffer protocol.
        # And memoryview's subview is faster than slice.
        data = memoryview(data).cast('B')
        nbytes = data.nbytes
        pos = 0

        while nbytes > 0:
            # Write size
            write_size = min(nbytes, self._left_d_size)

            # Save compressed position
            fp_pos = self._fp.tell()

            # Write
            super().write(data[pos:pos+write_size])

            # Cumulate
            self._current_c_size += self._fp.tell() - fp_pos
            self._current_d_size += write_size

            pos += write_size
            nbytes -= write_size
            self._left_d_size -= write_size

            # Should flush a frame
            if self._left_d_size == 0 or \
               self._current_c_size >= self.FRAME_MAX_C_SIZE:
                self.flush(self.FLUSH_FRAME)

        return pos

    def flush(self, mode=ZstdFile.FLUSH_BLOCK):
        """Flush remaining data to the underlying stream.

        The mode argument can be ZstdFile.FLUSH_BLOCK, ZstdFile.FLUSH_FRAME.
        Abuse of this method will reduce compression ratio, use it only when
        necessary.

        If the program is interrupted afterwards, all data can be recovered.
        To ensure saving to disk, also need to use os.fsync(fd).

        This method does nothing in reading mode.
        """
        if self._mode != _MODE_WRITE:
            # Like IOBase.flush(), do nothing in reading mode.
            # TextIOWrapper.close() relies on this behavior.
            if self._mode == _MODE_READ:
                return
            # Closed, raise ValueError.
            self._check_mode()

        # Save compressed position
        fp_pos = self._fp.tell()

        # Flush
        super().flush(mode)

        # Cumulate, self._current_d_size += 0
        self._current_c_size += self._fp.tell() - fp_pos

        if mode == self.FLUSH_FRAME and \
           self._current_c_size != 0:
            # Add an entry to seek table
            self._seek_table.append_entry(self._current_c_size,
                                          self._current_d_size)
            self._reset_frame_sizes()

    @property
    def seek_table_info(self):
        if self._mode == _MODE_WRITE:
            return self._seek_table.get_info()
        elif self._mode == _MODE_READ:
            return self._buffer.raw.get_seek_table_info()
        else:
            return 'SeekableZstdFile object has been closed'

    @staticmethod
    def is_seekable_format_file(filename):
        """Check if a file is a valid Zstandard Seekable Format file or 0-size
        file.

        It parses the seek table at the end of the file, returns True if no
        format error.

        filename can be either a file path (str/bytes/PathLike), or can be an
        existing file object in reading mode.
        """
        # Check argument
        if isinstance(filename, (str, bytes, PathLike)):
            fp = io.open(filename, 'rb')
            is_file_path = True
        elif hasattr(filename, 'readable') and filename.readable() and \
             hasattr(filename, "seekable") and filename.seekable():
            fp = filename
            is_file_path = False
            orig_pos = fp.tell()
        else:
            raise TypeError(
                ('filename argument must be a str/bytes/PathLike object, '
                 'or a file object that is readable and seekable.'))

        # Read/Parse the seek table
        table = SeekTable(read_mode=True)
        try:
            table.load_seek_table(fp, seek_to_0=False)
        except SeekableFormatError:
            ret = False
        else:
            ret = True

        # Post process
        if is_file_path:
            fp.close()
        else:
            fp.seek(orig_pos)

        return ret
