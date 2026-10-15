"""How much memory reading a worst-case PDF takes (text only, about 10 MB). Local, read-only.
Run: .venv/Scripts/python.exe tools/support_pdf_memory.py"""
import sys
import threading
import time
from pathlib import Path

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_support import make_pdf  # noqa: E402

from zyroguard import kb  # noqa: E402

LINE = "Our premium plan includes priority support, unlimited projects and a dedicated account manager."
page = "\n".join(f"{i}. {LINE}" for i in range(40))
pdf = make_pdf([page] * 2200)
print(f"PDF size {len(pdf) / 1e6:.1f} MB, 2200 pages")

proc = psutil.Process()
peak = [proc.memory_info().rss]
stop = False


def watch():
    while not stop:
        peak[0] = max(peak[0], proc.memory_info().rss)
        time.sleep(0.02)


base = proc.memory_info().rss
t = threading.Thread(target=watch, daemon=True)
t.start()
t0 = time.time()
try:
    text, pages = kb.read_file("big.pdf", pdf)
    parts = kb.sections(text, "pdf")
    result = f"read {pages} pages, {len(text) / 1e6:.2f} M characters, {len(parts)} sections"
except kb.ReadError as e:
    result = f"refused while reading (as designed): {e}"
finally:
    took = time.time() - t0
    stop = True
    t.join()
print(f"{result} in {took:.1f}s")
print(f"memory before {base / 1e6:.0f} MB, peak {peak[0] / 1e6:.0f} MB, extra {(peak[0] - base) / 1e6:.0f} MB")
