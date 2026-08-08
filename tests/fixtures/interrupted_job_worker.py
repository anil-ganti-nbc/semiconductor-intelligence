"""Standalone worker used by test_lifecycle_operations.py to simulate a hard
process kill mid-operation (subprocess termination, not physically killing
an unrelated process). Acquires a real lease and writes a real RUNNING
OperationalJobRun row, then blocks forever waiting to be killed -- exactly
the state a real crash would leave behind, since run_job()'s own
try/except/finally never gets a chance to run.
"""
from __future__ import annotations

import sys
import time

sys.path.insert(0, sys.argv[3])

from semi_intel.db import get_engine, get_sessionmaker, init_db
from semi_intel.domain.enums import OperationalJobStatus, OperationalJobType, OperationalTriggerType
from semi_intel.domain.models import OperationalJobRun
from semi_intel.operations.scheduler import LeaseManager
from semi_intel.notifications.service import utcnow

db_url = sys.argv[1]
job_type = OperationalJobType(sys.argv[2])

engine = get_engine(db_url)
init_db(engine)
session = get_sessionmaker(engine)()

now = utcnow()
lease_result = LeaseManager(session).acquire(job_type, duration_minutes=30, now=now)
assert lease_result.acquired, "worker could not acquire its lease"
job = OperationalJobRun(
    job_type=job_type, trigger_type=OperationalTriggerType.MANUAL_CLI,
    started_at=now, status=OperationalJobStatus.RUNNING,
    owner_identity="interrupted-job-worker", lock_token=lease_result.lease.lock_token,
)
session.add(job)
session.commit()
print("READY", flush=True)  # parent waits for this line before killing us

time.sleep(300)  # never reached in the test -- the parent kills us first
