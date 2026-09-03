"""Test per il serving locale pluggable (Fase 2 multi-modello).

Il sottoprocesso vero (`vllm serve`, `scripts/serve_model.sh`) non gira in
CI: sostituiamo `subprocess.Popen` con un finto processo di lunga vita
(`sleep`), così `serve_manager` viene esercitato per intero (avvio, stato,
stop-before-start, arresto) senza dipendere da pesi scaricati o GPU.
"""
from __future__ import annotations

import shutil
import sys
import time

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db import init_db
from app.services import model_registry, serve_manager


@pytest.fixture(autouse=True)
def _stop_any_active_server():
    init_db()
    yield
    serve_manager.stop()


def _wait_for(poll, done, timeout: float = 10.0) -> dict:
    """Attende che lo stato soddisfi `done` (chiave vera, o predicato)."""
    check = done if callable(done) else (lambda s: bool(s.get(done)))
    deadline = time.time() + timeout
    state: dict = {}
    while time.time() < deadline:
        state = poll()
        if check(state):
            return state
        time.sleep(0.2)
    raise AssertionError(f"stato non raggiunto entro {timeout}s: {state}")


def _fake_serve_argv(adapter_id: str) -> list[str]:
    """Comando lungo che si attribuisce a noi come farebbe un serve reale.

    `stop()` riconosce i propri processi dalla riga di comando
    (`_is_our_serving_process`), e ogni `serve_command` reale contiene il path
    del modello: un `sleep` nudo non è un server nostro e non va terminato.
    """
    return [
        sys.executable,
        "-c",
        "import time; time.sleep(30)",
        str(model_registry.models_dir(adapter_id)),
    ]


