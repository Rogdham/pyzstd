# Migrating to the standard library

In Python 3.14, [the `compression.zstd` module](https://docs.python.org/3.14/library/compression.zstd.html) is available to support Zstandard natively.

This guide was written to highlight the main differences and help with the migration.

_Note that to support Python versions before 3.14, you will need to install [the `backports.zstd` library](https://github.com/Rogdham/backports.zstd), created by the maintainer of `pyzstd`._

The examples in this guide assume the following imports:

```python
import pyzstd
import sys

if sys.version_info >= (3, 14):
    from compression import zstd
else:
    from backports import zstd
```

## `level_or_option` parameter

In `pyzstd`, the `level_or_option` parameter could accept either a compression level (as an integer) or a dictionary of options. In the standard library, this is split into two distinct parameters: `level` and `options`. Only one can be used at a time.

```python
# before
pyzstd.compress(data, 10)
pyzstd.compress(data, level_or_option=10)

# after
zstd.compress(data, 10)
zstd.compress(data, level=10)
```

```python
# before
pyzstd.compress(data, {pyzstd.CParameter.checksumFlag: True})
pyzstd.compress(data, level_or_option={pyzstd.CParameter.checksumFlag: True})

# after
zstd.compress(data, options={zstd.CompressionParameter.checksum_flag: True})
```

## `CParameter` and `DParameter`

The `CParameter` and `DParameter` classes have been renamed to `CompressionParameter` and `DecompressionParameter` respectively.

Additionally, attribute names now use snake_case instead of camelCase.

```python
# before
pyzstd.CParameter.enableLongDistanceMatching
pyzstd.DParameter.windowLogMax

# after
zstd.CompressionParameter.enable_long_distance_matching
zstd.DecompressionParameter.window_log_max
```

Finally, the `CParameter.targetCBlockSize` parameter is not available for now. Assuming a version of libzstd supporting it is used at runtime (1.5.6 or later), the integer `130` can be used as a key in the dictionary passed to the `options` parameter.

## `ZstdCompressor._set_pledged_input_size`

The method `_set_pledged_input_size` of the `ZstdCompressor` class has been renamed to `set_pledged_input_size`.

## `EndlessZstdDecompressor`

The `EndlessZstdDecompressor` class is not available.

Here are possible alternatives:

- Chain multiple `ZstdDecompressor` instances manually.
- Include [this code snippet](https://gist.github.com/Rogdham/e2d694cee709e75240a1fd5278e99666#file-endless_zstd_decompressor-py) in your codebase.
- Use the `decompress` function if the data is small enough.
- Use a file-like interface via `ZstdFile`.

## `RichMemZstdCompressor` and `richmem_compress`

The `RichMemZstdCompressor` class and `richmem_compress` function are not available.

Use `ZstdCompressor` and `compress` instead.

## `compress_stream` and `decompress_stream`

The `compress_stream` and `decompress_stream` functions, which are deprecated in `pyzstd`, are not available.

See [alternatives](./deprecated.md#compress-stream).

## `compressionLevel_values`

The constant `compressionLevel_values` namedtuple is not available. Use the following alternatives:

- `zstd.COMPRESSION_LEVEL_DEFAULT` for the default compression level.
- `zstd.CompressionParameter.compression_level.bounds()` for the minimum and maximum compression levels.

## `SeekableZstdFile`

Support for the Zstandard seekable format is not available. Continue using `pyzstd` for now if the feature is required.
