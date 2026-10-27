"""Paired OCR evaluation on the immutable validation files of a training run."""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from collections import Counter
from pathlib import Path

from PIL import Image

from . import evaluate, inference
from ..domain import otsl
from .model_adapters import MonkeyOCRv2ParsingAdapter


class ValidationClient(inference.VllmClient):
    """Use the requested adapter and deterministic settings, never active inference."""

    def __init__(self, url: str, model: str):
        super().__init__(url=url, model=model, api_key='', extra_headers={},
                         adapter=MonkeyOCRv2ParsingAdapter(), provider='vast',
                         max_pixels=1003520, timeout=180, max_retries=1)
        self.max_pixels = 1003520  # Freeze the comparison protocol despite saved UI overrides.

    def _sampling_for(self, task: str) -> dict:
        return {'temperature': 0.0, 'top_p': 1.0, 'seed': 42}


def validation_samples(run_dir: Path) -> list[dict]:
    """Resolve the run's frozen image copies; reject missing or leaking pages."""
    dataset = run_dir / 'dataset'
    samples = []
    layout_pages: dict[str, set[str]] = {'train': set(), 'val': set()}
    all_images: dict[str, set[str]] = {'train': set(), 'val': set()}
    for split in ('train', 'val'):
        for line in (dataset / f'{split}.jsonl').read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            all_images[split].add(Path(row['images'][0]).name)
            prompt = next(m['content'] for m in row['messages'] if m['role'] == 'user')
            if inference.LAYOUT_PROMPT in prompt:
                family = 'layout'
                layout_pages[split].add(Path(row['images'][0]).name)
            elif inference.TABLE_PROMPT in prompt:
                family = 'table'
            elif inference.TEXT_PROMPT in prompt:
                family = 'text'
            else:
                raise ValueError('Unsupported frozen validation prompt; no prompt substitution is allowed.')
            if split != 'val':
                continue
            image = dataset / 'images' / Path(row['images'][0]).name
            if not image.is_file():
                raise ValueError(f'Missing immutable validation image: {image.name}')
            samples.append({'family': family, 'image': image,
                'reference': next(m['content'] for m in row['messages'] if m['role'] == 'assistant')})
    if not samples or not layout_pages['val']:
        raise ValueError('The run has no frozen held-out validation pages.')
    if layout_pages['train'] & layout_pages['val']:
        raise ValueError('Training and validation page images overlap.')
    hashes = {split: {hashlib.sha256((dataset / 'images' / name).read_bytes()).hexdigest()
                      for name in names} for split, names in all_images.items()}
    if hashes['train'] & hashes['val']:
        raise ValueError('Duplicate image bytes occur in both training and validation.')
    return samples


def score_validation(run_dir: Path, url: str, model: str, *, target: str,
                     on_progress=None) -> dict:
    samples = validation_samples(run_dir)
    client = ValidationClient(url, model)
    if not client.ping():
        raise RuntimeError('Evaluation endpoint is not ready.')
    results = []
    for index, sample in enumerate(samples):
        if on_progress:
            on_progress(target=target, completed=index, total=len(samples), image=sample['image'].name,
                        family=sample['family'], sample_number=index + 1, sample_state='running',
                        sample_started_at=datetime.now(timezone.utc).isoformat())
        started = time.monotonic()
        with Image.open(sample['image']) as source:
            image = source.convert('RGB')
        reference = sample['reference']
        item = {'family': sample['family'], 'image': sample['image'].name,
                'image_sha256': hashlib.sha256(sample['image'].read_bytes()).hexdigest()}
        if sample['family'] == 'layout':
            expected = json.loads(reference)
            prediction = client.layout(image, total_timeout=180)
            item.update(reference=expected, prediction=prediction,
                        raw_prediction=client.last_text,
                        layout=evaluate.layout_match(expected, prediction),
                        layout_strict=evaluate.layout_match(expected, prediction, iou_thr=0.75),
                        order=evaluate.order_metrics([g['label'] for g in expected], [p['label'] for p in prediction]))
        else:
            prediction = client.recognize(image, 'Table' if sample['family'] == 'table' else 'Text', total_timeout=180)
            item.update(reference=reference, prediction=prediction,
                        cer=evaluate.cer(reference, prediction), wer=evaluate.wer(reference, prediction))
            if sample['family'] == 'table':
                item['table'] = evaluate.table_metrics(otsl.otsl_to_grid(reference), prediction, lang='en')
        item['seconds'] = time.monotonic() - started
        results.append(item)
        if on_progress:
            on_progress(target=target, completed=index + 1, total=len(samples), image=sample['image'].name,
                        family=sample['family'], sample_number=index + 1, sample_state='finished')
    layouts = [r for r in results if r['family'] == 'layout']
    texts = [r for r in results if r['family'] == 'text']
    tables = [r['table'] for r in results if r['family'] == 'table']
    char_errors = sum(evaluate._edit_distance(list(r['reference']), list(r['prediction'])) for r in texts)
    word_errors = sum(evaluate._edit_distance(r['reference'].split(), r['prediction'].split()) for r in texts)
    characters = sum(len(r['reference']) for r in texts)
    words = sum(len(r['reference'].split()) for r in texts)
    return {'target': target, 'pages_evaluated': len(layouts), 'samples': results,
        'aggregates': {
            'layout': evaluate._agg_layout(layouts),
            'layout_strict': evaluate._agg_layout(layouts, 'layout_strict'),
            'order': evaluate._agg_order(layouts),
            'text': {'n': len(texts), 'mean_cer': evaluate._mean([r['cer'] for r in texts]),
                     'mean_wer': evaluate._mean([r['wer'] for r in texts]),
                     'micro_cer': char_errors / characters if characters else None,
                     'micro_wer': word_errors / words if words else None,
                     'character_errors': char_errors, 'characters': characters,
                     'word_errors': word_errors, 'words': words},
            'tables': {'n': len(tables), 'mean_teds': evaluate._mean([r.get('teds') for r in tables]),
                       'mean_cell_cer': evaluate._mean([r.get('cell_cer') for r in tables])}}}


