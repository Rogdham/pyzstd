# CLI of pyzstd module: python -m pyzstd --help
import argparse
from collections.abc import Mapping, Sequence
import os
from shutil import copyfileobj
from time import time
from typing import Any, BinaryIO, Protocol, cast

from pyzstd import (
    CParameter,
    DParameter,
    ZstdDict,
    ZstdFile,
    compressionLevel_values,
    train_dict,
    zstd_version,
)
from pyzstd import __version__ as pyzstd_version


class Args(Protocol):
    dict: str
    f: bool
    compress: str
    tar_input_dir: str
    level: int
    threads: int
    long: int
    checksum: bool
    write_dictID: bool  # noqa: N815
    decompress: str
    tar_output_dir: str
    test: str | None
    windowLogMax: int  # noqa: N815
    train: str
    maxdict: int
    dictID: int  # noqa: N815
    output: BinaryIO | None
    input: BinaryIO | None
    zd: ZstdDict | None


# buffer sizes recommended by zstd
C_READ_BUFFER = 131072
D_READ_BUFFER = 131075


# open output file and assign to args.output
def open_output(args: Args, path: str) -> None:
    if not args.f and os.path.isfile(path):
        answer = input(f"output file already exists:\n{path}\noverwrite? (y/n) ")
        print()
        if answer != "y":
            import sys

            sys.exit()
    args.output = open(path, "wb")  # noqa: SIM115


def close_files(args: Args) -> None:
    if args.input is not None:
        args.input.close()

    if args.output is not None:
        args.output.close()


def compress_option(args: Args) -> Mapping[int, int]:
    # threads message
    if args.threads == 0:
        threads_msg = "single-thread mode"
    else:
        threads_msg = f"multi-thread mode, {args.threads} threads."

    # long mode
    if args.long >= 0:
        use_long = 1
        window_log = args.long
        long_msg = f"yes, windowLog is {window_log}."
    else:
        use_long = 0
        window_log = 0
        long_msg = "no"

    # option
    option: Mapping[int, int] = {
        CParameter.compressionLevel: args.level,
        CParameter.nbWorkers: args.threads,
        CParameter.enableLongDistanceMatching: use_long,
        CParameter.windowLog: window_log,
        CParameter.checksumFlag: args.checksum,
        CParameter.dictIDFlag: args.write_dictID,
    }

    # pre-compress message
    msg = (
        f" - compression level: {args.level}\n"
        f" - threads: {threads_msg}\n"
        f" - long mode: {long_msg}\n"
        f" - zstd dictionary: {args.zd}\n"
        f" - add checksum: {args.checksum}"
    )
    print(msg)

    return option


def compress(args: Args) -> None:
    args.input = cast("BinaryIO", args.input)

    # output file
    if args.output is None:
        open_output(args, args.input.name + ".zst")
        args.output = cast("BinaryIO", args.output)

    # pre-compress message
    msg = (
        "Compress file:\n"
        f" - input file : {args.input.name}\n"
        f" - output file: {args.output.name}"
    )
    print(msg)

    # option
    option = compress_option(args)

    # compress
    t1 = time()
    with ZstdFile(args.output, "w", level_or_option=option, zstd_dict=args.zd) as fout:
        copyfileobj(args.input, fout)
    t2 = time()
    in_size = args.input.tell()
    out_size = args.output.tell()
    close_files(args)

    # post-compress message
    ratio = 100.0 if in_size == 0 else 100 * out_size / in_size
    msg = (
        f"\nCompression succeeded, {t2 - t1:.2f} seconds.\n"
        f"Input {in_size:,} bytes, output {out_size:,} bytes, ratio {ratio:.2f}%.\n"
    )
    print(msg)


