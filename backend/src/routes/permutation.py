from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from src.config.database import connection
from src.constants.enums import Role
from src.controllers.auth import allow, current
from src.models.identity import User
from src.models.permutation import PermutationJob
from src.schemas.requests import RunRequest
from src.services import permutation_jobs
from src.services.validation import normalize_params
from src.utils import views
from src.utils.wakeup import wake

router = APIRouter(prefix='/permutations')


@router.get('')
def index(db: Session = Depends(connection), actor: User = Depends(current)):
    items = db.scalars(select(PermutationJob).order_by(PermutationJob.id.desc()).limit(100))
    return [views.permutation(item) for item in items]


@router.post('', status_code=202)
def create(body: RunRequest, db: Session = Depends(connection),
           actor: User = Depends(allow(Role.ADMIN, Role.ANALYST))):
    # build_snapshot 在此同步校验场次并固定开跑预测，缺场次直接 422，任务不入库。
    job = permutation_jobs.create_job(
        db, body.dataset_id, body.name, normalize_params(body.params), actor.username)
    db.commit()
    wake()
    return views.permutation(job, detail=True)


@router.get('/{job_id}')
def detail(job_id: int, db: Session = Depends(connection), actor: User = Depends(current)):
    item = db.get(PermutationJob, job_id)
    if not item:
        raise HTTPException(404, '找不到该显著性核对记录')
    return views.permutation(item, detail=True)
