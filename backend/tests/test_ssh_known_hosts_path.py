"""Il percorso di `known_hosts` quando il data root contiene uno spazio.

ssh interpreta il valore di `UserKnownHostsFile` come una lista di file
separati da spazi. Un data root come
`~/Library/CloudStorage/OneDrive-.../University of X/...` (macOS) o
`C:/Users/Nome Cognome/...` (Windows) veniva quindi spezzato in piu' nomi
inesistenti, e ssh rifiutava la connessione con

    No ED25519 host key is known for [host]:porta
    and you have requested strict checking.

pur avendo il `known_hosts` giusto sul disco.
"""
from __future__ import annotations

import shlex
from pathlib import Path

import conftest  # noqa: F401

from app import config
from app.services import cloud_manager


_ROOT_WITH_SPACES = Path("/Users/tizio/OneDrive-ateneo/University of X/Tabularium/data/known_hosts")


def test_known_hosts_option_survives_a_path_with_spaces(monkeypatch):
    """Il valore deve restare **un solo** file agli occhi di ssh."""
    monkeypatch.setattr(config, "SSH_KNOWN_HOSTS", _ROOT_WITH_SPACES)
    option = cloud_manager._known_hosts_option()

    assert option.startswith("UserKnownHostsFile=")
    value = option.split("=", 1)[1]
    # ssh divide il valore come farebbe una shell: dopo la divisione deve
    # restare un percorso solo, altrimenti cerca file che non esistono.
    assert shlex.split(value) == [str(_ROOT_WITH_SPACES)]


def test_ssh_args_carry_the_quoted_known_hosts(monkeypatch):
    """Gli argomenti reali della connessione portano il percorso intero."""
    monkeypatch.setattr(config, "SSH_KNOWN_HOSTS", _ROOT_WITH_SPACES)
    args = cloud_manager._ssh_base_args("ssh9.example.com", 37068, "root")

    opts = [a for a in args if a.startswith("UserKnownHostsFile=")]
    assert len(opts) == 1
    assert shlex.split(opts[0].split("=", 1)[1]) == [str(_ROOT_WITH_SPACES)]
