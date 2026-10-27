"""TEDS on canonical OTSL table trees using PubTabNet's edit-cost convention.

Reference: https://github.com/ibm-aur-nlp/PubTabNet/blob/master/src/metric.py
OTSL cells contain plain text; rows and merged-cell spans form the tree.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from apted import APTED, Config
from rapidfuzz.distance import Levenshtein


@dataclass
class TableNode:
    tag: str
    colspan: int = 1
    rowspan: int = 1
    content: str = ''
    children: list['TableNode'] = field(default_factory=list)


class TableConfig(Config):
    valuecls = float

    def rename(self, left, right):
        if (left.tag, left.colspan, left.rowspan) != (right.tag, right.colspan, right.rowspan):
            return 1.0
        if left.tag == 'td' and (left.content or right.content):
            return Levenshtein.distance(left.content, right.content) / max(len(left.content), len(right.content))
        return 0.0


def _tree(grid: dict) -> tuple[TableNode, int]:
    rows = [TableNode('tr') for _ in range(int(grid.get('rows') or 0))]
    nodes = len(rows)
    for cell in sorted(grid.get('cells', []), key=lambda c: (c['r'], c['c'])):
        r = int(cell['r'])
        if 0 <= r < len(rows):
            rows[r].children.append(TableNode('td', int(cell.get('colspan') or 1),
                                             int(cell.get('rowspan') or 1), cell.get('text') or ''))
            nodes += 1
    return TableNode('table', children=rows), nodes


def teds(reference: dict, prediction: dict) -> float:
    left, left_nodes = _tree(reference)
    right, right_nodes = _tree(prediction)
    if left == right:
        return 1.0
    if not left_nodes and not right_nodes:
        return 1.0
    if not left_nodes or not right_nodes:
        return 0.0
    distance = APTED(left, right, TableConfig()).compute_edit_distance()
    return max(0.0, 1.0 - float(distance) / max(left_nodes, right_nodes))
