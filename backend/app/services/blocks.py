"""Caso d'uso: sincronizzazione delle annotazioni di una pagina.

Estratto dal router (``api/blocks.py``), che resta la porta HTTP: qui sta la
logica che l'autosave del canvas esegue a ogni modifica e che deve proteggere
le griglie tabellari collegate ai blocchi.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from fastapi import HTTPException

from ..adapters import annotations as annotationstore
from . import audit as auditsvc

if TYPE_CHECKING:
    from ..schemas import BlockBulkWrite, BlockListOut, BlockOut


def _block_out(row) -> "BlockOut":
    from ..schemas import BlockOut  # noqa: PLC0415

    try:
        points = json.loads(row["points"] or "[]")
    except (TypeError, ValueError):
        points = []
    return BlockOut(
        id=row["id"],
        page_id=row["page_id"],
        label=row["label"],
        kind=row["kind"],
        points=[[float(px), float(py)] for px, py in points],
        content=row["content"],
        order_idx=row["order_idx"],
        confirmed=bool(row["confirmed"]),
        prefill_source=row["prefill_source"],
        updated_at=row["updated_at"],
    )


def _get_page_or_404(writer, page_id: int):
    row = writer.page(page_id)
    if row is None:
        raise HTTPException(status_code=404, detail="pagina non trovata")
    return row


def list_page_blocks(page_id: int) -> "BlockListOut":
    from ..schemas import BlockListOut  # noqa: PLC0415

    with annotationstore.writing() as writer:
        page = _get_page_or_404(writer, page_id)
        return BlockListOut(
            items=[_block_out(row) for row in writer.blocks(page_id)],
            annotation_revision=page["annotation_revision"],
        )


def sync_annotations(page_id: int, payload: "BlockBulkWrite", actor: dict | None = None) -> "BlockListOut":
    """Sincronizza i blocchi della pagina preservando gli ID esistenti.

    Le tabelle sono legate a ``blocks.id`` con ``ON DELETE CASCADE``. Il
    precedente delete+insert cancellava quindi ogni griglia al successivo
    autosave del canvas, anche quando il blocco Table era ancora presente.

    Per la stessa ragione le bozze di prefill non verificate sopravvivono:
    non stanno sul canvas, quindi la loro assenza dal payload non è una
    cancellazione richiesta dall'utente.
    """
    from ..schemas import BlockListOut  # noqa: PLC0415

    with annotationstore.writing() as writer:
        _get_page_or_404(writer, page_id)
        expected = payload.expected_revision
        if expected is not None:
            changed = writer.bump_revision(page_id, expected)
            if changed != 1:
                remote = list_page_blocks(page_id)
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "annotation_conflict",
                        "message": "la pagina è stata modificata da un altro utente",
                        "page_id": page_id,
                        "remote": remote.model_dump(),
                    },
                )
        else:
            # Backward-compatible clients still work, but every new client is
            # expected to send expected_revision.
            writer.bump_revision(page_id)
        rows_existing = writer.identities(page_id)
        existing = {int(row["id"]) for row in rows_existing}
        # Le bozze di prefill non verificate NON stanno sul canvas (le filtra
        # `applyBlocks` lato client): non arrivano mai nel payload, quindi
        # dedurne la cancellazione dall'assenza le distruggeva — con la loro
        # griglia — al primo autosave dopo una run di pseudo-etichettatura.
        # Si scartano solo dal comando ``DELETE`` esplicito su /blocks/{id}.
        drafts = {
            int(row["id"])
            for row in rows_existing
            if row["prefill_source"] and not bool(row["confirmed"])
        }
        kept: set[int] = set()
        assigned: list[int] = []
        for item in payload.items:
            if item.id is not None:
                if item.id not in existing:
                    raise HTTPException(
                        status_code=409,
                        detail=f"blocco {item.id} non appartiene alla pagina {page_id}",
                    )
                writer.update_block(
                    item.id,
                    label=item.label,
                    kind=item.kind,
                    points=item.points,
                    content=item.content,
                    order_idx=item.order_idx,
                    confirmed=item.confirmed,
                )
                kept.add(item.id)
                assigned.append(item.id)
            else:
                new_id = writer.insert_block(
                    page_id,
                    label=item.label,
                    kind=item.kind,
                    points=item.points,
                    content=item.content,
                    order_idx=item.order_idx,
                    confirmed=item.confirmed,
                )
                kept.add(new_id)
                assigned.append(new_id)
        # Una bozza si cancella solo se il client lo chiede per nome: è la
        # ragione per cui `deleted_ids` esiste. Un id tornato in `items`
        # (annullamento della cancellazione prima del salvataggio) non viene
        # cancellato, qualunque cosa dica la lista.
        explicit = {int(i) for i in payload.deleted_ids} & existing
        removed = ((existing - kept - drafts) | explicit) - kept
        if removed:
            writer.delete_blocks(sorted(removed))
        writer.mark_annotated(page_id)
        revision = writer.revision(page_id)
        auditsvc.record(
            writer.conn, actor, "page.annotations_saved",
            resource_type="page", resource_id=page_id,
            payload={"revision": revision, "blocks": len(payload.items)},
        )
        return BlockListOut(
            items=[_block_out(row) for row in writer.blocks(page_id)],
            annotation_revision=revision,
            assigned_ids=assigned,
        )
