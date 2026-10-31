"""Contratti per eseguire una recipe di training in provider diversi.

La recipe descrive *cosa* eseguire ed è identica per locale e remoto. Gli
executor descrivono *dove*: il manifest e lo script vengono sincronizzati
immutati, mentre il run remoto viene eseguito dalla propria directory.
"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from .artifacts import verify_manifest


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class TrainingRecipe:
    """Manifest immutabile della singola run."""

    run_id: str
    run_dir: Path
    script: Path
    train_dataset: Path
    val_dataset: Path
    dataset_snapshot_id: str | None
    config: dict = field(default_factory=dict)
    provider: str = "local"

    def manifest(self) -> dict:
        files = {}
        for path in (self.script, self.train_dataset, self.val_dataset):
            if path.is_file():
                files[str(path.relative_to(self.run_dir))] = {
                    "sha256": _sha256(path),
                    "bytes": path.stat().st_size,
                }
        return {
            "run_id": self.run_id,
            "provider": self.provider,
            "dataset_snapshot_id": self.dataset_snapshot_id,
            "config": self.config,
            "files": files,
        }

    def write_manifest(self) -> Path:
        target = self.run_dir / "recipe.json"
        target.write_text(json.dumps(self.manifest(), ensure_ascii=False, indent=2), encoding="utf-8")
        return target


class TrainingExecutor(Protocol):
    provider: str

    def launch(self, recipe: TrainingRecipe, log_file: Path) -> object:
        """Avvia la recipe e restituisce un handle osservabile dal trainer."""


class LocalProcessExecutor:
    provider = "local"

    def launch(self, recipe: TrainingRecipe, log_file: Path) -> subprocess.Popen:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        return subprocess.Popen(
            ["bash", str(recipe.script)],
            stdout=log_file.open("wb"),
            stderr=subprocess.STDOUT,
            cwd=str(recipe.run_dir),
            start_new_session=True,
            env=os.environ.copy(),
        )


@dataclass(frozen=True)
class SshExecutor:
    """Executor SSH per una GPU remota già provisionata."""

    host: str
    user: str = "root"
    port: int = 22
    key_path: str | None = None
    remote_root: str = "/tmp/tabularium-runs"
    known_hosts: Path | None = None
    provider = "ssh"

    def _target(self) -> str:
        # A leading `-` makes ssh read the target as an option (`-oProxyCommand=...`).
        if self.user.startswith("-") or self.host.startswith("-"):
            raise ValueError("destinazione SSH non valida")
        if not self.host.strip() or any(ch in self.host for ch in " \t\r\n/'\""):
            raise ValueError("host SSH non valido")
        if not self.user.strip() or any(ch in self.user for ch in " \t\r\n/'\""):
            raise ValueError("utente SSH non valido")
        return f"{self.user}@{self.host}"

    def _options(self) -> list[str]:
        if self.port < 1 or self.port > 65535:
            raise ValueError("porta SSH non valida")
        if self.known_hosts is None or not self.known_hosts.is_file():
            raise ValueError("known_hosts SSH obbligatorio e non trovato")
        options = [
            "-p", str(self.port),
            "-o", "StrictHostKeyChecking=yes",
            # Fra virgolette: ssh legge il valore come lista separata da spazi,
            # e un data root con uno spazio spezzava il percorso (v.
            # `cloud_manager._known_hosts_option`).
            "-o", f'UserKnownHostsFile="{self.known_hosts}"',
        ]
        if self.key_path:
            options.extend(["-i", self.key_path])
        return options

    def _remote_dir(self, recipe: TrainingRecipe) -> str:
        root = self.remote_root.rstrip("/")
        if (
            not root
            or root == "/"
            or not root.startswith("/")
            or any(ch in root for ch in " \t\r\n;|&`$\\")
        ):
            raise ValueError("remote_root SSH non valido")
        return f"{root}/{recipe.run_id}"

    def remote_dir(self, run_id: str) -> str:
        """Directory remota di una run, con la stessa validazione di `_remote_dir`."""
        root = self.remote_root.rstrip("/")
        name = str(run_id or "").strip()
        if (
            not root
            or root == "/"
            or not root.startswith("/")
            or any(ch in root for ch in " \t\r\n;|&`$\\")
            or not name
            or name in {".", ".."}
            or Path(name).name != name
        ):
            raise ValueError("percorso remoto della run non valido")
        return f"{root}/{name}"

    def run(self, command: str, *, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
        """Esegue un comando sull'host remoto con la chiave e l'host key già fissati."""
        return subprocess.run(
            ["ssh", *self._options(), self._target(), command],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    def stream(self, command: str, log_file: Path) -> subprocess.Popen:
        """Avvia un comando remoto lasciando stdout/stderr nel log della run."""
        log_file.parent.mkdir(parents=True, exist_ok=True)
        stream = log_file.open("ab")
        try:
            process = subprocess.Popen(
                ["ssh", *self._options(), self._target(), command],
                stdout=stream,
                stderr=subprocess.STDOUT,
            )
        finally:
            stream.close()
        return process

    def download(self, remote_path: str, local_dir: Path, *, log_file: Path | None = None) -> None:
        """Copia una directory remota in locale, con lo stesso trasporto SSH."""
        remote = f"{self._target()}:{remote_path.rstrip('/')}/"
        local_dir.mkdir(parents=True, exist_ok=True)
        command = [
            "rsync", "--archive", "--checksum", "--progress",
            "-e", "ssh " + " ".join(shlex.quote(x) for x in self._options()),
            remote, f"{local_dir}/",
        ]
        if log_file is None:
            subprocess.run(command, check=True)
            return
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("ab") as stream:
            stream.write(f">> rsync {remote} -> {local_dir}\n".encode())
            stream.flush()
            subprocess.run(command, check=True, stdout=stream, stderr=subprocess.STDOUT)

    def mkdir_argv(self, recipe: TrainingRecipe) -> list[str]:
        """Crea la cartella remota della run prima del primo rsync.

        `rsync` non crea le directory padre: senza questo, un `remote_root`
        nuovo (`/tmp/tabularium-runs` di default) faceva fallire il sync con
        `mkdir ... failed: No such file or directory` (exit 11).
        """
        remote_dir = shlex.quote(self._remote_dir(recipe))
        return ["ssh", *self._options(), self._target(), f"mkdir -p {remote_dir}"]

    def sync_argv(self, recipe: TrainingRecipe) -> list[str]:
        remote = f"{self._target()}:{self._remote_dir(recipe)}/"
        return [
            "rsync", "--archive", "--checksum", "--delete", "--out-format=TABULARIUM_FILE:%l:%b:%n",
            "--exclude=activity.json", "--exclude=activity.json.tmp",
            "--exclude=train.log", "--exclude=prepare.log", "--exclude=metrics.jsonl",
            "-e", "ssh " + " ".join(shlex.quote(x) for x in self._options()),
            f"{recipe.run_dir}/", remote,
        ]

    def launch_argv(self, recipe: TrainingRecipe) -> list[str]:
        remote_dir = shlex.quote(self._remote_dir(recipe))
        command = (
            f"mkdir -p {remote_dir} && cd {remote_dir} && "
            "nohup setsid sh -c 'bash train.sh > train.log 2>&1; code=$?; "
            "if [ -d checkpoints ]; then (cd checkpoints && find . -type f -exec sha256sum {} +) > artifacts.sha256; "
            "else : > artifacts.sha256; fi; "
            "printf \"%s\\n\" \"$code\" > .exit_code; exit \"$code\"' "
            "> /dev/null 2>&1 < /dev/null & printf '%s\\n' \"$!\""
        )
        return ["ssh", *self._options(), self._target(), command]

    def _stream(
        self, command: list[str], stream, on_line: Callable[[str], None]
    ) -> int:
        """Run a command, tee its output to `stream`, report every line, return the code.

        Separate from `launch` for one reason: the progress of a sync is a stream (rsync
        reports one file at a time), and a test that only cares about what happens after
        the sync should not have to start a real one.
        """
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        assert proc.stdout is not None
        for raw in proc.stdout:
            stream.write(raw)
            stream.flush()
            on_line(raw.decode("utf-8", "replace").strip())
        return proc.wait()

    def recover(self, recipe: TrainingRecipe, log_file: Path, remote_pid: int) -> "RemoteProcess":
        return RemoteProcess(self, recipe, log_file, int(remote_pid))

    def launch(self, recipe: TrainingRecipe, log_file: Path) -> "RemoteProcess":
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("ab") as stream:
            stream.write(f">> sync {self._target()}:{self._remote_dir(recipe)}\n".encode())
            stream.flush()
            subprocess.run(self.mkdir_argv(recipe), check=True, stdout=stream, stderr=subprocess.STDOUT)
            files = [item for item in recipe.run_dir.rglob("*") if item.is_file() and item.name not in {"activity.json", "activity.json.tmp", "train.log", "prepare.log", "metrics.jsonl"}]
            activity = {"stage": "sync", "started_at": time.time(), "files_done": 0,
                        "files_total": len(files), "bytes_done": 0,
                        "bytes_total": sum(item.stat().st_size for item in files)}
            def publish_activity() -> None:
                activity["updated_at"] = time.time()
                temporary = recipe.run_dir / "activity.json.tmp"
                temporary.write_text(json.dumps(activity), encoding="utf-8")
                temporary.replace(recipe.run_dir / "activity.json")
            publish_activity()
            command = self.sync_argv(recipe)

            def on_line(line: str) -> None:
                if line.startswith("TABULARIUM_FILE:"):
                    parts = line.split(":", 3)
                    if len(parts) == 4 and parts[1].isdigit() and parts[2].isdigit() and not parts[3].endswith("/"):
                        activity["files_done"] += 1
                        activity["bytes_done"] += int(parts[1])
                        publish_activity()

            code = self._stream(command, stream, on_line)
            activity["stage"] = "model_loading" if code == 0 else "sync_failed"
            publish_activity()
            if code:
                raise subprocess.CalledProcessError(code, command)
        try:
            result = subprocess.run(
                self.launch_argv(recipe), check=True, capture_output=True, text=True, timeout=60
            )
            stdout = result.stdout
        except subprocess.TimeoutExpired as exc:
            # Su alcune immagini ssh resta attaccato al canale anche dopo che il
            # PID è stato stampato; il training però è già partito. Recuperiamo
            # il PID dallo stdout e lasciamo che il monitor lo segua.
            raw = exc.stdout or ""
            stdout = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        try:
            remote_pid = int(stdout.strip().splitlines()[-1])
        except (IndexError, ValueError) as exc:
            raise RuntimeError("SSH non ha restituito il PID remoto del training") from exc
        return RemoteProcess(self, recipe, log_file, remote_pid)


@dataclass(frozen=True)
class VastExecutor(SshExecutor):
    """Training su un'istanza Vast già avviata e raggiungibile via SSH."""

    provider = "vast"


