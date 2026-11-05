from app.services.paddle_runtime import default_paddle_packages
from app.services import paddle_runtime


def test_paddle_runtime_uses_official_blackwell_cuda_129_wheel():
    assert default_paddle_packages(
        system="linux", machine="x86_64", driver_cuda="13.0"
    ) == [
        "paddlepaddle-gpu==3.2.1",
        "-i",
        "https://www.paddlepaddle.org.cn/packages/stable/cu129/",
    ]


def test_paddle_runtime_selects_supported_cuda_wheel_from_driver():
    assert default_paddle_packages(
        system="linux", machine="x86_64", driver_cuda="12.8"
    )[-1].endswith("/cu126/")
    assert default_paddle_packages(
        system="linux", machine="x86_64", driver_cuda="11.8"
    )[-1].endswith("/cu118/")


def test_paddle_runtime_falls_back_to_cpu_without_cuda():
    assert default_paddle_packages(
        system="linux", machine="x86_64", driver_cuda=None
    ) == ["paddlepaddle==3.2.1"]


def test_paddle_runtime_uses_apple_silicon_cpu_wheel():
    assert default_paddle_packages(
        system="darwin", machine="arm64", driver_cuda="13.0"
    ) == ["paddlepaddle==3.3.1"]


def test_paddle_install_does_not_recreate_venv_after_opening_log(tmp_path, monkeypatch):
    created = []
    probes = iter((False, True))

    class Builder:
        def __init__(self, **_kwargs):
            pass

        def create(self, target):
            created.append(target)
            (tmp_path / "bin").mkdir(exist_ok=True)

    monkeypatch.setattr(paddle_runtime, "_dir", lambda: tmp_path)
    monkeypatch.setattr(paddle_runtime.venv, "EnvBuilder", Builder)
    monkeypatch.setattr(paddle_runtime, "ready", lambda: next(probes))
    # This test covers install ordering, not host CUDA detection. Keep the
    # package choice deterministic instead of letting the subprocess.run mock
    # below intercept the nvidia-smi probe on Linux.
    monkeypatch.setattr(
        paddle_runtime, "default_paddle_packages", lambda: ["paddlepaddle==3.2.1"]
    )
    monkeypatch.setattr(paddle_runtime.subprocess, "run", lambda *_args, **_kwargs: None)

    paddle_runtime.ensure_ready()

    assert created == [str(tmp_path)]
    assert "installazione PaddleOCR document parser" in paddle_runtime.log_path().read_text()


def test_two_installs_at_once_build_the_runtime_once(monkeypatch, tmp_path):
    """paddleocr-vl and xiaomi-ocr-0 share this runtime and both start its install
    when the model list loads: two `EnvBuilder(clear=True)` on one folder break it."""
    import threading
    import time as _time

    from app.services import paddle_runtime

    installed = threading.Event()
    builds: list[int] = []

    class Builder:
        def __init__(self, **_kwargs):
            pass

        def create(self, _path):
            builds.append(1)
            _time.sleep(0.2)

    monkeypatch.setattr(paddle_runtime, "_dir", lambda: tmp_path / "paddle-runtime")
    monkeypatch.setattr(paddle_runtime, "ready", lambda **_k: installed.is_set())
    monkeypatch.setattr(paddle_runtime.venv, "EnvBuilder", Builder)
    monkeypatch.setattr(paddle_runtime.subprocess, "run", lambda *a, **k: installed.set())
    monkeypatch.setattr(paddle_runtime, "_write_state", lambda **_k: None)
    threads = [threading.Thread(target=paddle_runtime.ensure_ready) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert builds == [1]
