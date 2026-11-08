"""Run one measured command inside this process, then report its own peak memory (used by run_benchmark.py).

Usage: python benchmarks/_child.py REPORT.json MODE [ARGS...]

  cli ARGS...      order_quality's command line, exactly as `python -m order_quality ARGS...`
  script PATH      a stand-alone script, as `python PATH` (the original baseline script)
  alloc MIB        hold MIB mebibytes of written memory (self-test of the memory method)
  idle             do nothing (self-test)

The memory is read in the measured process itself. On Windows a virtual environment's python.exe is a
launcher that starts the real interpreter as a second process, so reading the launched process's handle
from the parent would measure the launcher, not the pipeline.
"""

from __future__ import annotations

import json
import runpy
import sys


def peak_memory() -> dict[str, float]:
    """Peak resident memory of this process in MiB (Windows: peak working set and peak private commit)."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessMemoryCounters),
                                                     wintypes.DWORD]
        kernel32.K32GetProcessMemoryInfo.restype = wintypes.BOOL
        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        if not kernel32.K32GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        return {"peak_rss_mib": counters.PeakWorkingSetSize / 2**20,
                "peak_private_mib": counters.PeakPagefileUsage / 2**20}
    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {"peak_rss_mib": peak / 2**20 if sys.platform == "darwin" else peak / 2**10}


def _run(mode: str, args: list[str]) -> int:
    if mode == "cli":
        from order_quality.cli import main

        return main(args)
    if mode == "script":
        sys.argv = [args[0]]
        try:
            runpy.run_path(args[0], run_name="__main__")
        except SystemExit as exc:
            return exc.code if isinstance(exc.code, int) else 1
        return 0
    if mode == "alloc":
        block = b"x" * (int(args[0]) * 2**20)  # bytes multiplication writes every page
        return 0 if block else 1
    if mode == "idle":
        return 0
    raise SystemExit(f"unknown mode: {mode}")


def main() -> int:
    report, mode, args = sys.argv[1], sys.argv[2], sys.argv[3:]
    code = _run(mode, args)
    with open(report, "w", encoding="utf-8") as handle:
        json.dump({"exit_code": code, **peak_memory()}, handle)
    return code


if __name__ == "__main__":
    sys.exit(main())
