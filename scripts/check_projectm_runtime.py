#!/usr/bin/env python3
"""Check whether libprojectM runtime is discoverable via ctypes."""
from __future__ import annotations

import ctypes
import ctypes.util


def main() -> int:
    candidates = []
    for name in ('projectM-4', 'projectM'):
        found = ctypes.util.find_library(name)
        if found:
            candidates.append(found)
    candidates.extend(['libprojectM-4.so', 'libprojectM.so'])

    seen = set()
    ordered = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            ordered.append(c)

    if not ordered:
        print('projectM runtime not found via find_library.')
        return 1

    for cand in ordered:
        try:
            lib = ctypes.CDLL(cand)
            print(f'Loaded: {cand}')
            try:
                lib.projectm_get_version_components.argtypes = [
                    ctypes.POINTER(ctypes.c_int),
                    ctypes.POINTER(ctypes.c_int),
                    ctypes.POINTER(ctypes.c_int),
                ]
                lib.projectm_get_version_components.restype = None
                major = ctypes.c_int(0)
                minor = ctypes.c_int(0)
                patch = ctypes.c_int(0)
                lib.projectm_get_version_components(
                    ctypes.byref(major), ctypes.byref(minor), ctypes.byref(patch)
                )
                print(f'projectM version: {major.value}.{minor.value}.{patch.value}')
            except Exception as exc:
                print(f'Loaded but version query failed: {exc}')
            return 0
        except OSError as exc:
            print(f'Failed to load {cand}: {exc}')

    return 2


if __name__ == '__main__':
    raise SystemExit(main())
