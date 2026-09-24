"""Offline scheduling, crash recovery and read-only waiting; no provider calls."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from volcengine_plugins.client import SeedreamClient, SeedreamError
from volcengine_plugins.generation_wait import GenerationTask, wait_tasks
from volcengine_plugins.image_jobs import ImageJobs, account_scope
from volcengine_plugins.models import ImageRequest, LocalOptions
from volcengine_plugins.task_io import TaskError


def client():
    return SeedreamClient(api_key='fake-key', base_url='https://api.example/v3', output_dir=Path('/unused-output'))


async def settled(jobs):
    await asyncio.gather(*tuple(jobs.workers))


def test_submission_returns_before_generation_and_reuses_receipt(tmp_path):
    async def run():
        jobs, remote = ImageJobs(tmp_path), client()
        gate, entered = asyncio.Event(), asyncio.Event()
        prompts = []
        async def generate(body, warnings, options):
            prompts.append(body['prompt'])
            entered.set()
            await gate.wait()
            return {'data': [{'local_path': '/saved/image.png'}]}
        remote.generate_prepared = generate
        request = ImageRequest(prompt='private-original-prompt')
        try:
            first = await jobs.create(remote, request, LocalOptions(), 'intent-one')
            assert first['task']['status'] == 'queued' and first['paid_request_sent'] is False
            await entered.wait()
            duplicate = await jobs.create(remote, request, LocalOptions(), 'intent-one')
            assert duplicate['reused'] and duplicate['task']['id'] == first['task']['id']
            request.prompt = 'changed-after-acceptance'
            with pytest.raises(TaskError, match='different input'):
                await jobs.create(remote, request, LocalOptions(), 'intent-one')
            assert jobs.get(first['task']['id'], account_scope(remote))['task']['status'] == 'running'
            gate.set()
            await settled(jobs)
            assert prompts == ['private-original-prompt']
            saved = jobs.get(first['task']['id'], account_scope(remote))
            assert saved['task']['status'] == 'succeeded'
            assert saved['task']['result']['data'][0]['local_path'] == '/saved/image.png'
            db_bytes = jobs.database.read_bytes()
            assert b'private-original-prompt' not in db_bytes and b'fake-key' not in db_bytes
        finally:
            await jobs.close()
        recovered = ImageJobs(tmp_path)
        try:
            assert recovered.get(first['task']['id'], account_scope(remote)) == saved
            with pytest.raises(ValueError, match='account'):
                recovered.get(first['task']['id'], 'different-account')
        finally:
            await recovered.close()
    asyncio.run(run())


def test_shared_queue_limit_and_dedup_across_managers(tmp_path):
    async def run():
        one, two, remote = ImageJobs(tmp_path), ImageJobs(tmp_path), client()
        gate = asyncio.Event()
        running = total = peak = 0
        async def generate(*args):
            nonlocal running, total, peak
            running += 1
            total += 1
            peak = max(peak, running)
            await gate.wait()
            running -= 1
            return {'data': []}
        remote.generate_prepared = generate
        request = ImageRequest(prompt='sample')
        try:
            a, b = await asyncio.gather(one.create(remote, request, LocalOptions(), 'same-intent'),
                                        two.create(remote, request, LocalOptions(), 'same-intent'))
            assert a['task']['id'] == b['task']['id']
            await two.create(remote, request, LocalOptions(), 'another-intent')
            pending = await one.create(remote, request, LocalOptions(), 'third-intent')
            await asyncio.sleep(0.05)
            assert running == 2
            assert two.get(pending['task']['id'], account_scope(remote))['task']['status'] == 'queued'
            gate.set()
            await asyncio.gather(settled(one), settled(two))
            assert peak == 2 and total == 3
        finally:
            await one.close()
            await two.close()
    asyncio.run(run())


def test_unknown_result_is_not_replayed_and_errors_are_sanitized(tmp_path):
    async def run():
        jobs, remote = ImageJobs(tmp_path), client()
        attempts = []
        async def generate(*args):
            attempts.append(1)
            raise RuntimeError('private-token secret-signed-url')
        remote.generate_prepared = generate
        request = ImageRequest(prompt='demo')
        first = await jobs.create(remote, request, LocalOptions(), 'uncertain-intent')
        await settled(jobs)
        await jobs.close()
        recovered = ImageJobs(tmp_path)
        try:
            receipt = await recovered.create(remote, request, LocalOptions(), 'uncertain-intent')
            assert receipt['reused'] and receipt['task']['status'] == 'unknown'
            assert receipt['task']['id'] == first['task']['id'] and attempts == [1]
            assert 'private-token' not in json.dumps(receipt)
        finally:
            await recovered.close()
    asyncio.run(run())


def test_shutdown_distinguishes_queued_from_dispatched(tmp_path):
    async def run():
        jobs, remote = ImageJobs(tmp_path), client()
        async def generate(*args):
            await asyncio.Event().wait()
        remote.generate_prepared = generate
        receipts = [await jobs.create(remote, ImageRequest(prompt='demo'), LocalOptions(), f'intent-{i:02}') for i in range(3)]
        await asyncio.sleep(0.05)
        await jobs.close()
        recovered = ImageJobs(tmp_path)
        try:
            states = [recovered.get(r['task']['id'], account_scope(remote)) for r in receipts]
            assert [r['task']['status'] for r in states] == ['unknown', 'unknown', 'interrupted']
            assert states[-1]['paid_request_sent'] is False
        finally:
            await recovered.close()
    asyncio.run(run())


def test_adapter_diagnostics_survive_background_execution(tmp_path):
    async def run():
        jobs, remote = ImageJobs(tmp_path), client()
        async def generate(*args):
            raise SeedreamError('Ark HTTP 403; code=Denied; request_id=fixture-request; no retry performed')
        remote.generate_prepared = generate
        try:
            receipt = await jobs.create(remote, ImageRequest(prompt='demo'), LocalOptions(), 'denied-intent')
            await settled(jobs)
            result = jobs.get(receipt['task']['id'], account_scope(remote))
            assert result['task']['status'] == 'unknown'
            assert 'fixture-request' in result['task']['result']['error']
            assert result['automatic_retry'] is False
        finally:
            await jobs.close()
    asyncio.run(run())


def test_abrupt_process_exit_releases_owner_lock_without_replay(tmp_path):
    script = '''
import asyncio, os, sys
from pathlib import Path
from volcengine_plugins.image_jobs import ImageJobs
from volcengine_plugins.client import SeedreamClient
from volcengine_plugins.models import ImageRequest, LocalOptions
async def run():
    jobs=ImageJobs(Path(sys.argv[1]))
    remote=SeedreamClient(api_key='fake-key',base_url='https://api.example/v3',output_dir=Path('/unused-output'))
    async def generate(*args):
        await asyncio.Event().wait()
    remote.generate_prepared=generate
    receipt=await jobs.create(remote,ImageRequest(prompt='demo'),LocalOptions(),'crashed-intent')
    await asyncio.sleep(0)
    print(receipt['task']['id'],flush=True)
    os._exit(0)
asyncio.run(run())
'''
    env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src'), 'ARK_READ_USER_ENV': '0'}
    task_id = subprocess.run([sys.executable, '-c', script, str(tmp_path)], env=env,
                             capture_output=True, text=True, check=True, timeout=15).stdout.strip()
    async def recover():
        jobs = ImageJobs(tmp_path)
        try:
            assert jobs.get(task_id, account_scope(client()))['task']['status'] == 'unknown'
            assert not jobs.workers
        finally:
            await jobs.close()
    asyncio.run(recover())


def test_preflight_missing_key_and_invalid_input_never_queue(tmp_path):
    async def run():
        jobs = ImageJobs(tmp_path)
        try:
            remote = SeedreamClient(api_key='', base_url='https://api.example/v3')
            with pytest.raises(TaskError, match='not configured'):
                await jobs.create(remote, ImageRequest(prompt='demo'), LocalOptions(), 'new-intent')
            with pytest.raises(TaskError, match='request_id'):
                await jobs.create(client(), ImageRequest(prompt='demo'), LocalOptions(), 'x')
            assert not jobs.workers
        finally:
            await jobs.close()
    asyncio.run(run())


def test_queue_capacity_is_bounded_before_paid_dispatch(tmp_path):
    async def run():
        jobs, remote = ImageJobs(tmp_path), client()
        async def forbidden(*args):
            pytest.fail('queued workers must not dispatch before this test closes them')
        remote.generate_prepared = forbidden
        try:
            for i in range(32):
                await jobs.create(remote, ImageRequest(prompt='fixture'), LocalOptions(), f'fixture-{i:02}')
            with pytest.raises(TaskError, match='full'):
                await jobs.create(remote, ImageRequest(prompt='fixture'), LocalOptions(), 'overflow-fixture')
        finally:
            await jobs.close()
    asyncio.run(run())


class Clock:
    def __init__(self): self.now = 0
    def __call__(self): return self.now
    async def sleep(self, seconds): self.now += seconds


def ref(kind='seedance', task_id='task-one'):
    return GenerationTask(kind=kind, task_id=task_id)


def test_wait_polls_internally_at_provider_interval_and_deduplicates():
    async def run():
        clock, observations = Clock(), []
        async def query(task):
            observations.append(clock())
            return {'task': {'id': task.task_id, 'status': ['queued', 'running', 'succeeded'][len(observations)-1]}}
        result = await wait_tasks([ref(), ref()], query, timeout_seconds=120, clock=clock, sleep=clock.sleep)
        assert observations == [0, 30, 60]
        assert result['status'] == 'ready' and len(result['items']) == 1
    asyncio.run(run())


def test_wait_all_keeps_partial_successes_and_does_not_retry_query_errors():
    async def run():
        clock, counts = Clock(), {}
        async def query(task):
            counts[task.task_id] = counts.get(task.task_id, 0)+1
            if task.task_id == 'bad': raise ValueError('private-api-token')
            return {'task': {'status': 'completed' if task.task_id == 'done' or counts[task.task_id] > 1 else 'running'}}
        result = await wait_tasks([ref(task_id=t) for t in ['done', 'bad', 'later']], query, mode='all',
                                  timeout_seconds=60, clock=clock, sleep=clock.sleep)
        assert counts == {'done': 1, 'bad': 1, 'later': 2}
        assert result['all_ready'] and result['items'][1]['status'] == 'query_error'
        assert 'private-api-token' not in json.dumps(result)
    asyncio.run(run())


def test_wait_any_does_not_wait_for_slow_query_sibling():
    async def run():
        cancelled = asyncio.Event()
        async def query(task):
            if task.task_id == 'slow':
                try: await asyncio.Event().wait()
                finally: cancelled.set()
            return {'task': {'status': 'succeeded'}}
        result = await asyncio.wait_for(wait_tasks([ref(task_id='slow'), ref(task_id='done')], query), 1)
        assert result['status'] == 'ready' and not result['all_ready']
        assert cancelled.is_set()
    asyncio.run(run())


def test_wait_timeout_respects_hints_and_zero_is_one_snapshot():
    async def run():
        clock, observations = Clock(), []
        async def query(task):
            observations.append(clock())
            return {'task': {'status': 'running'}, 'suggested_poll_after_seconds': 45}
        result = await wait_tasks([ref()], query, timeout_seconds=60, clock=clock, sleep=clock.sleep)
        assert result['status'] == 'timeout' and observations[:2] == [0, 45]
        observations.clear()
        result = await wait_tasks([ref()], query, timeout_seconds=0)
        assert len(observations) == 1 and result['status'] == 'timeout'
    asyncio.run(run())


def test_wait_cancellation_only_cancels_status_queries():
    async def run():
        entered, cancelled = asyncio.Event(), asyncio.Event()
        async def query(task):
            entered.set()
            try: await asyncio.Event().wait()
            finally: cancelled.set()
        waiting = asyncio.create_task(wait_tasks([ref()], query))
        await entered.wait()
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError): await waiting
        assert cancelled.is_set()
    asyncio.run(run())


@pytest.mark.parametrize('status', ['failed', 'expired', 'cancelled', 'unknown', 'interrupted', 'unexpected-new-state'])
def test_wait_terminal_and_unrecognized_status_do_not_loop(status):
    async def query(task): return {'task': {'status': status}}
    result = asyncio.run(wait_tasks([ref()], query))
    assert result['status'] == 'ready'
    assert result['items'][0]['status'] == ('query_error' if status == 'unexpected-new-state' else status)
