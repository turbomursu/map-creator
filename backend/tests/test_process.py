import os
import sys
import time

import psutil
import pytest
from app.process import Cancelled, run_native


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Objects")
def test_cancellation_terminates_only_owned_process_tree(tmp_path):
    child_pid = tmp_path / "child.pid"
    code = (
        "import subprocess,sys,time; from pathlib import Path; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)']); "
        f"Path({str(child_pid)!r}).write_text(str(p.pid)); "
        "print('child started',flush=True); time.sleep(120)"
    )
    started = time.monotonic()
    with pytest.raises(Cancelled):
        run_native([sys.executable, "-c", code], tmp_path, dict(os.environ),
                   lambda: child_pid.exists() and time.monotonic() - started > 1,
                   lambda message: None)
    pid = int(child_pid.read_text())
    for _ in range(20):
        if not psutil.pid_exists(pid):
            break
        time.sleep(.1)
    assert not psutil.pid_exists(pid)
