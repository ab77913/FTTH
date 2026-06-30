"""Re-ingest KMZ with improved extractor."""
from sqlalchemy import create_engine, text

engine = create_engine('postgresql://ftth:ftth@localhost:5432/ftth')
with engine.connect() as conn:
    # Find jobs
    result = conn.execute(text("SELECT id, source_file FROM ingestion_jobs"))
    jobs = result.fetchall()
    for job in jobs:
        print(f"Job {job[0]}: {job[1]}")
    
    # Delete KMZ records and job
    for job in jobs:
        if '.kmz' in (job[1] or '').lower():
            job_id = job[0]
            print(f"\nDeleting KMZ job {job_id} and its records...")
            conn.execute(text(f"DELETE FROM dispatch_queue WHERE address_id IN (SELECT id FROM addresses WHERE job_id = '{job_id}')"))
            conn.execute(text(f"DELETE FROM addresses WHERE job_id = '{job_id}'"))
            conn.execute(text(f"DELETE FROM ingestion_jobs WHERE id = '{job_id}'"))
            conn.commit()
            print("Done!")
            break
