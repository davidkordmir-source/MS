"""Keep GDAL polygonization and its Python GIL/CPU load outside the Qt process."""
from dataclasses import replace
import multiprocessing
from pathlib import Path
import tempfile
import time
import traceback

from terrain_export.engine import CancelledError


SIDECARS = ('.shp', '.shx', '.dbf', '.prj', '.cpg', '.json')


def _extract_child(options, connection):
    # Import in the child, never pass Qt objects or open GDAL datasets across processes.
    from terrain_export.engine import extract
    last_progress = 0.
    stopped = False

    def progress(message):
        nonlocal last_progress
        now = time.monotonic()
        if message.startswith(('Writing polygons:', 'Applying elevation filter:')):
            if now-last_progress < .1:
                return
            last_progress = now
        connection.send(('progress', message))

    def cancelled():
        nonlocal stopped
        if connection.poll():
            stopped = connection.recv() == 'cancel'
        return stopped

    try:
        count = extract(options, progress, cancelled)
        connection.send(('success', count))
    except CancelledError:
        connection.send(('cancelled', None))
    except Exception as exc:
        connection.send(('error', (type(exc).__name__, str(exc), traceback.format_exc())))
    finally:
        connection.close()


def extract_isolated(options, progress=lambda message: None, cancelled=lambda: False):
    """Publish only completed results; cancelled/crashed children leave no output behind."""
    if cancelled():
        raise CancelledError()
    output = Path(options.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if any(output.with_suffix(suffix).exists() for suffix in SIDECARS):
        raise ValueError('Choose a new output name; existing datasets will not be overwritten.')
    context = multiprocessing.get_context('spawn')  # Windows and frozen executable compatible.
    with tempfile.TemporaryDirectory(prefix='terrain-export-', dir=output.parent) as folder:
        staged = Path(folder)/output.name
        parent, child = context.Pipe()
        process = context.Process(target=_extract_child, args=(replace(options, output=str(staged)), child))
        count = None
        cancel_time = None
        try:
            process.start()
            child.close()
            while True:
                if cancelled():
                    if cancel_time is None:
                        cancel_time = time.monotonic()
                        try:
                            parent.send('cancel')
                        except (BrokenPipeError, OSError):
                            # The child may have finished at the same instant as Cancel.
                            raise CancelledError()
                    elif time.monotonic()-cancel_time >= .5:
                        # GDAL cannot check cancellation during a native polygonization call.
                        # All of its writes are confined to our temporary directory.
                        raise CancelledError()
                if parent.poll(.05):
                    try:
                        kind, payload = parent.recv()
                    except EOFError:
                        if cancel_time is not None: raise CancelledError()
                        raise RuntimeError('Extraction process exited without a result.')
                    if kind == 'progress':
                        progress(payload)
                    elif kind == 'success':
                        count = payload
                        break
                    elif kind == 'cancelled':
                        raise CancelledError()
                    elif kind == 'error':
                        error_kind, message, details = payload
                        progress(details)
                        if error_kind == 'PermissionError': raise PermissionError(message)
                        raise RuntimeError(message)
                elif not process.is_alive() and not parent.poll():
                    if cancel_time is not None: raise CancelledError()
                    raise RuntimeError(f'Extraction process exited unexpectedly (code {process.exitcode}).')
        finally:
            child.close()
            if process.pid is not None:
                # Never let a stuck native call keep the UI's worker or shutdown waiting.
                process.join(timeout=.2)
                if process.is_alive():
                    process.terminate(); process.join(timeout=2)
                if process.is_alive():
                    process.kill(); process.join()
                process.close()
            parent.close()
        if cancelled():
            raise CancelledError()
        published = []
        try:
            for suffix in SIDECARS:
                source, target = staged.with_suffix(suffix), output.with_suffix(suffix)
                if target.exists():
                    raise ValueError('Output appeared during processing; choose a different name.')
                source.rename(target)
                published.append(target)
        except Exception:
            for target in published: target.unlink()
            raise
    return count
