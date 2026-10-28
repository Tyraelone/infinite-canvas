"""Wait for the already submitted canvas jobs, then render and verify the film."""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def main():
    production_path = Path(sys.argv[1]).resolve()
    runtime = Path(__file__).resolve().parent
    root = runtime.parent
    last_completed = None
    while True:
        try:
            production = json.loads(production_path.read_text(encoding='utf-8-sig'))
            targets = production['clipJobs']
            assert len(targets) == 12, 'The canvas has not submitted all twelve shots'
            with urllib.request.urlopen('http://127.0.0.1:8190/api/jobs', timeout=30) as response:
                jobs = {job['id']: job for job in json.load(response)['jobs']}
            selected = {}
            for number, task in targets.items():
                if task not in jobs:
                    with urllib.request.urlopen('http://127.0.0.1:8190/api/jobs/'+task, timeout=30) as response:
                        jobs[task] = json.load(response)
                selected[number] = jobs[task]
            completed = sorted(number for number, job in selected.items() if job['status'] == 'completed')
            failed = {number: {'id': job['id'], 'error': job.get('error'), 'status': job['status']}
                      for number, job in selected.items() if job['status'] in ('failed', 'cancelled')}
            active = {number: {'id': job['id'], 'stage': job.get('stage'), 'progress': job.get('progress'), 'elapsed': job.get('elapsed')}
                      for number, job in selected.items() if job['status'] == 'running'}
            progress = {'at': datetime.now(timezone.utc).isoformat(), 'completed': completed, 'total': 12,
                        'active': active, 'queued': sum(job['status'] == 'queued' for job in selected.values()), 'failed': failed}
            (production_path.parent/'render-progress.json').write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(progress, ensure_ascii=False), flush=True)
            if failed:
                raise RuntimeError('Resolve the terminal shot failure through the canvas Agent before resuming')
            if completed != last_completed:
                subprocess.run([sys.executable, '-B', '-s', str(runtime/'collect_film_shots.py'), str(production_path)], cwd=root, check=True)
                last_completed = completed
            if len(completed) == 12:
                break
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            print(json.dumps({'transientObservationError': str(error), 'action': 'keep the same jobs and retry observation'}), flush=True)
        time.sleep(30)
    manifest_path = production_path.parent/'edit.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if not Path(manifest['output']).exists():
        subprocess.run([sys.executable, '-B', '-s', str(runtime/'film_edit.py'), str(manifest_path)], cwd=root, check=True)
    subprocess.run([sys.executable, '-B', '-s', str(runtime/'verify_film.py'), str(manifest_path)], cwd=root, check=True)
    print(json.dumps({'status': 'film_rendered_and_decoded', 'output': manifest['output']}), flush=True)


if __name__ == '__main__':
    os.environ['PYTHONUTF8'] = '1'
    main()
