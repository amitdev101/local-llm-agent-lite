"""Keep native model allocation in a process that can be stopped during loading."""
import multiprocessing
import os
import threading

import psutil


def _serve(connection, settings, parent_pid, parent_created):
    # Also release memory if the launcher forcibly replaces the server on Windows.
    def watch_parent():
        while True:
            try:
                if psutil.Process(parent_pid).create_time() != parent_created:
                    break
            except psutil.NoSuchProcess:
                break
            except psutil.AccessDenied:
                pass
            threading.Event().wait(1)
        os._exit(0)

    threading.Thread(target=watch_parent, daemon=True, name="model-parent-watch").start()
    model = None
    try:
        from llama_cpp import Llama
        model = Llama(**settings)
        connection.send(("ready", None))
        while True:
            operation, value = connection.recv()
            try:
                if operation == "tokenize":
                    connection.send(("result", model.tokenize(value)))
                elif operation == "generate":
                    chunks = model.create_chat_completion(**value)
                    try:
                        for chunk in chunks:
                            connection.send(("chunk", chunk))
                            # Pull one chunk at a time: stopping a response leaves no
                            # unread output that could corrupt the next token count.
                            action, _ = connection.recv()
                            if action == "stop":
                                break
                            if action != "next":
                                raise RuntimeError("Invalid model stream command")
                    finally:
                        chunks.close()
                    connection.send(("done", None))
                else:
                    raise RuntimeError("Unknown model command")
            except Exception as error:
                connection.send(("error", str(error)))
    except (EOFError, BrokenPipeError, ConnectionResetError):
        pass
    except Exception as error:
        try:
            connection.send(("error", str(error)))
        except (EOFError, OSError):
            pass
    finally:
        if model is not None:
            model.close()
        connection.close()


class ProcessModel:
    """Small synchronous proxy; ModelRuntime serializes inference and loading."""

    def __init__(self, **settings):
        context = multiprocessing.get_context("spawn")
        self.connection, child = context.Pipe()
        self._close_lock = threading.Lock()
        self._closed = False
        parent = psutil.Process()
        self.process = context.Process(target=_serve, args=(child, settings, parent.pid, parent.create_time()),
                                       name="playground-model", daemon=True)
        try:
            self.process.start()
        except BaseException:
            self.connection.close()
            raise
        finally:
            child.close()

    @property
    def alive(self):
        return not self._closed and self.process.is_alive()

    def _receive(self):
        try:
            kind, value = self.connection.recv()
        except (EOFError, OSError) as error:
            raise RuntimeError("The model worker stopped. Load the model again to continue.") from error
        if kind == "error":
            raise RuntimeError(value)
        return kind, value

    def wait_ready(self):
        kind, _ = self._receive()
        if kind != "ready":
            raise RuntimeError("The model worker did not finish loading")

    def tokenize(self, value):
        self.connection.send(("tokenize", value))
        kind, result = self._receive()
        if kind != "result":
            raise RuntimeError("Invalid tokenizer response")
        return result

    def create_chat_completion(self, **kwargs):
        self.connection.send(("generate", kwargs))
        waiting = False
        try:
            while True:
                kind, chunk = self._receive()
                if kind == "done":
                    return
                if kind != "chunk":
                    raise RuntimeError("Invalid model response")
                waiting = True
                yield chunk
                self.connection.send(("next", None))
                waiting = False
        finally:
            if waiting and self.alive:
                self.connection.send(("stop", None))
                kind, _ = self._receive()
                if kind != "done":
                    raise RuntimeError("The model stream did not stop cleanly")

    def close(self):
        # Independent of recv(): cancellation must interrupt a blocked native load.
        with self._close_lock:
            if self._closed:
                return
            if self.process.is_alive():
                self.process.terminate()
            self.process.join(timeout=3)
            if self.process.is_alive():
                self.process.kill()
                self.process.join(timeout=3)
            if self.process.is_alive():
                raise RuntimeError("The model worker could not be stopped. Try Unload again.")
            self.connection.close()
            self._closed = True
            self.process.close()
