import logging
import os
import sys
import time
from pathlib import Path

from app.pipeline import Pipeline
from app.store import Store


def main(root: Path, tools: Path):
    root.mkdir(parents=True, exist_ok=True)
    with (root / "worker.lock").open("a+b") as lock:
        if os.name == "nt":
            import msvcrt
            lock.seek(0)
            lock.write(b"0")
            lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        store = Store(root)
        store.recover()
        pipeline = Pipeline(store, tools)
        while True:
            job = store.claim()
            if job:
                pipeline.execute(job)
            else:
                time.sleep(0.5)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main(Path(sys.argv[1]), Path(sys.argv[2]))
