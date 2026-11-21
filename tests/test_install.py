import hashlib
import os
import tarfile
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from livedub import install

DATA = os.urandom(2_500_000)   # more than one read (1 MiB), so a resume appends across chunks
SHA = hashlib.sha256(DATA).hexdigest()


class Handler(BaseHTTPRequestHandler):
    """/range honours Range, /norange always sends the whole file, anything else is 404."""
    seen: list[tuple[str, str | None]] = []

    def do_GET(self):
        rng = self.headers.get("Range")
        Handler.seen.append((self.path, rng))
        if self.path not in ("/range", "/norange"):
            self.send_error(404)
            return
        start = int(rng.removeprefix("bytes=").rstrip("-")) if rng and self.path == "/range" else 0
        if start >= len(DATA):
            self.send_error(416)
            return
        self.send_response(206 if start else 200)
        self.send_header("Content-Length", str(len(DATA) - start))
        self.end_headers()
        self.wfile.write(DATA[start:])

    def log_message(self, format, *args):
        pass


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv("no_proxy", "127.0.0.1")   # the sandbox's HTTP(S)_PROXY must not catch a local server
    Handler.seen = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()
    srv.server_close()


def job(source="", sources=("huggingface", "hf-mirror")):
    return install.Job("model:test", "测试", len(DATA), list(sources), source)


def test_a_partial_download_resumes_from_where_it_stopped(server, tmp_path):
    dest = tmp_path / "m.gguf"
    (tmp_path / "m.gguf.part").write_bytes(DATA[:1_000_000])
    install.fetch(job("huggingface"), lambda s: server + "/range", dest, len(DATA), SHA)
    assert dest.read_bytes() == DATA and not (tmp_path / "m.gguf.part").exists()
    assert Handler.seen == [("/range", "bytes=1000000-")]


def test_a_server_that_ignores_the_range_is_downloaded_again_from_the_start(server, tmp_path):
    dest = tmp_path / "m.gguf"
    (tmp_path / "m.gguf.part").write_bytes(b"x" * 1_000_000)   # appending to it would give the wrong file
    install.fetch(job("huggingface"), lambda s: server + "/norange", dest, len(DATA), SHA)
    assert dest.read_bytes() == DATA


def test_a_file_that_fails_its_checksum_is_deleted_and_not_used(server, tmp_path):
    dest = tmp_path / "m.gguf"
    with pytest.raises(RuntimeError, match="SHA-256"):
        install.fetch(job("huggingface"), lambda s: server + "/range", dest, len(DATA), "0" * 64)
    assert not dest.exists() and not (tmp_path / "m.gguf.part").exists()


def test_without_a_picked_source_the_next_one_is_tried(server, tmp_path):
    j = job()
    dest = install.fetch(j, lambda s: server + ("/range" if s == "hf-mirror" else "/gone"), tmp_path / "m.gguf",
                         len(DATA), SHA)
    assert dest.read_bytes() == DATA and j.source == "hf-mirror"


def test_a_picked_source_that_fails_is_reported_and_not_switched(server, tmp_path):
    j = job("huggingface")
    with pytest.raises(RuntimeError) as e:
        install.fetch(j, lambda s: server + "/gone", tmp_path / "m.gguf", len(DATA), SHA)
    assert "Hugging Face：服务器返回 404" in str(e.value) and "接着下载" in str(e.value)
    assert j.source == "huggingface" and [p for p, _ in Handler.seen] == ["/gone"]


def test_a_finished_file_of_the_right_size_is_not_downloaded_again(server, tmp_path):
    dest = tmp_path / "m.gguf"
    dest.write_bytes(DATA)
    j = job("huggingface")
    install.fetch(j, lambda s: server + "/range", dest, len(DATA), SHA)
    assert Handler.seen == [] and j.base == len(DATA)


@pytest.mark.skipif(os.name == "nt", reason="file modes")
@pytest.mark.parametrize("kind", ["zip", "tar.gz"])
def test_unpacked_tools_can_be_run(tmp_path, kind):
    src = tmp_path / "llama-server"
    src.write_bytes(b"#!/bin/sh\n")
    archive = tmp_path / f"tool.{kind}"
    if kind == "zip":
        with zipfile.ZipFile(archive, "w") as z:   # zip keeps no modes
            z.write(src, "build/bin/llama-server")
    else:
        src.chmod(0o644)
        with tarfile.open(archive, "w:gz") as t:
            t.add(src, "build/bin/llama-server")
    install.extract(archive, tmp_path / "out")
    assert os.access(tmp_path / "out" / "build" / "bin" / "llama-server", os.X_OK)


@pytest.mark.parametrize("system, arch, cuda, variant, names", [
    ("Windows", "x64", 13.2, "cuda", ["llama-T-bin-win-cuda-13.4-x64.zip", "cudart-llama-bin-win-cuda-13.4-x64.zip"]),
    ("Windows", "x64", 12.6, "cuda", ["llama-T-bin-win-cuda-12.4-x64.zip", "cudart-llama-bin-win-cuda-12.4-x64.zip"]),
    ("Windows", "x64", None, "vulkan", ["llama-T-bin-win-vulkan-x64.zip"]),
    ("Linux", "x64", 12.6, "cuda", ["llama-T-bin-ubuntu-cuda-12.8-x64.tar.gz",
                                    "cudart-llama-T-bin-ubuntu-cuda-12.8-x64.tar.gz"]),
    ("Linux", "arm64", 13.0, "vulkan", ["llama-T-bin-ubuntu-vulkan-arm64.tar.gz"]),   # CUDA builds are x64 only
    ("Darwin", "arm64", None, "metal", ["llama-T-bin-macos-arm64.tar.gz"]),
])
def test_the_llama_build_matches_the_machine(monkeypatch, system, arch, cuda, variant, names):
    monkeypatch.setattr(install.platform, "system", lambda: system)
    monkeypatch.setattr(install, "_arch", lambda: arch)
    monkeypatch.setattr(install, "driver_cuda", lambda: cuda)
    assert install.llama_files("T") == (variant, names)


def test_the_driver_cuda_version_is_read_from_nvidia_smi(monkeypatch):
    class Out:
        stdout = "| NVIDIA-SMI 580.88   Driver Version: 580.88   CUDA Version: 13.2     |"
    monkeypatch.setattr(install.subprocess, "run", lambda *a, **k: Out())
    assert install.driver_cuda() == 13.2

    def missing(*a, **k):
        raise FileNotFoundError("nvidia-smi")
    monkeypatch.setattr(install.subprocess, "run", missing)
    assert install.driver_cuda() is None
