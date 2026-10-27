"""Compare checkpoints on the training GPU before download and release."""
from __future__ import annotations

import re
import shlex
import socket
import subprocess
import time
from pathlib import Path
from contextlib import contextmanager

from . import training_comparison
from .. import config
from .training_executor import SshExecutor


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_port(port: int, timeout: float = 30) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def _serve_dir(cfg: dict) -> str | None:
    train_repo = str(cfg.get("ssh_train_repo") or "").strip()
    if not train_repo:
        return None
    return str(Path(train_repo).parent)


def ensure_evaluation_runtime(executor: SshExecutor, cfg: dict, log_file: Path, on_progress=None) -> dict:
    """Install a compatible serving runtime separately from the training environment."""
    python = str(cfg.get('ssh_python') or 'python3')
    runtime = '/opt/tabularium/eval-venv'
    command = (
        f'test -x {runtime}/bin/python || {shlex.quote(python)} -m venv --system-site-packages {runtime}; '
        f'{runtime}/bin/python -m pip install "vllm==0.11.2" "transformers==4.57.1" "qwen_vl_utils==0.0.14"'
    )
    process = executor.stream(command, log_file)
    deadline = time.monotonic() + 1200
    from .trainer_metrics import _log_tail
    while process.poll() is None:
        if time.monotonic() >= deadline:
            process.terminate()
            raise RuntimeError('Evaluation environment setup exceeded 20 minutes.')
        if on_progress:
            matches = list(re.finditer(r"Downloading ([\w.+-]+\.whl) \(([\d.]+) ([kMGT]?B)\)", _log_tail(log_file)))
            if matches:
                package, amount, unit = matches[-1].groups()
                total = int(float(amount) * {"B": 1, "kB": 1000, "MB": 1000000, "GB": 1000000000, "TB": 1000000000000}[unit])
                probe = "import glob,os; print(max([os.path.getsize(p) for p in glob.glob(" + repr('/tmp/pip-unpack-*/' + package) + ")], default=0))"
                try:
                    measured = executor.run(f"{shlex.quote(python)} -c {shlex.quote(probe)}", timeout=10)
                    numeric = [line.strip() for line in measured.stdout.splitlines() if line.strip().isdigit()]
                    if measured.returncode == 0 and numeric:
                        on_progress(target='base', completed=0, total=0, phase='environment',
                                    download={"name": package, "done": int(numeric[-1]), "total": total})
                except (OSError, subprocess.SubprocessError):
                    pass  # Optional telemetry must never stop installation.
        time.sleep(5)
    code = process.returncode
    if code != 0:
        raise RuntimeError('Evaluation environment setup failed; inspect its installation log.')
    updated = {**cfg, 'ssh_python': f'{runtime}/bin/python'}
    probe = ('import sys; '
             f'sys.path.insert(0, {_serve_dir(cfg)!r}); '
             'from modeling import modeling_monkeyocrv2_vllm_011; '
             'import torch, vllm; print(torch.__version__, vllm.__version__)')
    checked = executor.run(f'{runtime}/bin/python -c {shlex.quote(probe)}', timeout=120)
    if checked.returncode != 0:
        raise RuntimeError(f'Official model registration is incompatible with the runtime: {(checked.stderr or "")[-1800:]}')
    return updated


