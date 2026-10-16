#!/usr/bin/env python3
"""Letting go of a QThread without destroying it while it still runs.

The tool's workers have no Qt parent, so the Python attribute holding one is the
only thing keeping the C++ QThread alive. A worker's "I'm done" signal is emitted
from inside run(), and the GUI slot it triggers can execute before the thread has
actually returned. Dropping the reference there lets Qt destroy a running thread:
"QThread: Destroyed while thread is still running", then abort.
"""


def join_worker(worker):
    """Block until the thread has really exited. Call before dropping or replacing it.

    After the worker's final signal this is a wait of microseconds.
    """
    if worker is not None:
        worker.wait()


def stop_worker(worker):
    """Ask a running worker to stop and wait for it — for window close.

    Workers must honour cancel() promptly (they poll it at least every few hundred
    milliseconds), which is what makes an unbounded wait safe here. Never
    terminate(): killing a thread that may hold the GIL can hang the interpreter.
    """
    if worker is None:
        return
    if worker.isRunning():
        worker.cancel()
    worker.wait()