@dataclass(frozen=True)
class RunPodExecutor(SshExecutor):
    """Training su un Pod RunPod già avviato e raggiungibile via SSH."""

    provider = "runpod"


class RemoteProcess:
    """Handle process-like per un training detached su SSH."""

    def __init__(self, executor: SshExecutor, recipe: TrainingRecipe, log_file: Path, pid: int):
        self.executor = executor
        self.recipe = recipe
        self.log_file = log_file
        self.pid = int(pid)
        self._returncode: int | None = None
        self._missing_exit_since: float | None = None
        self._gpu_cache: tuple[float, list[dict]] = (0.0, [])

    def _remote(self, command: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["ssh", *self.executor._options(), self.executor._target(), command],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )

    def gpu_snapshot(self) -> list[dict]:
        """Telemetria della GPU remota, con cache breve per non martellare SSH."""
        if self._gpu_cache[0] and time.monotonic() - self._gpu_cache[0] < 5:
            return [dict(item) for item in self._gpu_cache[1]]
        try:
            result = self._remote(
                "nvidia-smi --query-gpu=index,name,memory.total,memory.used,"
                "utilization.gpu,temperature.gpu --format=csv,noheader,nounits"
            )
        except (OSError, subprocess.SubprocessError):
            return [dict(item) for item in self._gpu_cache[1]]
        gpus: list[dict] = []
        if result.returncode == 0:
            for line in result.stdout.strip().splitlines():
                parts = [part.strip() for part in line.split(",")]
                if len(parts) >= 6:
                    try:
                        gpus.append({
                            "index": parts[0], "name": parts[1],
                            "memory_total": int(float(parts[2])),
                            "memory_used": int(float(parts[3])),
                            "utilization": int(float(parts[4])),
                            "temp": int(float(parts[5])),
                        })
                    except ValueError:
                        continue
        self._gpu_cache = (time.monotonic(), gpus)
        return [dict(item) for item in gpus]

    def _sync_log(self) -> None:
        try:
            result = self._remote(f"cat {shlex.quote(self.executor._remote_dir(self.recipe))}/train.log")
        except (OSError, subprocess.SubprocessError):
            return
        if result.returncode == 0:
            self.log_file.write_text(result.stdout, encoding="utf-8")

    def download_artifacts(self, destination: Path) -> dict:
        destination.mkdir(parents=True, exist_ok=True)
        remote = f"{self.executor._target()}:{self.executor._remote_dir(self.recipe)}/checkpoints/"
        subprocess.run(
            [
                "rsync", "--archive", "--checksum",
                "-e", "ssh " + " ".join(shlex.quote(x) for x in self.executor._options()),
                remote, f"{destination}/",
            ],
            check=True,
        )
        manifest_result = self._remote(
            "printf '__TABULARIUM_MANIFEST_BEGIN__\\n'; "
            f"cat {shlex.quote(self.executor._remote_dir(self.recipe))}/artifacts.sha256; "
            + 'code=$?; printf "\\n__TABULARIUM_MANIFEST_END__\\n"; exit "$code"'
        )
        if manifest_result.returncode != 0:
            return {"ok": False, "checked": 0, "errors": ["manifest remoto assente"]}
        manifest = destination.parent / "remote-artifacts.sha256"
        import re
        framed = re.search(r"__TABULARIUM_MANIFEST_BEGIN__\n(.*?)\n__TABULARIUM_MANIFEST_END__", manifest_result.stdout, re.DOTALL)
        if not framed or not framed.group(1).strip():
            return {"ok": False, "checked": 0, "errors": ["manifest remoto vuoto: nessun checkpoint recuperato"]}
        # Preserve the entire framed manifest: malformed entries must fail
        # validation rather than silently disappear with the provider banner.
        manifest.write_text(framed.group(1), encoding="utf-8")
        return verify_manifest(destination, manifest)

    def poll(self) -> int | None:
        if self._returncode is not None:
            return self._returncode
        self._sync_log()
        try:
            result = self._remote(
                f"code=$(cat {shlex.quote(self.executor._remote_dir(self.recipe))}/.exit_code) "
                + "&& printf 'TABULARIUM_EXIT=%s\\n' " + '\"$code\"'
            )
        except (OSError, subprocess.SubprocessError):
            result = None
        if result is not None and result.returncode == 0:
            # Provider login banners can be printed on stdout. Only our marked
            # protocol response is an exit code; malformed output is unknown.
            import re
            match = re.search(r"^TABULARIUM_EXIT=(-?\d+)\s*$", result.stdout, re.MULTILINE)
            if match:
                self._returncode = int(match.group(1))
                return self._returncode
            return None
        try:
            result = self._remote(f"kill -0 {self.pid}")
        except (OSError, subprocess.SubprocessError):
            return None  # rete indisponibile: non dichiarare il job terminato
        if result.returncode == 0:
            return None
        if result.returncode == 255:
            return None
        # Give the detached wrapper time to flush its manifest and exit file.
        # A vanished PID alone does not prove the training command failed.
        if self._missing_exit_since is None:
            self._missing_exit_since = time.monotonic()
        if time.monotonic() - self._missing_exit_since < 60:
            return None
        self._returncode = 1
        return self._returncode

    def wait(self, timeout: float | None = None) -> int:
        started = time.monotonic()
        while self.poll() is None:
            if timeout is not None and time.monotonic() - started >= timeout:
                raise subprocess.TimeoutExpired("ssh training", timeout)
            time.sleep(1.0)
        self._sync_log()
        return int(self._returncode or 0)

    def terminate(self) -> None:
        self._remote(f"kill -TERM -- -{self.pid} 2>/dev/null || kill -TERM {self.pid}")

    def kill(self) -> None:
        self._remote(f"kill -KILL -- -{self.pid} 2>/dev/null || kill -KILL {self.pid}")

    def cleanup(self) -> None:
        """Rimuove esplicitamente la directory della run sull'host remoto.

        Il percorso è composto da ``remote_root`` e ``run_id`` già validati da
        ``_remote_dir``; il cleanup non viene mai eseguito automaticamente.
        """
        result = self._remote(f"rm -rf -- {shlex.quote(self.executor._remote_dir(self.recipe))}")
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "errore remoto").strip()
            raise RuntimeError(f"cleanup remoto fallito: {detail[:300]}")


def executor_from_config(cfg: dict, *, known_hosts: Path | None = None) -> TrainingExecutor:
    provider = str(cfg.get("executor", "local")).strip().lower()
    if provider == "local":
        return LocalProcessExecutor()
    if provider in {"ssh", "vast", "runpod"}:
        executor_type = {"ssh": SshExecutor, "vast": VastExecutor, "runpod": RunPodExecutor}[provider]
        key_path = str(cfg.get("ssh_key_path") or "") or None
        if key_path is None and provider in {"vast", "runpod"}:
            # La chiave che Tabularium registra sull'account del provider è
            # quella che le istanze accettano: chiederne il percorso a mano
            # era un passo che l'app sapeva già fare da sé.
            from .cloud_manager import ssh_key_path

            if ssh_key_path().exists():
                key_path = str(ssh_key_path())
        return executor_type(
            host=str(cfg.get("ssh_host") or ""),
            user=str(cfg.get("ssh_user") or "root"),
            port=int(cfg.get("ssh_port", 22)),
            key_path=key_path,
            remote_root=str(cfg.get("ssh_root") or "/tmp/tabularium-runs"),
            known_hosts=known_hosts,
        )
    raise ValueError(f"executor training non supportato: {provider}")
