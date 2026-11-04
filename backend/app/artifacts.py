import gzip
import hashlib
import json
import math
import mmap
import sqlite3
import struct
import zlib
from contextlib import closing
from itertools import islice
from pathlib import Path

import mapbox_vector_tile
from google.protobuf.message import DecodeError
from pmtiles.reader import Reader, all_tiles
from pmtiles.tile import MagicNumberNotFound, SpecVersionUnsupported


def checksum(path: Path, check=None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            if check:
                check()
            digest.update(chunk)
    return digest.hexdigest()


def decode_tile(data: bytes):
    if data.startswith(b"\x1f\x8b"):
        data = gzip.decompress(data)
    return mapbox_vector_tile.decode(data)


def tiles(path: Path):
    if path.suffix == ".mbtiles":
        with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as db:
            for z, x, tms, data in db.execute(
                "SELECT zoom_level,tile_column,tile_row,tile_data FROM tiles"
            ):
                yield (z, x, (1 << z) - 1 - tms), data
    else:
        with path.open("rb") as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
            yield from all_tiles(lambda offset, length: data[offset:offset + length])


def metadata(path: Path) -> dict:
    if path.suffix == ".mbtiles":
        with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as db:
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("MBTiles database integrity check failed.")
            result = dict(db.execute("SELECT name,value FROM metadata"))
            if "json" in result:
                result.update(json.loads(result["json"]))
            result["bounds"] = [float(value) for value in result["bounds"].split(",")]
            return result
    with path.open("rb") as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
        reader = Reader(lambda offset, length: data[offset:offset + length])
        header = reader.header()
        if header["version"] != 3 or int(header["tile_type"].value) != 1:
            raise ValueError("Expected a PMTiles v3 MVT archive.")
        return {**reader.metadata(), "minzoom": header["min_zoom"], "maxzoom": header["max_zoom"],
                "bounds": [header[key] / 1e7 for key in
                           ("min_lon_e7", "min_lat_e7", "max_lon_e7", "max_lat_e7")]}


def validate(path: Path, check=None) -> dict:
    try:
        return _validate(path, check)
    except (MagicNumberNotFound, SpecVersionUnsupported, EOFError, struct.error, sqlite3.DatabaseError,
            gzip.BadGzipFile, zlib.error, DecodeError) as error:
        raise ValueError(f"Archive is corrupt or unsupported: {error}") from error


def _validate(path: Path, check=None) -> dict:
    result = metadata(path)
    if not result.get("attribution") or not result.get("vector_layers"):
        raise ValueError("Archive is missing attribution or vector-layer metadata.")
    if not 0 <= int(result["minzoom"]) <= int(result["maxzoom"]) <= 14:
        raise ValueError("Archive zoom metadata is invalid.")
    bounds = result["bounds"]
    if (len(bounds) != 4 or not all(math.isfinite(value) for value in bounds) or
        not -180 <= bounds[0] <= bounds[2] <= 180 or
        not -85.051129 <= bounds[1] <= bounds[3] <= 85.051129):
        raise ValueError("Archive bounds metadata is invalid.")
    with closing(tiles(path)) as iterator:
        samples = list(islice(iterator, 8))
    if not samples or not any(decode_tile(data) for _, data in samples):
        raise ValueError("No readable vector tiles. Try a populated region, more layers, or higher zoom.")
    return {"size": path.stat().st_size, "sha256": checksum(path, check), "metadata": result}
