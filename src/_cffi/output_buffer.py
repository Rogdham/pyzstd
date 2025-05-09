from .common import m, ffi, _new_nonzero

class _BlocksOutputBuffer:
    KB = 1024
    MB = 1024 * 1024
    BUFFER_BLOCK_SIZE = (
        # If change this list, also change:
        #   The C implementation
        #   OutputBufferTestCase unittest
        # If change the first blocks's size, also change:
        #   _32_KiB in __init__.py
        #   FileTestCase.test_decompress_limited() test
        32*KB, 64*KB, 256*KB, 1*MB, 4*MB, 8*MB, 16*MB, 16*MB,
        32*MB, 32*MB, 32*MB, 32*MB, 64*MB, 64*MB, 128*MB, 128*MB,
        256*MB )
    MEM_ERR_MSG = "Unable to allocate output buffer."

    def initAndGrow(self, out, max_length):
        # Get block size
        if 0 <= max_length < self.BUFFER_BLOCK_SIZE[0]:
            block_size = max_length
        else:
            block_size = self.BUFFER_BLOCK_SIZE[0]

        # The first block
        block = _new_nonzero("char[]", block_size)
        if block == ffi.NULL:
            raise MemoryError

        # Create the list
        self.list = [block]

        # Set variables
        self.allocated = block_size
        self.max_length = max_length

        out.dst = block
        out.size = block_size
        out.pos = 0

    def initWithSize(self, out, max_length, init_size):
        # Get block size
        if 0 <= max_length < init_size:
            block_size = max_length
        else:
            block_size = init_size

        # The first block
        block = _new_nonzero("char[]", block_size)
        if block == ffi.NULL:
            raise MemoryError(self.MEM_ERR_MSG)

        # Create the list
        self.list = [block]

        # Set variables
        self.allocated = block_size
        self.max_length = max_length

        out.dst = block
        out.size = block_size
        out.pos = 0

    def grow(self, out):
        # Ensure no gaps in the data
        assert out.pos == out.size

        # Get block size
        list_len = len(self.list)
        if list_len < len(self.BUFFER_BLOCK_SIZE):
            block_size = self.BUFFER_BLOCK_SIZE[list_len]
        else:
            block_size = self.BUFFER_BLOCK_SIZE[-1]

        # Check max_length
        if self.max_length >= 0:
            # If (rest == 0), should not grow the buffer.
            rest = self.max_length - self.allocated
            assert rest > 0

            # block_size of the last block
            if block_size > rest:
                block_size = rest

        # Create the block
        b = _new_nonzero("char[]", block_size)
        if b == ffi.NULL:
            raise MemoryError(self.MEM_ERR_MSG)
        self.list.append(b)

        # Set variables
        self.allocated += block_size

        out.dst = b
        out.size = block_size
        out.pos = 0

    def reachedMaxLength(self, out):
        # Ensure (data size == allocated size)
        assert out.pos == out.size

        return self.allocated == self.max_length

    def finish(self, out):
        # Fast path for single block
        if (len(self.list) == 1 and out.pos == out.size) or \
           (len(self.list) == 2 and out.pos == 0):
            return bytes(ffi.buffer(self.list[0]))

        # Final bytes object
        data_size = self.allocated - (out.size-out.pos)
        final = _new_nonzero("char[]", data_size)
        if final == ffi.NULL:
            raise MemoryError(self.MEM_ERR_MSG)

        # Memory copy
        # Blocks except the last one
        posi = 0
        for block in self.list[:-1]:
            ffi.memmove(final+posi, block, len(block))
            posi += len(block)
        # The last block
        ffi.memmove(final+posi, self.list[-1], out.pos)

        return bytes(ffi.buffer(final))