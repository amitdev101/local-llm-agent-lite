import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from model_downloads import DownloadError, ModelDownloads, model_name, repository_id

PAYLOAD = b"GGUF" + bytes(196604)
COMMIT = "a" * 40


class ChunkStream(httpx.SyncByteStream):
    def __init__(self, data, fail=False, gate=None, reached=None):
        self.data, self.fail, self.gate, self.reached = data, fail, gate, reached

    def __iter__(self):
        yield self.data[:65536]
        if self.reached:
            self.reached.set()
            if not self.gate.wait(timeout=3):
                raise httpx.ReadTimeout("Test gate timed out")
        if self.fail:
            raise httpx.ReadError("Connection interrupted")
        yield self.data[65536:]


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent, prefix=".downloads-test-")
        self.root = Path(self.temp.name).resolve(); self.root.relative_to(Path(__file__).parent.resolve())
        self.models = self.root / "models"; self.models.mkdir()
        self.requests = []; self.fail_once = False; self.ignore_range = False; self.bad_range = False
        self.payload = PAYLOAD; self.checksum = hashlib.sha256(PAYLOAD).hexdigest()
        self.gate = threading.Event(); self.reached = threading.Event(); self.gated = False
        self.transport = httpx.MockTransport(self.respond)
        self.manager = ModelDownloads(self.models, self.root / "downloads.json", self.transport)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.stop_worker)

    def stop_worker(self):
        self.gate.set(); self.manager.stop.set()
        if self.manager.worker:
            self.manager.worker.join(timeout=4)

    def respond(self, request):
        self.requests.append(request)
        if request.url.path.startswith("/api/models/"):
            return httpx.Response(200, json={"sha": COMMIT, "siblings": [
                {"rfilename": "tiny.gguf", "size": len(self.payload), "lfs": {"size": len(self.payload), "sha256": self.checksum}},
                {"rfilename": "weights.safetensors", "size": 999},
                {"rfilename": "large-00001-of-00002.gguf", "size": 999},
                {"rfilename": "../outside.gguf", "size": 999},
            ]})
        offset = int(request.headers.get("range", "bytes=0-").split("=")[1].rstrip("-"))
        ranged = offset and not self.ignore_range
        data = self.payload[offset:] if ranged else self.payload
        headers = {"Content-Range": f"bytes {offset + int(self.bad_range)}-{len(self.payload)-1}/{len(self.payload)}"} if ranged else {}
        fail = self.fail_once; self.fail_once = False
        gated = self.gated; self.gated = False
        return httpx.Response(206 if ranged else 200, headers=headers,
                              stream=ChunkStream(data, fail, self.gate if gated else None, self.reached if gated else None))

    def start(self, **kwargs):
        return self.manager.start("public/tiny", "tiny.gguf", **kwargs)

    def finished(self):
        self.manager.worker.join(timeout=4)
        self.assertFalse(self.manager.worker.is_alive())
        return self.manager.snapshot()["jobs"][0]

    def test_metadata_lists_only_complete_gguf_and_pins_commit(self):
        result = self.manager.inspect("https://huggingface.co/public/tiny/tree/main")
        self.assertEqual(result["revision"], COMMIT)
        self.assertEqual([row["filename"] for row in result["files"]], ["tiny.gguf"])
        self.assertEqual(result["files"][0]["sha256"], self.checksum)

    def test_completed_download_is_verified_published_and_retained_after_restart(self):
        self.start(); job = self.finished()
        self.assertEqual(job["status"], "complete")
        self.assertEqual((self.models / "tiny.gguf").read_bytes(), PAYLOAD)
        self.assertFalse(self.manager._part(job["id"]).exists())
        restarted = ModelDownloads(self.models, self.root / "downloads.json", self.transport)
        self.assertEqual(restarted.snapshot()["jobs"][0]["status"], "complete")

    def test_repository_url_revision_is_respected_until_a_commit_is_pinned(self):
        self.manager.inspect("https://huggingface.co/public/tiny/tree/v2")
        self.assertEqual(self.requests[-1].url.path, "/api/models/public/tiny/revision/v2")
        self.manager.inspect("https://huggingface.co/public/tiny/blob/v2/tiny.gguf", COMMIT)
        self.assertTrue(self.requests[-1].url.path.endswith("/revision/" + COMMIT))

    def test_gated_metadata_explains_that_access_is_required(self):
        manager = ModelDownloads(self.models, self.root / "gated.json", httpx.MockTransport(
            lambda request: httpx.Response(200, json={"sha": COMMIT, "siblings": [], "gated": "auto"})))
        with self.assertRaises(DownloadError) as error:
            manager.inspect("public/gated")
        self.assertEqual(error.exception.status, 403)
        self.assertIn("sign-in", str(error.exception))

    def test_network_failure_resumes_at_the_saved_byte_offset(self):
        self.fail_once = True; job = self.start(); self.assertEqual(self.finished()["status"], "error")
        self.assertEqual(self.manager._part(job["id"]).stat().st_size, 65536)
        self.manager.control(job["id"], "resume")
        self.assertEqual(self.finished()["status"], "complete")
        self.assertEqual(self.requests[-1].headers["range"], "bytes=65536-")
        self.assertIn(COMMIT, self.requests[-1].url.path)

    def test_ignored_range_restarts_instead_of_appending_duplicate_bytes(self):
        self.fail_once = True; job = self.start(); self.finished()
        self.ignore_range = True; self.manager.control(job["id"], "resume"); self.finished()
        self.assertEqual((self.models / "tiny.gguf").read_bytes(), PAYLOAD)

    def test_invalid_resume_range_does_not_modify_the_partial_file(self):
        self.fail_once = True; job = self.start(); self.finished()
        before = self.manager._part(job["id"]).read_bytes()
        self.bad_range = True; self.manager.control(job["id"], "resume")
        self.assertEqual(self.finished()["status"], "error")
        self.assertEqual(self.manager._part(job["id"]).read_bytes(), before)

    def test_pause_and_resume_keep_the_partial_file(self):
        self.gated = True; job = self.start(); self.assertTrue(self.reached.wait(timeout=2))
        self.manager.control(job["id"], "pause"); self.gate.set()
        self.assertEqual(self.finished()["status"], "paused")
        self.assertEqual(self.manager._part(job["id"]).stat().st_size, 65536)
        self.assertFalse((self.models / "tiny.gguf").exists())
        self.manager.control(job["id"], "resume")
        self.assertEqual(self.finished()["status"], "complete")

    def test_cancel_removes_only_its_partial_file(self):
        other = self.models / "existing.gguf"; other.write_bytes(b"Existing model")
        self.fail_once = True; job = self.start(); self.finished()
        self.manager.control(job["id"], "cancel")
        self.assertFalse(self.manager._part(job["id"]).exists())
        self.assertEqual(other.read_bytes(), b"Existing model")
        self.assertEqual(self.manager.snapshot()["jobs"][0]["status"], "cancelled")

    def test_restart_marks_interrupted_job_paused_and_can_resume(self):
        self.fail_once = True; job = self.start(); self.finished()
        rows = json.loads(self.manager.state_path.read_text(encoding="utf-8")); rows[0]["status"] = "downloading"
        self.manager.state_path.write_text(json.dumps(rows), encoding="utf-8")
        self.manager = ModelDownloads(self.models, self.root / "downloads.json", self.transport)
        self.assertEqual(self.manager.snapshot()["jobs"][0]["status"], "paused")
        self.manager.control(job["id"], "resume")
        self.assertEqual(self.finished()["status"], "complete")

    def test_checksum_failure_does_not_publish_a_model_or_keep_corrupt_data(self):
        self.checksum = "0" * 64; self.start(); job = self.finished()
        self.assertEqual(job["status"], "error")
        self.assertIn("checksum", job["message"])
        self.assertFalse((self.models / "tiny.gguf").exists())
        self.assertFalse(self.manager._part(job["id"]).exists())

    def test_invalid_header_is_rejected_even_with_matching_hash(self):
        self.payload = b"HTML" + bytes(100); self.checksum = hashlib.sha256(self.payload).hexdigest()
        self.start(); job = self.finished()
        self.assertEqual(job["status"], "error")
        self.assertFalse((self.models / "tiny.gguf").exists())
        self.assertFalse(self.manager._part(job["id"]).exists())

    def test_existing_models_are_never_overwritten(self):
        destination = self.models / "tiny.gguf"; destination.write_bytes(b"Keep this model")
        with self.assertRaises(DownloadError) as error:
            self.start()
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(destination.read_bytes(), b"Keep this model")
        self.start(save_name="another-name.gguf"); self.finished()
        self.assertEqual(destination.read_bytes(), b"Keep this model")
        self.assertEqual((self.models / "another-name.gguf").read_bytes(), PAYLOAD)

    def test_one_download_at_a_time(self):
        self.gated = True; self.start(); self.assertTrue(self.reached.wait(timeout=2))
        with self.assertRaises(DownloadError) as error:
            self.start(save_name="second.gguf")
        self.assertEqual(error.exception.status, 409)
        self.gate.set(); self.finished()

    def test_insufficient_space_stops_before_creating_a_job(self):
        with patch("model_downloads.shutil.disk_usage", return_value=types_space(0)):
            with self.assertRaises(DownloadError):
                self.start()
        self.assertEqual(self.manager.snapshot()["jobs"], [])

    def test_untrusted_repositories_and_destination_paths_are_rejected(self):
        for value in (None, 42, "http://huggingface.co/public/tiny", "https://example.com/model", "../../etc", "https://user:secret@huggingface.co/public/tiny"):
            with self.assertRaises(DownloadError): repository_id(value)
        for value in ("../bad.gguf", "C:\\bad.gguf", "CON.gguf", "not-a-model.txt"):
            with self.assertRaises(DownloadError): model_name(value)
        self.assertEqual(self.requests, [])

    def test_redirects_outside_supported_hosts_are_rejected_before_following(self):
        requests = []
        def redirect(request):
            requests.append(request)
            return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})
        manager = ModelDownloads(self.models, self.root / "other.json", httpx.MockTransport(redirect))
        with self.assertRaises(DownloadError): manager.inspect("public/tiny")
        self.assertEqual(len(requests), 1)


def types_space(free):
    from collections import namedtuple
    return namedtuple("Space", "total used free")(100, 100 - free, free)


if __name__ == "__main__":
    unittest.main()
