# pyzstd module: deprecations

## `compress_stream`

```python
# before
with io.open(input_file_path, 'rb') as ifh:
    with io.open(output_file_path, 'wb') as ofh:
        compress_stream(ifh, ofh, level_or_option=5)

# after
with io.open(input_file_path, 'rb') as ifh:
    with pyzstd.open(output_file_path, 'w', level_or_option=5) as ofh:
        shutil.copyfileobj(ifh, ofh)
```

```{hint}
Instead of the `read_size` and `write_size` parameters, you can use
`shutil.copyfileobj`'s `length` parameter.
```

Alternatively, you can use `ZstdCompressor` to have more control:

```python
# after: more complex alternative
with io.open(input_file_path, 'rb') as ifh:
    with io.open(output_file_path, 'wb') as ofh:
        compressor = ZstdCompressor(level_or_option=5)
        compressor._set_pledged_input_size(pledged_input_size)  # optional
        while data := ifh.read(read_size):
            ofh.write(compressor.compress(data))
            callback_progress(ifh.tell(), ofh.tell())  # optional
        ofh.write(compressor.flush())
```

_Deprecated in version 0.17.0._

## `decompress_stream`

```python
# before
with io.open(input_file_path, 'rb') as ifh:
    with io.open(output_file_path, 'wb') as ofh:
        decompress_stream(ifh, ofh)

# after
with pyzstd.open(input_file_path) as ifh:
    with io.open(output_file_path, 'wb') as ofh:
        shutil.copyfileobj(ifh, ofh)
```

```{hint}
Instead of the `read_size` and `write_size` parameters, you can use
`shutil.copyfileobj`'s `length` parameter.
```

Alternatively, you can use `EndlessZstdDecompressor` to have more control:

```python
# after: more complex alternative
with io.open(input_file_path, 'rb') as ifh:
    with io.open(output_file_path, 'wb') as ofh:
        decompressor = EndlessZstdDecompressor()
        while True:
            if decompressor.needs_input:
                data = input_stream.read(read_size)
                if not data:
                    break
            else:
                data = b""
            ofh.write(decompressor.decompress(data, write_size))
            callback_progress(ifh.tell(), ofh.tell())  # optional
        if not decompressor.at_frame_edge:
            raise ValueError("zstd data ends in an incomplete frame")
```

_Deprecated in version 0.17.0._

## `richmem_compress`

```python
# before
data_out = pyzstd.richmem_compress(data_in, level_or_option=5)

# after
data_out = pyzstd.compress(data_in, level_or_option=5)
```

_Deprecated in version 0.18.0._

## `RichMemZstdCompressor`

```python
# before
compressor = pyzstd.RichMemZstdCompressor(level_or_option=5)
data_out1 = compressor.compress(data_in1)
data_out2 = compressor.compress(data_in2)
data_out3 = compressor.compress(data_in3)

# after
data_out1 = pyzstd.compress(data_in1, level_or_option=5)
data_out2 = pyzstd.compress(data_in2, level_or_option=5)
data_out3 = pyzstd.compress(data_in3, level_or_option=5)
```

_Deprecated in version 0.18.0._
