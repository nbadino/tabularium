#!/usr/bin/env python3
"""Rent one bounded GPU, compare saved weights with their base, and release it."""
from __future__ import annotations

import argparse
import json
import importlib
import shlex
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

from app import config
from app.services import cloud_manager, remote_eval, trainer, training_comparison, vault
from app.services.training_executor import SshExecutor


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True, type=int)
    parser.add_argument('--run', required=True)
    parser.add_argument('--source-run', required=True)
    parser.add_argument('--max-dph', type=float, default=0.6)
    args = parser.parse_args()
    run_dir, meta = trainer._load_run(args.project, args.run)
    source_dir, _ = trainer._load_run(args.project, args.source_run)
    samples = training_comparison.validation_samples(source_dir)
    checkpoint = trainer.checkpoint_info(run_dir)
    if not checkpoint or not checkpoint.get('merged_path'):
        raise ValueError('A complete registered merged checkpoint is required.')
    model_dir = Path(checkpoint['merged_path'])
    key = vault.get('vault:vast_api_key')
    if not key:
        raise ValueError('Vast.ai credential is unavailable.')
    job = {'state': 'running', 'phase': 'rent', 'started_at': datetime.now(timezone.utc).isoformat(),
           'completed': 0, 'total': len(samples) * 2, 'source_run_id': args.source_run,
           'instance_id': None, 'cleanup_confirmed': False, 'max_dph': args.max_dph}
    lock = threading.Lock()
    cancelled = threading.Event()
    log_file = run_dir / 'comparison.log'

    def update(**patch):
        with lock:
            job.update(patch)
            job['updated_at'] = datetime.now(timezone.utc).isoformat()
            _, latest = trainer._load_run(args.project, args.run)
            latest['comparison_job'] = dict(job)
            trainer.write_run_meta(run_dir, latest)
            (run_dir / 'comparison_job.json').write_text(json.dumps(job, indent=2), encoding='utf-8')

    def retry_comparison(operation):
        for attempt in range(3):
            try:
                return operation()
            except Exception as exc:
                if attempt == 2:
                    raise
                update(error=str(exc)[-3000:], phase='server_start', retry=attempt + 1)
                print('Comparison attempt failed; retaining the same GPU for bounded repair:', str(exc), flush=True)
                retry_marker = run_dir / 'retry-comparison'
                for _ in range(36):
                    if retry_marker.exists():
                        retry_marker.unlink()
                        break
                    time.sleep(5)
                importlib.reload(remote_eval)
                update(error=None)

    def release():
        instance = job.get('instance_id')
        if not instance:
            return
        for attempt in range(3):
            try:
                present = any(int(i.get('id') or 0) == int(instance) for i in cloud_manager.list_vast_instances(key))
                if present:
                    cloud_manager.control_vast_instance(key, int(instance), 'delete')
                    present = any(int(i.get('id') or 0) == int(instance) for i in cloud_manager.list_vast_instances(key))
                if not present:
                    update(cleanup_confirmed=True, provider_status='deleted', released_at=datetime.now(timezone.utc).isoformat())
                    return
            except Exception as exc:
                update(cleanup_error=str(exc)[-500:])
            time.sleep(2)
        raise RuntimeError('GPU release could not be verified.')

    def interrupted(_signal, _frame):
        cancelled.set()
        raise RuntimeError('Comparison interrupted.')

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    update()
    watchdog = threading.Timer(45 * 60, release)
    watchdog.daemon = True
    watchdog.start()
    succeeded = False
    try:
        offers = cloud_manager.search_vast_offers(key, max_dph=args.max_dph,
            min_gpu_ram_gb=24, min_compute_capability=8.0, min_cuda=12.8,
            min_inet_down=500, min_reliability=0.98, disk_gb=80, limit=50)
        # Ampere/Ada/Hopper are sufficient for this 0.7B paired evaluation.
        offers = [o for o in offers if float(o.get('compute_cap') or 0) <= 900]
        offers.sort(key=lambda o: (float(o.get('gpu_ram') or 0), float(o.get('inet_down') or 0)), reverse=True)
        for offer in offers:
            try:
                rented = cloud_manager.rent_vast_instance(key, int(offer['id']),
                    image='ghcr.io/nbadino/tabularium-train-monkey:monkey-v4', disk_gb=80)
                break
            except RuntimeError as exc:
                if 'no_such_ask' not in str(exc):
                    raise
        else:
            raise RuntimeError('No available compatible GPU within the hourly ceiling.')
        instance = int(rented['contract_id'])
        update(instance_id=instance, hourly_rate=float(offer['dph_total']), gpu=offer.get('gpu_name'), phase='startup')
        cloud_manager.track_cloud_resource('vast', instance, owner_id=1, hourly_rate=offer['dph_total'],
            metadata={'purpose': 'paired-training-evaluation', 'run_id': args.run})
        deadline = time.monotonic() + 15 * 60
        while time.monotonic() < deadline:
            item = next((i for i in cloud_manager.list_vast_instances(key) if int(i.get('id') or 0) == instance), None)
            if item:
                update(provider_status=item.get('status'))
                if item.get('is_running') and item.get('ssh_host') and item.get('ssh_port'):
                    try:
                        cloud_manager.pin_ssh_host_key(item['ssh_host'], int(item['ssh_port']))
                        access = cloud_manager.check_ssh_access(item['ssh_host'], int(item['ssh_port']))
                        if access.get('ok'):
                            break
                    except Exception:
                        pass
            time.sleep(8)
        else:
            raise RuntimeError('The comparison GPU did not become ready within 15 minutes.')
        executor = SshExecutor(host=item['ssh_host'], port=int(item['ssh_port']), user='root',
            key_path=str(cloud_manager.ssh_key_path()), known_hosts=config.SSH_KNOWN_HOSTS,
            remote_root='/tmp/tabularium-comparison')
        cfg = {**(meta.get('config') or {}), 'ssh_python': '/opt/tabularium/venv/bin/python',
               'ssh_train_repo': '/opt/tabularium/MonkeyOCRv2/parsing/train'}
        update(phase='environment', target='base')
        cfg = remote_eval.ensure_evaluation_runtime(executor, cfg, log_file)
        resolve = "from huggingface_hub import snapshot_download; print(snapshot_download('zenosai/MonkeyOCRv2-B-Parsing'))"
        update(phase='model_download')
        resolved = executor.run(shlex.quote(cfg['ssh_python']) + ' -c ' + shlex.quote(resolve), timeout=900)
        if resolved.returncode:
            raise RuntimeError('Base checkpoint preparation failed: ' + (resolved.stderr or '')[-2000:])
        base_path = resolved.stdout.strip().splitlines()[-1]

        def preflight():
            update(phase='server_start')
            with remote_eval.remote_endpoint(executor=executor, cfg=cfg, model_path=base_path, log_file=log_file):
                print('Base inference engine is ready on the rented GPU.', flush=True)
        retry_comparison(preflight)
        remote_model = '/tmp/tabularium-comparison/fine_tuned'
        made = executor.run(f'mkdir -p {remote_model}', timeout=30)
        if made.returncode:
            raise RuntimeError('Cannot create the remote comparison directory.')
        update(phase='upload', transfer={'done': 0, 'total': sum(p.stat().st_size for p in model_dir.rglob('*') if p.is_file())})
        command = ['rsync', '--archive', '--checksum', '--partial', '--exclude=.*',
                   '-e', 'ssh ' + ' '.join(shlex.quote(x) for x in executor._options()),
                   f'{model_dir}/', f'{executor._target()}:{remote_model}/']
        with log_file.open('ab') as log:
            upload = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
            while upload.poll() is None:
                result = executor.run(f'du -sb {remote_model}', timeout=20)
                if result.returncode == 0:
                    done = int(result.stdout.split()[0])
                    update(transfer={'done': done, 'total': job['transfer']['total']})
                time.sleep(4)
            if upload.returncode:
                raise RuntimeError('Merged checkpoint upload failed.')
        def progress(**values):
            target = values.pop('target')
            done = values.pop('completed')
            per_target = values.pop('total')
            phase = values.pop('phase', 'scoring')
            update(phase=phase, target=target,
                   completed=(len(samples) if target == 'fine_tuned' else 0) + done,
                   target_completed=done, target_total=per_target, **values)

        evaluation = retry_comparison(lambda: remote_eval.evaluate_on_remote(executor=executor, cfg=cfg, project_id=args.project,
            merged_remote=remote_model, lang='en', log_file=log_file, source_run_dir=source_dir,
            on_progress=progress))
        evaluation['comparison']['trained_run_id'] = args.source_run
        evaluation['comparison']['checkpoint_run_id'] = args.run
        report_file = run_dir / 'comparison.json'
        report_file.write_text(json.dumps(evaluation['comparison'], ensure_ascii=False, indent=2), encoding='utf-8')
        evaluation['comparison_report'] = str(report_file)
        _, latest = trainer._load_run(args.project, args.run)
        latest['evaluation'] = evaluation
        trainer.write_run_meta(run_dir, latest)
        update(phase='cleanup', completed=len(samples) * 2, result_path=str(report_file),
               verdict=evaluation['comparison']['verdict'])
        succeeded = True
    except Exception as exc:
        update(error=str(exc)[-3000:], phase='cleanup')
        print(type(exc).__name__, str(exc), flush=True)
    finally:
        try:
            release()
        except Exception as exc:
            update(cleanup_error=str(exc))
        watchdog.cancel()
        update(state='finished' if succeeded and job.get('cleanup_confirmed') else 'failed',
               phase='done' if succeeded else 'failed', ended_at=datetime.now(timezone.utc).isoformat())
    print(json.dumps(job, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