def comparison_report(run_dir: Path, baseline: dict, fine_tuned: dict) -> dict:
    if [(s['image'], s['image_sha256']) for s in baseline['samples']] != [(s['image'], s['image_sha256']) for s in fine_tuned['samples']]:
        raise ValueError('Paired evaluation did not use identical images.')
    metrics = [('layout_f1', 'layout', 'f1', 'higher'),
               ('layout_mean_iou', 'layout', 'mean_iou_of_matched', 'higher'),
               ('text_cer', 'text', 'micro_cer', 'lower'),
               ('text_wer', 'text', 'micro_wer', 'lower'),
               ('table_teds', 'tables', 'mean_teds', 'higher')]
    deltas = {}
    directions = []
    for name, group, key, direction in metrics:
        before = baseline['aggregates'][group].get(key)
        after = fine_tuned['aggregates'][group].get(key)
        if before is None or after is None:
            continue
        delta = after - before
        improved = delta > 1e-9 if direction == 'higher' else delta < -1e-9
        regressed = delta < -1e-9 if direction == 'higher' else delta > 1e-9
        result = 'improved' if improved else 'regressed' if regressed else 'unchanged'
        directions.append(result)
        deltas[name] = {'baseline': before, 'fine_tuned': after, 'delta': delta,
                        'better': direction, 'result': result}
    verdict = ('mixed' if 'improved' in directions and 'regressed' in directions else
               'improved' if 'improved' in directions else 'regressed' if 'regressed' in directions else 'unchanged')
    counts = dict(Counter(s['family'] for s in baseline['samples']))
    limitations = [f"Descriptive comparison on {counts.get('layout', 0)} held-out pages; no statistical generalization is claimed.",
                   'Text metrics cover the exported held-out text crops, not unannotated document content.']
    if not counts.get('table'):
        limitations.append('No table-content quality conclusion is possible without held-out verified table samples.')
    meta_path = run_dir / 'run.json'
    meta = json.loads(meta_path.read_text(encoding='utf-8')) if meta_path.is_file() else {}
    train_rows = [json.loads(line) for line in (run_dir / 'dataset' / 'train.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    train_pages = sum(any(inference.LAYOUT_PROMPT in message.get('content', '') for message in row['messages'] if message['role'] == 'user') for row in train_rows)
    training_context = {'pages': train_pages, 'samples': len(train_rows),
                        'optimizer_steps': (meta.get('summary') or {}).get('steps'),
                        'epochs': (meta.get('config') or {}).get('epochs'),
                        'learning_rate': (meta.get('config') or {}).get('learning_rate')}
    return {'source_run_id': run_dir.name,
        'validation_sha256': hashlib.sha256((run_dir / 'dataset' / 'val.jsonl').read_bytes()).hexdigest(),
        'counts': counts, 'training_context': training_context,
        'output_changes': {'total': len(baseline['samples']),
                           'changed': sum(a['prediction'] != b['prediction'] for a, b in zip(baseline['samples'], fine_tuned['samples']))},
        'generation': {'temperature': 0.0, 'top_p': 1.0, 'seed': 42, 'max_pixels': 1003520},
        'baseline': baseline, 'fine_tuned': fine_tuned, 'deltas': deltas, 'verdict': verdict,
        'limitations': limitations}
