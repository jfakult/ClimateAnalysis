"""C parser for step 3: one raw ISD file -> flat arrays (common/yeardata.py),
one merged record per (date, hour).

common/isd_fast.c does the whole job (line parsing + per-hour merging). The
library is built on first use with `cc` if libisd_fast.so is missing or older
than the source, then loaded with ctypes. There is no Python fallback: if the
library can't be built or loaded, or a file has malformed content, this raises.
The plain-Python statement of the same rules is reference/parse_reference.py,
which tests/check_isd_fast.py compares against.

Measurements and the reasoning for using C: compute/PARSER_ACCELERATION.md.
"""

import array
import ctypes
import os
import subprocess
from pathlib import Path

_DIR = Path(__file__).resolve().parent
_SRC = _DIR / "isd_fast.c"
_LIB = _DIR / "libisd_fast.so"

_lib = None
_buffers = {}   # reusable output arrays, grown as needed


def _build():
    # Build to a per-process temp name, then rename: with several worker
    # processes starting at once, nobody ever loads a half-written .so.
    tmp = _LIB.with_suffix(f".{os.getpid()}.tmp")
    subprocess.run(["cc", "-O2", "-shared", "-fPIC", "-o", str(tmp), str(_SRC)], check=True, capture_output=True)
    os.replace(tmp, _LIB)


def load():
    """Build (if needed) and load the library; raises RuntimeError if it can't."""
    global _lib
    if _lib is not None:
        return _lib
    try:
        if not _LIB.exists() or _LIB.stat().st_mtime < _SRC.stat().st_mtime:
            _build()
        try:
            lib = ctypes.CDLL(str(_LIB))
        except OSError:
            _build()    # e.g. a .so built on another machine: rebuild here
            lib = ctypes.CDLL(str(_LIB))
    except (OSError, subprocess.CalledProcessError, FileNotFoundError) as e:
        detail = (getattr(e, "stderr", b"") or b"").decode(errors="replace")[:300]
        raise RuntimeError(f"cannot build/load the C parser ({type(e).__name__}: {e} {detail}). "
                           f"It needs a C compiler: install gcc/cc, then rerun.") from e
    lib.isd_parse.restype = ctypes.c_long
    lib.isd_parse.argtypes = [ctypes.c_char_p, ctypes.c_long, ctypes.c_long] + [ctypes.c_void_p] * 9
    _lib = lib
    return lib


def _arrays(cap):
    if _buffers.get("cap", 0) < cap:
        n = max(cap, 4096)
        _buffers["cap"] = n
        _buffers["key"] = array.array("i", bytes(4 * n))
        for name in ("temp", "dew", "wind", "cloud", "precip", "acc_depth"):
            _buffers[name] = array.array("d", bytes(8 * n))
        _buffers["acc_period"] = array.array("b", bytes(n))
        _buffers["wet"] = array.array("b", bytes(n))
    return _buffers


def parse_arrays(raw):
    """raw: decompressed file bytes -> the flat arrays of common/yeardata.py
    (one entry per merged hour, sorted by time)."""
    lib = load()
    cap = raw.count(b"\n") + 1
    b = _arrays(cap)
    ptr = lambda name: b[name].buffer_info()[0]
    n = lib.isd_parse(raw, len(raw), cap, ptr("key"), ptr("temp"), ptr("dew"), ptr("wind"),
                      ptr("cloud"), ptr("precip"), ptr("acc_depth"), ptr("acc_period"), ptr("wet"))
    if n < 0:
        raise ValueError("unexpected content in ISD file (a required numeric field is not numeric)")
    # slicing an array.array copies, so the reusable buffers are not shared
    return {name: b[name][:n] for name in ("key", "temp", "dew", "wind", "cloud", "precip", "acc_depth", "acc_period", "wet")}
