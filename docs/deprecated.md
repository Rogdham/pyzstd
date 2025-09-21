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

_Deprecated in version 0.17.0._