def decompress(args: Args) -> None:
    args.input = cast("BinaryIO", args.input)

    # output file
    if args.output is None:
        if args.test is None:
            from re import subn

            out_path, replaced = subn(r"(?i)^(.*)\.zst$", r"\1", args.input.name)
            if not replaced:
                out_path = args.input.name + ".decompressed"
        else:
            out_path = os.devnull
        open_output(args, out_path)
        args.output = cast("BinaryIO", args.output)

    # option
    option: Mapping[int, int] = {DParameter.windowLogMax: args.windowLogMax}

    # pre-decompress message
    output_name = args.output.name
    if output_name == os.devnull:
        output_name = "None"
    print(
        "Decompress file:\n"
        f" - input file : {args.input.name}\n"
        f" - output file: {output_name}\n"
        f" - zstd dictionary: {args.zd}"
    )

    # decompress
    t1 = time()
    with ZstdFile(args.input, level_or_option=option, zstd_dict=args.zd) as fin:
        copyfileobj(fin, args.output)
    t2 = time()
    in_size = args.input.tell()
    out_size = args.output.tell()
    close_files(args)

    # post-decompress message
    ratio = 100.0 if out_size == 0 else 100 * in_size / out_size
    msg = (
        f"\nDecompression succeeded, {t2 - t1:.2f} seconds.\n"
        f"Input {in_size:,} bytes, output {out_size:,} bytes, ratio {ratio:.2f}%.\n"
    )
    print(msg)


def train(args: Args) -> None:
    from glob import glob

    # check output file
    if args.output is None:
        raise ValueError("need to specify output file using -o/--output option")

    # gather samples
    print("Gathering samples, please wait.", flush=True)
    lst = []
    for file in glob(args.train, recursive=True):
        with open(file, "rb") as f:
            dat = f.read()
            lst.append(dat)
            print("samples count:", len(lst), end="\r", flush=True)
    if len(lst) == 0:
        raise ValueError("No samples gathered, please check GLOB_PATH.")

    samples_size = sum(len(sample) for sample in lst)
    if samples_size == 0:
        raise ValueError("Samples content is empty, can't train.")

    # pre-train message
    msg = (
        "Gathered, train zstd dictionary:\n"
        " - samples: {}\n"
        " - samples number: {}\n"
        " - samples content: {:,} bytes\n"
        " - dict file: {}\n"
        " - dict max size: {:,} bytes\n"
        " - dict id: {}\n"
        "Training, please wait."
    ).format(
        args.train,
        len(lst),
        samples_size,
        args.output.name,
        args.maxdict,
        "random" if args.dictID is None else args.dictID,
    )
    print(msg, flush=True)

    # train
    t1 = time()
    zd = train_dict(lst, args.maxdict)
    t2 = time()

    # Dictionary_ID: 4 bytes, stored in little-endian format.
    # it can be any value, except 0 (which means no Dictionary_ID).
    if args.dictID is not None and len(zd.dict_content) >= 8:
        content = (
            zd.dict_content[:4]
            + args.dictID.to_bytes(4, "little")
            + zd.dict_content[8:]
        )
        zd = ZstdDict(content)

    # save to file
    args.output.write(zd.dict_content)
    close_files(args)

    # post-train message
    msg = f"Training succeeded, {t2 - t1:.2f} seconds.\nDictionary: {zd}\n"
    print(msg)


def tarfile_create(args: Args) -> None:
    import sys

    if sys.version_info < (3, 14):
        from backports.zstd import tarfile
    else:
        import tarfile

    # check input dir
    args.tar_input_dir = args.tar_input_dir.rstrip(os.sep)
    if not os.path.isdir(args.tar_input_dir):
        msg = "Tar archive input dir invalid: " + args.tar_input_dir
        raise NotADirectoryError(msg)
    dirname, basename = os.path.split(args.tar_input_dir)

    # check output file
    if args.output is None:
        out_path = os.path.join(dirname, basename + ".tar.zst")
        open_output(args, out_path)
        args.output = cast("BinaryIO", args.output)

    # pre-compress message
    msg = (
        "Archive tar file:\n"
        f" - input directory: {args.tar_input_dir}\n"
        f" - output file: {args.output.name}"
    )
    print(msg)

    # option
    option = compress_option(args)

    # compress
    print("Archiving, please wait.", flush=True)
    t1 = time()
    with tarfile.TarFile.zstopen(
        None, fileobj=args.output, mode="w", options=option, zstd_dict=args.zd
    ) as f:
        f.add(args.tar_input_dir, basename)
        uncompressed_size = f.fileobj.tell()  # type: ignore[union-attr]
    t2 = time()

    output_file_size = args.output.tell()
    close_files(args)

    # post-compress message
    if uncompressed_size != 0:
        ratio = 100 * output_file_size / uncompressed_size
    else:
        ratio = 100.0

    msg = (
        f"Archiving succeeded, {t2 - t1:.2f} seconds.\n"
        f"Input ~{uncompressed_size:,} bytes, output {output_file_size:,} bytes, ratio {ratio:.2f}%.\n"
    )
    print(msg)