def _fake_installed(adapter_id: str) -> None:
    """Crea i marker minimi che `model_registry.is_installed` richiede."""
    d = model_registry.models_dir(adapter_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text("{}", encoding="utf-8")
    (d / "model.safetensors").write_bytes(b"\x00")


def test_start_refuses_uninstalled_model():
    with pytest.raises(ValueError, match="non è installato"):
        serve_manager.start("mineru2.5", port=18888)


def test_start_refuses_adapter_without_serve_command(monkeypatch):
    # Tutti gli 8 adapter predefiniti hanno ormai un `serve_command` (locale
    # via vLLM/Docker): il caso "nessun comando di serving" resta comunque un
    # ramo reale di `serve_manager.start` (es. un adapter futuro di sola
    # ricetta) e va coperto forzandolo via monkeypatch.
    _fake_installed("glm-ocr")
    monkeypatch.setattr(
        "app.services.model_adapters.GlmOcrAdapter.serve_command",
        lambda self, model_path, port: None,
    )
    with pytest.raises(ValueError, match="comando di serving"):
        serve_manager.start("glm-ocr", port=18888)


def test_start_stop_lifecycle(monkeypatch):
    _fake_installed("mineru2.5")

    long_lived = _fake_serve_argv("mineru2.5")
    monkeypatch.setattr(
        "app.services.model_adapters.MinerU2_5Adapter.serve_command",
        lambda self, model_path, port: long_lived,
    )

    status = serve_manager.start("mineru2.5", port=18888)
    assert status.running is True
    assert status.adapter_id == "mineru2.5"
    assert status.pid is not None

    live = serve_manager.get_status()
    assert live.running is True
    assert live.adapter_id == "mineru2.5"

    stopped = serve_manager.stop()
    assert stopped.running is False


def test_start_stops_previous_server_first(monkeypatch):
    _fake_installed("mineru2.5")
    _fake_installed("dots-ocr")
    long_lived = _fake_serve_argv("dots-ocr")
    monkeypatch.setattr(
        "app.services.model_adapters.MinerU2_5Adapter.serve_command",
        lambda self, model_path, port: long_lived,
    )
    monkeypatch.setattr(
        "app.services.model_adapters.DotsOcrAdapter.serve_command",
        lambda self, model_path, port: long_lived,
    )

    first = serve_manager.start("mineru2.5", port=18888)
    first_pid = first.pid
    second = serve_manager.start("dots-ocr", port=18889)

    assert second.adapter_id == "dots-ocr"
    # Il primo processo deve essere stato terminato (stop-before-start), non
    # solo dimenticato: os.kill con segnale 0 solleva se il pid non esiste più.
    import os

    with pytest.raises(ProcessLookupError):
        os.kill(first_pid, 0)


def test_status_and_stop_recover_persisted_server(monkeypatch):
    _fake_installed("mineru2.5")
    long_lived = _fake_serve_argv("mineru2.5")
    monkeypatch.setattr(
        "app.services.model_adapters.MinerU2_5Adapter.serve_command",
        lambda self, model_path, port: long_lived,
    )

    started = serve_manager.start("mineru2.5", port=18887)
    assert started.running is True
    # Simula il processo Python del backend che viene riavviato: il server
    # resta vivo e viene ritrovato dalla riga persistita in jobs.
    serve_manager._ACTIVE_PROC = None
    serve_manager._ACTIVE_INFO = {}
    recovered = serve_manager.get_status()
    assert recovered.running is True
    assert recovered.pid == started.pid
    assert recovered.port == 18887

    stopped = serve_manager.stop()
    assert stopped.running is False


def test_reconcile_marks_dead_persisted_server_failed():
    init_db()
    from app.db import connect

    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO jobs(kind, provider, pid, process_group, state, command_json, "
            "recovery_strategy) VALUES('serve', 'local', 99999999, 99999999, 'running', ?, 'pid-process-group')",
            ('{"adapter_id":"mineru2.5","port":18886}',),
        )
        job_id = cur.lastrowid

    serve_manager.reconcile_jobs()

    with connect() as conn:
        row = conn.execute("SELECT state, error FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["state"] == "failed"
    assert "non più presente" in row["error"]


def test_start_endpoint_activates_inference_config(monkeypatch):
    _fake_installed("mineru2.5")
    long_lived = _fake_serve_argv("mineru2.5")
    monkeypatch.setattr(
        "app.services.model_adapters.MinerU2_5Adapter.serve_command",
        lambda self, model_path, port: long_lived,
    )

    with TestClient(app) as client:
        # L'avvio è asincrono (202): la preparazione dell'ambiente può durare
        # minuti e non sta dentro una richiesta HTTP. La rotta risponde subito
        # «preso in carico», e lo stato racconta il resto.
        res = client.post("/api/models/mineru2.5/serve/start", json={"port": 18890})
        assert res.status_code == 202
        data = res.json()
        assert data["starting"] is True
        assert data["adapter_id"] == "mineru2.5"

        from app.services import inference as infmod

        cfg = infmod.get_inference_config()
        assert cfg["adapter_id"] == "mineru2.5"
        assert cfg["url"] == "http://127.0.0.1:18890/v1"
        assert cfg["model"] == "mineru2.5"

        status = _wait_for(lambda: client.get("/api/models/serve/status").json(), "running")
        assert status["running"] is True
        # Processo vivo non vuol dire endpoint che risponde: finché la porta
        # tace, la fase resta «loading» e la UI non promette un server pronto.
        assert status["phase"] == "loading"
        assert status["ready"] is False

        stop_res = client.post("/api/models/serve/stop")
        assert stop_res.status_code == 200
        assert stop_res.json()["running"] is False


def test_start_reports_failure_through_status(monkeypatch):
    """Un avvio fallito deve arrivare a chi guarda: la POST è già tornata."""
    _fake_installed("mineru2.5")
    monkeypatch.setattr(
        "app.services.model_adapters.MinerU2_5Adapter.serve_command",
        lambda self, model_path, port: ["/non/esiste/affatto"],
    )

    with TestClient(app) as client:
        res = client.post("/api/models/mineru2.5/serve/start", json={"port": 18895})
        assert res.status_code == 202

        status = _wait_for(
            lambda: client.get("/api/models/serve/status").json(),
            lambda s: s["phase"] == "failed",
        )
        assert status["running"] is False
        assert status["error"]

        # Il tentativo fallito non blocca il successivo.
        again = client.post("/api/models/mineru2.5/serve/start", json={"port": 18895})
        assert again.status_code == 202


def test_generic_vllm_adapter_auto_provisions_runtime_when_missing(monkeypatch):
    # Nessun passaggio manuale: se vLLM non è sul PATH e non c'è un override
    # esplicito, `start()` deve preparare da sé l'ambiente condiviso prima
    # di lanciare il sottoprocesso — mai chiedere all'utente di installarlo.
    _fake_installed("mineru2.5")
    monkeypatch.setattr(
        "app.services.model_adapters.MinerU2_5Adapter.serve_command",
        lambda self, model_path, port: ["vllm", "serve", model_path],
    )
    monkeypatch.setattr("app.services.serve_manager.shutil.which", lambda name: None)
    calls: list[str] = []
    monkeypatch.setattr(
        "app.services.serve_manager.local_runtime.ensure_ready",
        lambda: calls.append("ensure_ready"),
    )
    monkeypatch.setattr("app.services.serve_manager.local_runtime.is_ready", lambda: True)
    monkeypatch.setattr(
        "app.services.serve_manager.local_runtime.bin_dir",
        lambda: __import__("pathlib").Path("/fake/vllm-runtime/bin"),
    )
    captured: dict = {}

    class FakeProc:
        def __init__(self, *args, **kwargs):
            captured["argv"] = args[0]
            captured["env"] = kwargs.get("env")
            self.pid = 4242

        def poll(self):
            return None

    monkeypatch.setattr("app.services.serve_manager.subprocess.Popen", FakeProc)

    serve_manager.start("mineru2.5", port=18891)

    assert calls == ["ensure_ready"]
    assert captured["env"]["PATH"].startswith("/fake/vllm-runtime/bin")
    assert captured["env"]["NVCC_PREPEND_FLAGS"] == "-allow-unsupported-compiler"


def test_generic_vllm_adapter_skips_provisioning_when_already_on_path(monkeypatch):
    _fake_installed("mineru2.5")
    monkeypatch.setattr(
        "app.services.model_adapters.MinerU2_5Adapter.serve_command",
        lambda self, model_path, port: ["vllm", "serve", model_path],
    )
    monkeypatch.setattr("app.services.serve_manager.shutil.which", lambda name: "/usr/bin/vllm")
    calls: list[str] = []
    monkeypatch.setattr(
        "app.services.serve_manager.local_runtime.ensure_ready",
        lambda: calls.append("ensure_ready"),
    )

    class FakeProc:
        def __init__(self, *args, **kwargs):
            self.pid = 4243

        def poll(self):
            return None

    monkeypatch.setattr("app.services.serve_manager.subprocess.Popen", FakeProc)

    serve_manager.start("mineru2.5", port=18892)

    assert calls == []


def test_monkeyocrv2_auto_prepares_repo_and_runtime_when_unset(monkeypatch):
    _fake_installed("monkeyocrv2-parsing")
    monkeypatch.setattr("app.services.serve_manager.config.TRAIN_REPO", "")
    monkeypatch.setattr("app.services.serve_manager.config.TRAIN_PYTHON", "")
    monkeypatch.setattr("app.services.serve_manager.config.SERVE_PYTHON", "")
    calls: list[str] = []
    monkeypatch.setattr(
        "app.services.serve_manager.vendor_repos.ensure_monkeyocrv2_repo",
        lambda: (calls.append("repo"), __import__("pathlib").Path("/fake/vendor/MonkeyOCRv2"))[1],
    )
    monkeypatch.setattr(
        "app.services.serve_manager.local_runtime.ensure_ready",
        lambda: calls.append("runtime"),
    )
    monkeypatch.setattr(
        "app.services.serve_manager.local_runtime.python_bin",
        lambda: __import__("pathlib").Path("/fake/vllm-runtime/bin/python"),
    )
    monkeypatch.setattr(
        "app.services.serve_manager.ensure_draft",
        lambda adapter_id: (calls.append("draft"), __import__("pathlib").Path("/fake/models/draft"))[1],
    )
    captured: dict = {}

    class FakeProc:
        def __init__(self, *args, **kwargs):
            captured["env"] = kwargs.get("env")
            self.pid = 4244

        def poll(self):
            return None

    monkeypatch.setattr("app.services.serve_manager.subprocess.Popen", FakeProc)

    serve_manager.start("monkeyocrv2-parsing", port=18893)

    assert "repo" in calls and "runtime" in calls and "draft" in calls
    assert captured["env"]["TABULARIUM_TRAIN_REPO"] == "/fake/vendor/MonkeyOCRv2"
    assert captured["env"]["TABULARIUM_TRAIN_PYTHON"] == "/fake/vllm-runtime/bin/python"
    # DFlash ufficiale: il draft arriva a `scripts/serve_model.sh` via env, che
    # lo gira a `serve.py -d` (README §vLLM Serving).
    assert captured["env"]["TABULARIUM_MONKEY_DFLASH_DRAFT"] == "/fake/models/draft"


def test_monkeyocrv2_respects_existing_manual_overrides(monkeypatch):
    # Chi ha già un checkout/ambiente proprio non deve vederselo sostituire.
    _fake_installed("monkeyocrv2-parsing")
    monkeypatch.setattr("app.services.serve_manager.config.TRAIN_REPO", "/my/own/MonkeyOCRv2")
    monkeypatch.setattr("app.services.serve_manager.config.TRAIN_PYTHON", "/my/own/env/bin/python")
    calls: list[str] = []
    monkeypatch.setattr(
        "app.services.serve_manager.vendor_repos.ensure_monkeyocrv2_repo",
        lambda: calls.append("repo"),
    )
    monkeypatch.setattr(
        "app.services.serve_manager.local_runtime.ensure_ready",
        lambda: calls.append("runtime"),
    )
    monkeypatch.setattr(
        "app.services.serve_manager.ensure_draft",
        lambda adapter_id: (calls.append("draft"), None)[1],
    )

    class FakeProc:
        def __init__(self, *args, **kwargs):
            self.pid = 4245

        def poll(self):
            return None

    monkeypatch.setattr("app.services.serve_manager.subprocess.Popen", FakeProc)

    serve_manager.start("monkeyocrv2-parsing", port=18894)

    # Il draft DFlash è indipendente dagli override manuali di repo/ambiente:
    # chi porta il proprio checkout vuole comunque l'accelerazione ufficiale.
    assert calls == ["draft"]


def test_monkeyocrv2_serves_without_dflash_when_draft_unavailable(monkeypatch):
    """Il draft è un'accelerazione, non un requisito: se manca si serve uguale."""
    _fake_installed("monkeyocrv2-parsing")
    monkeypatch.setattr("app.services.serve_manager.config.TRAIN_REPO", "/my/own/MonkeyOCRv2")
    monkeypatch.setattr("app.services.serve_manager.config.TRAIN_PYTHON", "/my/own/env/bin/python")
    monkeypatch.setattr("app.services.serve_manager.ensure_draft", lambda adapter_id: None)
    captured: dict = {}

    class FakeProc:
        def __init__(self, *args, **kwargs):
            captured["env"] = kwargs.get("env")
            self.pid = 4246

        def poll(self):
            return None

    monkeypatch.setattr("app.services.serve_manager.subprocess.Popen", FakeProc)

    serve_manager.start("monkeyocrv2-parsing", port=18895)

    assert "TABULARIUM_MONKEY_DFLASH_DRAFT" not in captured["env"]


def test_monkeyocrv2_skips_draft_when_dflash_disabled(monkeypatch):
    _fake_installed("monkeyocrv2-parsing")
    monkeypatch.setattr("app.services.serve_manager.config.TRAIN_REPO", "/my/own/MonkeyOCRv2")
    monkeypatch.setattr("app.services.serve_manager.config.TRAIN_PYTHON", "/my/own/env/bin/python")
    monkeypatch.setattr("app.services.serve_manager.config.MONKEY_DFLASH", False)
    calls: list[str] = []
    monkeypatch.setattr(
        "app.services.serve_manager.ensure_draft",
        lambda adapter_id: calls.append("draft"),
    )

    class FakeProc:
        def __init__(self, *args, **kwargs):
            self.pid = 4247

        def poll(self):
            return None

    monkeypatch.setattr("app.services.serve_manager.subprocess.Popen", FakeProc)

    serve_manager.start("monkeyocrv2-parsing", port=18896)

    assert calls == []


def test_start_async_retries_without_dflash_when_the_draft_does_not_fit(monkeypatch):
    """Il draft entra nel budget di `--gpu-memory-utilization`: su una GPU
    stretta lascia alla cache KV meno di quanto serve per `--max-model-len` e
    vLLM esce durante il dimensionamento, decine di secondi dopo il lancio.
    DFlash è un'accelerazione: meglio ripartire senza che restare senza modello.
    """
    import threading

    serve_manager._STARTING_INFO = {}
    serve_manager._ACTIVE_PROC = None
    attempts: list[bool | None] = []
    done = threading.Event()

    def fake_start(adapter_id, port=8888, owner_id=None, dflash=None):
        attempts.append(dflash)
        serve_manager._ACTIVE_INFO = {"dflash": dflash is not False}
        if len(attempts) == 2:
            done.set()
        return serve_manager.get_status()

    monkeypatch.setattr(serve_manager, "start", fake_start)
    monkeypatch.setattr(serve_manager, "is_installed", lambda adapter_id: True)
    # Primo tentativo: morto prima di servire. Secondo: nessun draft, niente watch.
    monkeypatch.setattr(serve_manager, "_died_before_serving", lambda port, **kw: True)
    marked: list[str] = []
    monkeypatch.setattr(
        serve_manager, "mark_draft_unusable", lambda adapter_id, reason: marked.append(reason)
    )

    serve_manager.start_async("monkeyocrv2-parsing", port=18897)
    assert done.wait(timeout=5.0), attempts
    assert attempts == [None, False]
    # Il fallimento va ricordato: il prossimo avvio non deve ripagare i ~90 s
    # del tentativo con DFlash.
    assert len(marked) == 1 and "VRAM" in marked[0]


def test_start_async_leaves_a_working_dflash_server_alone(monkeypatch):
    serve_manager._STARTING_INFO = {}
    serve_manager._ACTIVE_PROC = None
    calls: list[bool | None] = []

    def fake_start(adapter_id, port=8888, owner_id=None, dflash=None):
        calls.append(dflash)
        serve_manager._ACTIVE_INFO = {"dflash": True}
        return serve_manager.get_status()

    monkeypatch.setattr(serve_manager, "start", fake_start)
    monkeypatch.setattr(serve_manager, "is_installed", lambda adapter_id: True)
    monkeypatch.setattr(serve_manager, "_died_before_serving", lambda port, **kw: False)

    serve_manager.start_async("monkeyocrv2-parsing", port=18898)
    time.sleep(0.3)
    assert calls == [None]


def test_cpu_offload_is_computed_only_when_the_checkpoint_does_not_fit(monkeypatch):
    """`--cpu-offload-gb` è la differenza fra «non parte» e «parte più lento»:
    va aggiunto solo quando serve, così i modelli che entrano non pagano banda
    PCIe per niente. Misurato su una GPU da 8 GB: DeepSeek-OCR-2 (pesi 6.33 GiB)
    senza offload dà cache KV -1.03 GiB; MonkeyOCRv2 (1.92 GiB) entra comodo.
    """
    monkeypatch.setattr(
        "app.services.trainer_metrics.gpu_snapshot",
        lambda: [{"memory_total": 8188, "memory_used": 4986}],
    )
    gib = 1024 ** 3

    assert serve_manager.cpu_offload_gib(int(6.32 * gib)) > 0
    assert serve_manager.cpu_offload_gib(int(1.92 * gib)) == 0
    assert serve_manager.cpu_offload_gib(None) == 0
    # La memoria occupata adesso non conta: `start()` ferma il server
    # precedente prima di lanciare il nuovo.
    monkeypatch.setattr(
        "app.services.trainer_metrics.gpu_snapshot",
        lambda: [{"memory_total": 8188, "memory_used": 0}],
    )
    assert serve_manager.cpu_offload_gib(int(1.92 * gib)) == 0


def test_cpu_offload_never_exceeds_the_weights_themselves(monkeypatch):
    """Oltre i pesi non c'è niente da spostare: se non basta, il modello non è
    per questa macchina e l'avviso VRAM del registro lo dice già."""
    monkeypatch.setattr(
        "app.services.trainer_metrics.gpu_snapshot",
        lambda: [{"memory_total": 4096, "memory_used": 0}],
    )
    weights_gib = 20
    assert serve_manager.cpu_offload_gib(weights_gib * 1024 ** 3) == weights_gib


def test_no_gpu_detected_means_no_offload_guess(monkeypatch):
    monkeypatch.setattr("app.services.trainer_metrics.gpu_snapshot", lambda: [])
    assert serve_manager.cpu_offload_gib(10 * 1024 ** 3) == 0


def test_docker_backed_adapter_pulls_its_image_as_a_declared_phase(monkeypatch):
    """L'immagine vLLM dedicata pesa una decina di GB: senza una fase propria,
    `docker run` la scarica in silenzio e l'avvio sembra bloccato."""
    _fake_installed("unlimited-ocr")
    monkeypatch.setattr(serve_manager, "docker_gpu_blocker", lambda: None)
    pulled: list[str] = []
    monkeypatch.setattr(serve_manager, "_docker_image_present", lambda image: False)
    monkeypatch.setattr(
        serve_manager, "_docker_pull", lambda image, adapter_id: pulled.append(image)
    )
    phases: list[str] = []
    real_set_phase = serve_manager._set_phase
    monkeypatch.setattr(
        serve_manager,
        "_set_phase",
        lambda adapter_id, phase, error=None: (phases.append(phase), real_set_phase(adapter_id, phase, error))[1],
    )

    class FakeProc:
        def __init__(self, *args, **kwargs):
            self.pid = 4248

        def poll(self):
            return None

    monkeypatch.setattr("app.services.serve_manager.subprocess.Popen", FakeProc)

    serve_manager.start("unlimited-ocr", port=18899)

    assert pulled == ["vllm/vllm-openai:unlimited-ocr"]
    assert "preparing_image" in phases
    assert "preparing_image" in serve_manager.PHASES


def test_an_already_present_image_is_not_pulled_again(monkeypatch):
    _fake_installed("unlimited-ocr")
    monkeypatch.setattr(serve_manager, "docker_gpu_blocker", lambda: None)
    pulled: list[str] = []
    monkeypatch.setattr(serve_manager, "_docker_image_present", lambda image: True)
    monkeypatch.setattr(
        serve_manager, "_docker_pull", lambda image, adapter_id: pulled.append(image)
    )

    class FakeProc:
        def __init__(self, *args, **kwargs):
            self.pid = 4249

        def poll(self):
            return None

    monkeypatch.setattr("app.services.serve_manager.subprocess.Popen", FakeProc)

    serve_manager.start("unlimited-ocr", port=18900)

    assert pulled == []


def test_docker_adapter_refuses_with_an_actionable_message_without_the_nvidia_toolkit(monkeypatch):
    """`docker run --gpus all` esce con codice 125 e «could not select device
    driver»: illeggibile. Il prerequisito è di sistema e non possiamo
    installarlo noi, ma possiamo dirlo prima di spendere l'avvio."""
    _fake_installed("unlimited-ocr")
    monkeypatch.setattr(
        "app.services.serve_manager.shutil.which",
        lambda name: "/usr/bin/docker" if name == "docker" else None,
    )

    with pytest.raises(RuntimeError) as exc:
        serve_manager.start("unlimited-ocr", port=18901)

    message = str(exc.value)
    assert "nvidia-container-toolkit" in message
    assert "Cloud" in message  # l'alternativa praticabile, non solo il problema


def test_docker_adapter_refuses_when_docker_itself_is_absent(monkeypatch):
    _fake_installed("unlimited-ocr")
    monkeypatch.setattr("app.services.serve_manager.shutil.which", lambda name: None)

    with pytest.raises(RuntimeError) as exc:
        serve_manager.start("unlimited-ocr", port=18902)

    assert "Docker non è installato" in str(exc.value)


def test_prerequisites_are_declared_in_the_registry_before_the_click(monkeypatch):
    monkeypatch.setattr(
        "app.services.serve_manager.shutil.which",
        lambda name: "/usr/bin/docker" if name == "docker" else None,
    )
    items = {m["adapter_id"]: m for m in model_registry.list_models()}
    assert items["unlimited-ocr"]["local_serve_blocker"] == "nvidia_container_toolkit_missing"
    # Gli adapter che non passano da Docker non ereditano il blocco.
    assert items["monkeyocrv2-parsing"]["local_serve_blocker"] is None


def test_a_leftover_server_of_ours_is_reclaimed_without_asking(monkeypatch):
    """Un nostro server rimasto orfano non è un problema dell'utente: il
    registro dei job ne ha perso le tracce, ma è pur sempre lo stop-before-start
    che deve occuparsene. Si termina e si prosegue."""
    _fake_installed("monkeyocrv2-parsing")
    monkeypatch.setattr("app.services.serve_manager.config.TRAIN_REPO", "/my/own/MonkeyOCRv2")
    monkeypatch.setattr("app.services.serve_manager.config.TRAIN_PYTHON", "/my/own/env/bin/python")
    monkeypatch.setattr("app.services.serve_manager.ensure_draft", lambda adapter_id: None)
    freed: list[int] = []
    busy = {"value": True}
    monkeypatch.setattr(serve_manager, "_port_in_use", lambda port: busy["value"])
    monkeypatch.setattr(serve_manager, "_port_holder", lambda port: "4242")
    monkeypatch.setattr(serve_manager, "_is_our_serving_process", lambda pid: True)

    def fake_terminate(pid, process_group=None, timeout=20.0):
        freed.append(int(pid))
        busy["value"] = False
        return True

    monkeypatch.setattr(serve_manager, "_terminate", fake_terminate)

    class FakeProc:
        def __init__(self, *args, **kwargs):
            self.pid = 4251

        def poll(self):
            return None

    monkeypatch.setattr("app.services.serve_manager.subprocess.Popen", FakeProc)

    serve_manager.start("monkeyocrv2-parsing", port=18905)

    assert 4242 in freed


def test_a_foreign_process_on_the_port_is_never_killed(monkeypatch):
    """Un Jupyter o un server dell'utente sulla stessa porta non si tocca: qui
    sì che serve dirlo invece di agire."""
    _fake_installed("monkeyocrv2-parsing")
    monkeypatch.setattr("app.services.serve_manager.config.TRAIN_REPO", "/my/own/MonkeyOCRv2")
    monkeypatch.setattr("app.services.serve_manager.config.TRAIN_PYTHON", "/my/own/env/bin/python")
    monkeypatch.setattr("app.services.serve_manager.ensure_draft", lambda adapter_id: None)
    monkeypatch.setattr(serve_manager, "_port_in_use", lambda port: True)
    monkeypatch.setattr(serve_manager, "_port_holder", lambda port: "4242")
    monkeypatch.setattr(serve_manager, "_is_our_serving_process", lambda pid: False)
    monkeypatch.setattr(
        serve_manager,
        "_terminate",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("non si termina un processo altrui")),
    )

    class FakeProc:
        def __init__(self, *args, **kwargs):
            raise AssertionError("non deve nemmeno provare a lanciare")

    monkeypatch.setattr("app.services.serve_manager.subprocess.Popen", FakeProc)

    with pytest.raises(RuntimeError) as exc:
        serve_manager.start("monkeyocrv2-parsing", port=18903)

    message = str(exc.value)
    assert "18903" in message
    assert "4242" in message


def test_a_free_port_does_not_block_the_start(monkeypatch):
    _fake_installed("monkeyocrv2-parsing")
    monkeypatch.setattr("app.services.serve_manager.config.TRAIN_REPO", "/my/own/MonkeyOCRv2")
    monkeypatch.setattr("app.services.serve_manager.config.TRAIN_PYTHON", "/my/own/env/bin/python")
    monkeypatch.setattr("app.services.serve_manager.ensure_draft", lambda adapter_id: None)
    monkeypatch.setattr(serve_manager, "_port_in_use", lambda port: False)
    launched: list[int] = []

    class FakeProc:
        def __init__(self, *args, **kwargs):
            launched.append(1)
            self.pid = 4250

        def poll(self):
            return None

    monkeypatch.setattr("app.services.serve_manager.subprocess.Popen", FakeProc)

    serve_manager.start("monkeyocrv2-parsing", port=18904)

    assert launched == [1]


def test_stop_waits_for_the_process_to_actually_exit(monkeypatch):
    """Tornare al SIGTERM invece che alla morte del processo faceva partire il
    modello nuovo su una porta ancora aperta: `Address already in use`."""
    alive = {"value": True}
    signals: list[int] = []

    def fake_kill(pid, sig):
        if sig == 0:
            if alive["value"]:
                return
            raise ProcessLookupError
        signals.append(sig)
        alive["value"] = False

    monkeypatch.setattr(serve_manager.os, "kill", fake_kill)
    monkeypatch.setattr(serve_manager.os, "killpg", fake_kill)
    monkeypatch.setattr(serve_manager.os, "getpgid", lambda pid: pid)

    assert serve_manager._terminate(4242, 4242, timeout=5.0) is True
    assert signals and signals[0] == serve_manager.signal.SIGTERM


def test_stop_escalates_to_sigkill_when_sigterm_is_ignored(monkeypatch):
    """Un solo modello per volta è un vincolo di VRAM, non una preferenza."""
    monkeypatch.setattr(serve_manager, "_pid_alive", lambda pid: True)
    signals: list[int] = []

    def fake_kill(pid, sig):
        if sig == 0:
            return  # sempre vivo
        signals.append(sig)

    monkeypatch.setattr(serve_manager.os, "kill", fake_kill)
    monkeypatch.setattr(serve_manager.os, "killpg", fake_kill)
    monkeypatch.setattr(serve_manager.os, "getpgid", lambda pid: pid)

    assert serve_manager._terminate(4242, 4242, timeout=0.5) is False
    assert serve_manager.signal.SIGKILL in signals


def test_stop_never_signals_a_process_it_cannot_attribute(monkeypatch):
    """I numeri di PID vengono riciclati: una riga in `jobs` scritta prima di un
    riavvio della macchina può puntare oggi a un processo qualunque
    dell'utente. Il job si marca fermo, il processo non si tocca."""
    import subprocess as sp

    stranger = sp.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        serve_manager._ACTIVE_PROC = None
        serve_manager._ACTIVE_INFO = {}
        monkeypatch.setattr(
            serve_manager,
            "_persisted_running",
            lambda: {"id": 1, "pid": stranger.pid, "process_group": stranger.pid,
                     "adapter_id": "mineru2.5", "port": 18906, "log_path": ""},
        )

        serve_manager.stop()

        assert stranger.poll() is None, "un processo non attribuibile è stato terminato"
    finally:
        stranger.kill()
        stranger.wait(timeout=5)
