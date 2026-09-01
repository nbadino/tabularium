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
from typing import Protocol

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
            "-o", f"UserKnownHostsFile={self.known_hosts}",
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

    def sync_argv(self, recipe: TrainingRecipe) -> list[str]:
        remote = f"{self._target()}:{self._remote_dir(recipe)}/"
        return [
            "rsync", "--archive", "--checksum", "--delete",
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
            "< /dev/null & printf '%s\\n' \"$!\""
        )
        return ["ssh", *self._options(), self._target(), command]

    def recover(self, recipe: TrainingRecipe, log_file: Path, remote_pid: int) -> "RemoteProcess":
        return RemoteProcess(self, recipe, log_file, int(remote_pid))

    def launch(self, recipe: TrainingRecipe, log_file: Path) -> "RemoteProcess":
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("ab") as stream:
            stream.write(f">> sync {self._target()}:{self._remote_dir(recipe)}\n".encode())
            stream.flush()
            subprocess.run(self.sync_argv(recipe), check=True, stdout=stream, stderr=subprocess.STDOUT)
        result = subprocess.run(self.launch_argv(recipe), check=True, capture_output=True, text=True)
        try:
            remote_pid = int(result.stdout.strip().splitlines()[-1])
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

    def _remote(self, command: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["ssh", *self.executor._options(), self.executor._target(), command],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )

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
        manifest_result = self._remote(f"cat {shlex.quote(self.executor._remote_dir(self.recipe))}/artifacts.sha256")
        if manifest_result.returncode != 0:
            return {"ok": False, "checked": 0, "errors": ["manifest remoto assente"]}
        manifest = destination.parent / "remote-artifacts.sha256"
        manifest.write_text(manifest_result.stdout, encoding="utf-8")
        return verify_manifest(destination, manifest)

    def poll(self) -> int | None:
        if self._returncode is not None:
            return self._returncode
        self._sync_log()
        try:
            result = self._remote(f"cat {shlex.quote(self.executor._remote_dir(self.recipe))}/.exit_code")
        except (OSError, subprocess.SubprocessError):
            result = None
        if result is not None and result.returncode == 0:
            try:
                self._returncode = int(result.stdout.strip())
            except ValueError:
                self._returncode = 1
            return self._returncode
        try:
            result = self._remote(f"kill -0 {self.pid}")
        except (OSError, subprocess.SubprocessError):
            return None  # rete indisponibile: non dichiarare il job terminato
        if result.returncode == 0:
            return None
        if result.returncode == 255:
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
        return executor_type(
            host=str(cfg.get("ssh_host") or ""),
            user=str(cfg.get("ssh_user") or "root"),
            port=int(cfg.get("ssh_port", 22)),
            key_path=str(cfg.get("ssh_key_path") or "") or None,
            remote_root=str(cfg.get("ssh_root") or "/tmp/tabularium-runs"),
            known_hosts=known_hosts,
        )
    raise ValueError(f"executor training non supportato: {provider}")