def tarfile_extract(args: Args) -> None:
    import sys

    if sys.version_info < (3, 14):
        from backports.zstd import tarfile
    else:
        import tarfile

    # input file size
    if args.input is None:
        msg = "need to specify input file using -d/--decompress option."
        raise FileNotFoundError(msg)
    input_file_size = os.path.getsize(args.input.name)

    # check output dir
    if not os.path.isdir(args.tar_output_dir):
        msg = "Tar archive output dir invalid: " + args.tar_output_dir
        raise NotADirectoryError(msg)

    # option
    option: Mapping[int, int] = {DParameter.windowLogMax: args.windowLogMax}

    # pre-extract message
    msg = (
        "Extract tar archive:\n"
        f" - input file: {args.input.name}\n"
        f" - output dir: {args.tar_output_dir}\n"
        f" - zstd dictionary: {args.zd}\n"
        "Extracting, please wait."
    )
    print(msg, flush=True)

    # extract
    t1 = time()
    with tarfile.TarFile.zstopen(
        None, fileobj=args.input, mode="r", zstd_dict=args.zd, options=option
    ) as f:
        f.extractall(args.tar_output_dir, filter="data")
        uncompressed_size = f.fileobj.tell()  # type: ignore[union-attr]
    t2 = time()
    close_files(args)

    # post-extract message
    if uncompressed_size != 0:
        ratio = 100 * input_file_size / uncompressed_size
    else:
        ratio = 100.0
    msg = (
        f"Extraction succeeded, {t2 - t1:.2f} seconds.\n"
        f"Input {input_file_size:,} bytes, output ~{uncompressed_size:,} bytes, ratio {ratio:.2f}%.\n"
    )
    print(msg)


def range_action(start: int, end: int) -> type[argparse.Action]:
    class RangeAction(argparse.Action):
        def __call__(
            self,
            _: object,
            namespace: object,
            values: str | Sequence[Any] | None,
            option_string: str | None = None,
        ) -> None:
            # convert to int
            try:
                v = int(values)  # type: ignore[arg-type]
            except ValueError:
                raise TypeError(f"{option_string} should be an integer") from None

            # check range
            if not (start <= v <= end):
                # message
                msg = (
                    f"{option_string} value should: {start} <= v <= {end}. "
                    f"provided value is {v}."
                )
                raise ValueError(msg)

            setattr(namespace, self.dest, v)

    return RangeAction


