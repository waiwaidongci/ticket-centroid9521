from datetime import datetime
from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base
from src.constants.enums import Status
from src.utils.time import now


class PermutationJob(Base):
    """场次置换检验任务。

    快照在启动时固定开跑场次与两套预测；settled/hits 按批落库支持中断续算。
    每次被 worker 认领都会递增 generation，旧进程结束时按 generation 条件写回，
    租期失效后新旧进程同时写进度时，旧进程的写入不会生效。
    """

    __tablename__ = 'permutation_jobs'

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey('datasets.id'))
    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default=Status.QUEUED, index=True)
    params: Mapped[dict] = mapped_column(JSON)
    snapshot: Mapped[dict] = mapped_column(JSON)
    total_combinations: Mapped[int]
    settled_combinations: Mapped[int] = mapped_column(default=0)
    hit_combinations: Mapped[int] = mapped_column(default=0)
    result: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(String(500))
    attempt: Mapped[int] = mapped_column(default=0)
    generation: Mapped[int] = mapped_column(default=0)
    lease_until: Mapped[datetime | None]
    author: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=now)
    finished_at: Mapped[datetime | None]
