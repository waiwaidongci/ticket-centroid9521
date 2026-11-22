import logging
import time
from datetime import timedelta
from sqlalchemy import and_, or_, select
from src.config.database import Session
from src.constants.enums import Status
from src.domain import permutation
from src.models.permutation import PermutationJob
from src.models.source import Dataset
from src.services.audit import record
from src.utils.time import now

log = logging.getLogger(__name__)

LEASE_MINUTES = 3
# 单个租约内的计算时间预算，预留时间做最后一批的落库与续租。
LEASE_BUDGET_SECONDS = 100


def create_job(db, dataset_id, name, params, actor):
    """同步固定开跑场次与预测，缺场次直接拒绝，不产生后台任务。"""
    dataset = db.get(Dataset, dataset_id)
    if not dataset:
        raise ValueError('找不到指定的数据批次')
    snapshot = permutation.build_snapshot(dataset.rows, params)
    job = PermutationJob(
        dataset_id=dataset_id, name=name, status=Status.QUEUED, params=dict(snapshot['params']),
        snapshot=snapshot, total_combinations=snapshot['total_combinations'],
        settled_combinations=0, hit_combinations=0, generation=0, lease_until=None, author=actor,
    )
    db.add(job)
    db.flush()
    record(db, actor, 'permutation.queued', job.id, dataset_id=dataset_id,
           sessions=len(snapshot['sessions']), combinations=job.total_combinations)
    return job


def claim():
    """认领一个排队中或租约过期的任务，递增 generation 使旧进程写回失效。"""
    stamp = now()
    with Session.begin() as db:
        query = select(PermutationJob).where(or_(
            PermutationJob.status == Status.QUEUED,
            and_(PermutationJob.status == Status.RUNNING, PermutationJob.lease_until < stamp),
        )).order_by(PermutationJob.id).limit(1)
        job = db.scalar(query.with_for_update(skip_locked=True))
        if job is None:
            return None
        if job.attempt >= 3:
            job.status = Status.FAILED
            job.error = '后台进程多次中断，请发起新的显著性核对'
            job.lease_until = None
            job.finished_at = stamp
            record(db, 'worker', 'permutation.failed', job.id, reason=job.error)
            return None
        job.status = Status.RUNNING
        job.attempt += 1
        job.generation += 1
        job.lease_until = stamp + timedelta(minutes=LEASE_MINUTES)
        return job.id, job.attempt, job.generation


def claim_active():
    """认领一个仍在运行（租约有效）的任务继续算，不递增 generation。

    与 claim() 区分：租约过期意味着原进程可能已死，必须递增 generation 围栏；
    租约有效时只是同一 worker 的时间预算用尽，沿原 generation 续租续算即可。
    """
    with Session.begin() as db:
        job = db.scalar(
            select(PermutationJob).where(
                PermutationJob.status == Status.RUNNING,
                PermutationJob.lease_until > now(),
            ).order_by(PermutationJob.id).limit(1).with_for_update(skip_locked=True))
        if job is None:
            return None
        return job.id, job.attempt, job.generation


def _commit_batch(job_id, attempt, generation, settled, hits, finished, failure=None):
    """带围栏的进度写回：generation 不匹配说明已有更新进程接手，旧进度丢弃。"""
    stamp = now()
    with Session.begin() as db:
        job = db.get(PermutationJob, job_id, with_for_update=True)
        if job is None or job.generation != generation or job.attempt != attempt \
                or job.status != Status.RUNNING:
            log.info('Ignore stale permutation progress job=%s generation=%s->%s',
                     job_id, generation, job.generation if job else None)
            return False
        job.settled_combinations = settled
        job.hit_combinations = hits
        if finished:
            job.lease_until = None
            job.finished_at = stamp
            if failure:
                job.status = Status.FAILED
                job.error = failure[:500]
                record(db, 'worker', 'permutation.failed', job.id, attempt=attempt, reason=failure)
            else:
                job.status = Status.COMPLETE
                job.result = permutation.summary(job.snapshot, hits, job.total_combinations)
                record(db, 'worker', 'permutation.complete', job.id, attempt=attempt,
                       hits=hits, total=job.total_combinations)
        else:
            # 仅推进租期，任务保持 running，下一轮 worker 沿同一快照只算未结清组合。
            job.lease_until = stamp + timedelta(minutes=LEASE_MINUTES)
        return True


def execute(lease):
    """沿开跑快照只算未结清组合；每批落库并续租，租期内尽量多结，崩溃可断点续算。"""
    job_id, attempt, generation = lease
    with Session() as db:
        job = db.get(PermutationJob, job_id)
        snapshot = job.snapshot
        total = job.total_combinations
        settled = job.settled_combinations
        hits = job.hit_combinations
    started = time.monotonic()
    try:
        while True:
            batch = permutation.next_batch(settled, total)
            if batch is None:
                _commit_batch(job_id, attempt, generation, settled, hits, finished=True)
                return
            start, end = batch
            hits += permutation.count_batch(snapshot, start, end)
            settled = end
            if settled >= total:
                _commit_batch(job_id, attempt, generation, settled, hits, finished=True)
                return
            # 每批落库一次进度并续租；租约预算用尽则退出，下一轮 worker 沿同一快照继续。
            alive = _commit_batch(job_id, attempt, generation, settled, hits, finished=False)
            if not alive or time.monotonic() - started >= LEASE_BUDGET_SECONDS:
                return
    except Exception:
        log.exception('Permutation %s failed', job_id)
        _commit_batch(job_id, attempt, generation, settled, hits, finished=True,
                      failure='显著性核对计算失败，请检查批次与场次后重试')
