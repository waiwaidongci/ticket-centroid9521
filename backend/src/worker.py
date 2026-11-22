import logging
import time
from pathlib import Path
from src.services import permutation_jobs
from src.services.execution import claim, execute, materialize_due
from src.utils.wakeup import wait

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)
heartbeat = Path('/tmp/analysis-heartbeat')


def main():
    while True:
        try:
            materialize_due()
            lease = claim()
            if lease:
                execute(lease)
                heartbeat.touch()
                continue
            permutation_lease = permutation_jobs.claim() or permutation_jobs.claim_active()
            if permutation_lease:
                permutation_jobs.execute(permutation_lease)
                heartbeat.touch()
            else:
                wait()
        except Exception:
            log.exception('Worker cycle failed')
            time.sleep(2)


if __name__ == '__main__':
    main()