@contextmanager
def remote_endpoint(
    *,
    executor: SshExecutor,
    cfg: dict,
    model_path: str,
    log_file: Path,
):
    """Serve one checkpoint without changing official repositories or active inference."""
    serve_dir = _serve_dir(cfg)
    if not serve_dir:
        raise ValueError("Remote serving repository is not configured")
    python = str(cfg.get("ssh_python") or "python3")
    remote_port = 18921
    remote_log = '/tmp/tabularium-comparison/serve.log'
    compatibility = """import importlib.metadata
_original_version = importlib.metadata.version
def _monkey_legacy_version(name):
    value = _original_version(name)
    return '0.11' if name == 'vllm' and value.startswith('0.11.') else value
importlib.metadata.version = _monkey_legacy_version
"""
    wrapper = """import runpy, sys
from pathlib import Path
repo = Path(sys.argv.pop(1))
sys.path.insert(0, str(repo))
runpy.run_path(str(repo / 'serve.py'), run_name='__main__')
"""
    prepared = executor.run('mkdir -p /tmp/tabularium-comparison/compat && printf %s '
                            + shlex.quote(compatibility) + ' > /tmp/tabularium-comparison/compat/sitecustomize.py && printf %s '
                            + shlex.quote(wrapper) + ' > /tmp/tabularium-comparison/serve-wrapper.py', timeout=30)
    if prepared.returncode != 0:
        raise RuntimeError('Unable to create the comparison launcher.')
    launch = (
        f"cd {shlex.quote(serve_dir)} && ("
        f"nohup setsid env PYTHONPATH=/tmp/tabularium-comparison/compat:{shlex.quote(serve_dir)} {shlex.quote(python)} /tmp/tabularium-comparison/serve-wrapper.py {shlex.quote(serve_dir)} "
        f"--model-path {shlex.quote(model_path)} "
        "--served-model-name MonkeyOCRv2 --host 127.0.0.1 "
        f"--port {remote_port} --gpu-memory-utilization 0.75 --max-model-len 16384 --max-num-seqs 1 -- --enforce-eager "
        f"> {shlex.quote(remote_log)} 2>&1 < /dev/null & echo $!)"
    )
    result = executor.run(launch, timeout=30)
    try:
        server_pid = int((result.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise RuntimeError(f"vLLM did not start: {(result.stderr or result.stdout or '').strip()[-500:]}") from exc

    local_port = _free_port()
    tunnel: subprocess.Popen | None = None
    try:
        deadline = time.time() + 15 * 60
        ready = False
        while time.time() < deadline:
            probe = executor.run(
                f"curl -sf http://127.0.0.1:{remote_port}/v1/models", timeout=15
            )
            if probe.returncode == 0 and "MonkeyOCRv2" in (probe.stdout or ""):
                ready = True
                break
            alive = executor.run(f"kill -0 {server_pid}", timeout=10)
            if alive.returncode != 0:
                captured = executor.run(f"cat {shlex.quote(remote_log)}", timeout=20)
                diagnostic = captured.stdout or captured.stderr or ''
                saved_log = log_file.parent / ('serve-' + ('fine-tuned' if 'fine_tuned' in model_path or 'merged' in model_path else 'base') + '.log')
                saved_log.write_text(diagnostic, encoding='utf-8')
                with log_file.open('a', encoding='utf-8') as log:
                    log.write('\nServing startup failed; complete diagnostics: ' + str(saved_log) + '\n' + diagnostic)
                errors = [line for line in diagnostic.splitlines() if any(word in line for word in ('Error', 'Exception', 'CUDA', 'failed', 'unsupported', 'Unsupported'))]
                raise RuntimeError('Evaluation server failed to start; diagnostics saved to ' + str(saved_log) + ':\n' + '\n'.join(errors)[-7000:])
            time.sleep(5)
        if not ready:
            raise RuntimeError("vLLM did not become ready within 15 minutes")

        tunnel = subprocess.Popen(
            [
                "ssh", *executor._options(), "-N",
                "-L", f"127.0.0.1:{local_port}:127.0.0.1:{remote_port}",
                executor._target(),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if not _wait_port(local_port, timeout=20):
            raise RuntimeError("SSH tunnel is unavailable")
        yield f'http://127.0.0.1:{local_port}/v1'
    finally:
        try:
            captured = executor.run(f"cat {shlex.quote(remote_log)}", timeout=20)
            saved_log = log_file.parent / ('serve-' + ('fine-tuned' if 'fine_tuned' in model_path or 'merged' in model_path else 'base') + '.log')
            saved_log.write_text(captured.stdout or captured.stderr or '', encoding='utf-8')
        except Exception:
            # Diagnostic collection must not hide inference or cleanup errors.
            pass
        if tunnel is not None:
            tunnel.terminate()
            try:
                tunnel.wait(timeout=5)
            except subprocess.TimeoutExpired:
                tunnel.kill()
        executor.run(f"kill -- -{server_pid} 2>/dev/null || kill {server_pid} 2>/dev/null || true", timeout=15)
        time.sleep(3)


def evaluate_on_remote(*, executor: SshExecutor, cfg: dict, project_id: int,
                       merged_remote: str, lang: str, log_file: Path,
                       source_run_dir: Path | None = None, on_progress=None) -> dict:
    """Compare base and trained weights on exactly the run's frozen validation samples."""
    from datetime import datetime, timezone
    import json

    run_id = Path(merged_remote).parents[1].name
    run_dir = source_run_dir or config.DATA_DIR / str(project_id) / 'runs' / run_id
    training_comparison.validation_samples(run_dir)
    if on_progress:
        on_progress(target='base', completed=0, total=0, phase='environment')
    cfg = ensure_evaluation_runtime(executor, cfg, log_file, on_progress=on_progress)
    python = str(cfg.get('ssh_python') or 'python3')
    base_model = str(cfg.get('model_path') or cfg.get('model') or 'zenosai/MonkeyOCRv2-B-Parsing')
    if on_progress:
        on_progress(target='base', completed=0, total=0, phase='model_download')
    training_log = run_dir / 'train.log'
    revisions = re.findall(re.escape('models--' + base_model.replace('/', '--') + '/snapshots/') + r'([a-f0-9]{40})',
                           training_log.read_text(encoding='utf-8', errors='replace')) if training_log.is_file() else []
    revision = revisions[-1] if revisions else None
    resolve = ('from pathlib import Path; from huggingface_hub import snapshot_download; '
               f"p={base_model!r}; print(str(Path(p)) if Path(p).is_dir() else snapshot_download(p, revision={revision!r}))")
    result = executor.run(f'{shlex.quote(python)} -c {shlex.quote(resolve)}', timeout=900)
    if result.returncode != 0:
        raise RuntimeError(f'Unable to resolve base checkpoint: {(result.stderr or "")[-1000:]}')
    base_remote = result.stdout.strip().splitlines()[-1]
    results = {}
    for target, checkpoint in [('base', base_remote), ('fine_tuned', merged_remote)]:
        if on_progress:
            on_progress(target=target, completed=0, total=0, phase='server_start')
        with remote_endpoint(executor=executor, cfg=cfg, model_path=checkpoint, log_file=log_file) as url:
            results[target] = training_comparison.score_validation(run_dir, url, 'MonkeyOCRv2', target=target,
                                                                   on_progress=on_progress)
        partial = run_dir / f'comparison_{target}.json'
        partial.write_text(json.dumps(results[target], ensure_ascii=False, indent=2), encoding='utf-8')
    report = training_comparison.comparison_report(run_dir, results['base'], results['fine_tuned'])
    report['evaluated_at'] = datetime.now(timezone.utc).isoformat()
    report['base_model'] = base_model
    report['base_revision'] = revision or (Path(base_remote).name if '/snapshots/' in base_remote else None)
    report_path = run_dir / 'comparison.json'
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'evaluated_at': report['evaluated_at'], 'pages_evaluated': results['fine_tuned']['pages_evaluated'],
            'aggregates': results['fine_tuned']['aggregates'], 'comparison': report,
            'comparison_report': str(report_path)}