def parse_arg() -> Args:
    p = argparse.ArgumentParser(
        prog="CLI of pyzstd module",
        description=(
            "The command style is similar to zstd's "
            "CLI, but there are some differences.\n"
            "Zstd's CLI should be faster, it has "
            "some I/O optimizations."
        ),
        epilog=(
            "Examples of use:\n"
            "  compress a file:\n"
            "    python -m pyzstd -c IN_FILE -o OUT_FILE\n"
            "  decompress a file:\n"
            "    python -m pyzstd -d IN_FILE -o OUT_FILE\n"
            "  create a tar archive:\n"
            "    python -m pyzstd --tar-input-dir DIR -o OUT_FILE\n"
            "  extract a tar archive, output will forcibly overwrite existing files:\n"
            "    python -m pyzstd -d IN_FILE --tar-output-dir DIR\n"
            "  train a zstd dictionary, ** traverses sub-directories:\n"
            '    python -m pyzstd --train "E:\\cpython\\**\\*.c" -o OUT_FILE'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    g = p.add_argument_group("Common arguments")
    g.add_argument(
        "-D",
        "--dict",
        metavar="FILE",
        type=argparse.FileType("rb"),
        help="use FILE as zstd dictionary for compression or decompression",
    )
    g.add_argument(
        "-o", "--output", metavar="FILE", type=str, help="result stored into FILE"
    )
    g.add_argument(
        "-f",
        action="store_true",
        help="disable output check, allows overwriting existing file.",
    )

    g = p.add_argument_group("Compression arguments")
    gm = g.add_mutually_exclusive_group()
    gm.add_argument("-c", "--compress", metavar="FILE", type=str, help="compress FILE")
    gm.add_argument(
        "--tar-input-dir",
        metavar="DIR",
        type=str,
        help=(
            "create a tar archive from DIR. this option overrides -c/--compress option."
        ),
    )
    g.add_argument(
        "-l",
        "--level",
        metavar="#",
        default=compressionLevel_values.default,
        action=range_action(compressionLevel_values.min, compressionLevel_values.max),
        help=f"compression level, range: [{compressionLevel_values.min},{compressionLevel_values.max}], default: {compressionLevel_values.default}.",
    )
    g.add_argument(
        "-t",
        "--threads",
        metavar="#",
        default=0,
        action=range_action(*CParameter.nbWorkers.bounds()),
        help=(
            "spawns # threads to compress. if this option is not "
            "specified or is 0, use single thread mode."
        ),
    )
    g.add_argument(
        "--long",
        metavar="#",
        nargs="?",
        const=27,
        default=-1,
        action=range_action(*CParameter.windowLog.bounds()),
        help="enable long distance matching with given windowLog (default #: 27)",
    )
    g.add_argument(
        "--no-checksum",
        action="store_false",
        dest="checksum",
        default=True,
        help="don't add 4-byte XXH64 checksum to the frame",
    )
    g.add_argument(
        "--no-dictID",
        action="store_false",
        dest="write_dictID",
        default=True,
        help="don't write dictID into frame header (dictionary compression only)",
    )

    g = p.add_argument_group("Decompression arguments")
    gm = g.add_mutually_exclusive_group()
    gm.add_argument(
        "-d", "--decompress", metavar="FILE", type=str, help="decompress FILE"
    )
    g.add_argument(
        "--tar-output-dir",
        metavar="DIR",
        type=str,
        help=(
            "extract tar archive to DIR, "
            "output will forcibly overwrite existing files. "
            "this option overrides -o/--output option."
        ),
    )
    gm.add_argument(
        "--test",
        metavar="FILE",
        type=str,
        help="try to decompress FILE to check integrity",
    )
    g.add_argument(
        "--windowLogMax",
        metavar="#",
        default=0,
        action=range_action(*DParameter.windowLogMax.bounds()),
        help="set a memory usage limit for decompression (windowLogMax)",
    )

    g = p.add_argument_group("Dictionary builder")
    g.add_argument(
        "--train",
        metavar="GLOB_PATH",
        type=str,
        help="create a dictionary from a training set of files",
    )
    g.add_argument(
        "--maxdict",
        metavar="SIZE",
        type=int,
        default=112640,
        help="limit dictionary to SIZE bytes (default: 112640)",
    )
    g.add_argument(
        "--dictID",
        metavar="DICT_ID",
        default=None,
        action=range_action(1, 0xFFFFFFFF),
        help="specify dictionary ID value (default: random)",
    )

    args = p.parse_args()

    # input file
    if args.compress is not None:
        args.input = open(args.compress, "rb", buffering=C_READ_BUFFER)  # noqa: SIM115
    elif args.decompress is not None:
        args.input = open(args.decompress, "rb", buffering=D_READ_BUFFER)  # noqa: SIM115
    elif args.test is not None:
        args.input = open(args.test, "rb", buffering=D_READ_BUFFER)  # noqa: SIM115
    else:
        args.input = None

    # output file
    if args.output is not None:
        open_output(args, args.output)

    # load dictionary
    if args.dict is not None:
        zd_content = args.dict.read()
        args.dict.close()
        # Magic_Number: 4 bytes, value 0xEC30A437, little-endian format.
        is_raw = zd_content[:4] != b"\x37\xa4\x30\xec"
        args.zd = ZstdDict(zd_content, is_raw=is_raw)
    else:
        args.zd = None

    # arguments combination
    functions = [
        args.compress,
        args.decompress,
        args.test,
        args.train,
        args.tar_input_dir,
    ]
    if sum(1 for i in functions if i is not None) > 1:
        raise ValueError("Wrong arguments combination")

    return args


def main() -> None:
    print(f"*** pyzstd module v{pyzstd_version}, zstd library v{zstd_version}. ***\n")

    args = parse_arg()

    if args.tar_input_dir:
        tarfile_create(args)
    elif args.tar_output_dir:
        tarfile_extract(args)
    elif args.compress:
        compress(args)
    elif args.decompress or args.test:
        decompress(args)
    elif args.train:
        train(args)
    else:
        print("Invalid command. See help: python -m pyzstd --help")


if __name__ == "__main__":
    main()
