"""Exercise the real --pod CLI against a local synthetic HTTP pod (no real data)."""
import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from threading import Thread
from urllib.parse import urlsplit, parse_qs

from fetch_pod import iso, millis


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('inputs', type=Path)
    ap.add_argument('features', type=Path, help='Features from the preceding synthetic snapshot run')
    ap.add_argument('output', type=Path)
    args = ap.parse_args()
    spec = importlib.util.spec_from_file_location('watch', Path(__file__).with_name('parse-watch-export.py'))
    parser = importlib.util.module_from_spec(spec); spec.loader.exec_module(parser)
    with sqlite3.connect(args.inputs / 'biometrics.db') as db:
        sessions = db.execute('select id,side,entered_bed_at,left_bed_at from sleep_records').fetchall()
        vitals = db.execute('select rowid,side,timestamp,heart_rate,hrv,breathing_rate from vitals').fetchall()
        movement = db.execute('select rowid,side,timestamp,total_movement from movement').fetchall()
    # Shift invented dates to recent UTC midnights so --days works at any CI date.
    offset = int(datetime.now(timezone.utc).timestamp() // 86400) * 86400000 - 7 * 86400000 - sessions[0][2] * 1000
    datasets = {
        'sleep-records': [dict(id=i, side=s, enteredBedAt=iso(a*1000+offset), leftBedAt=iso(b*1000+offset)) for i,s,a,b in sessions],
        'vitals': [dict(id=i, side=s, timestamp=iso(t*1000+offset), heartRate=h, hrv=v, breathingRate=b) for i,s,t,h,v,b in vitals],
        'movement': [dict(id=i, side=s, timestamp=iso(t*1000+offset), totalMovement=m) for i,s,t,m in movement],
        'reference-stages': [dict(id=i, side='left', source='apple_watch', start=iso(a+offset), end=iso(b+offset), stage=s) for i,(a,b,s) in enumerate(parser.parse(args.inputs / 'export.zip')['stages'])],
    }
    overview = {'stageView': {'classifier': {'ruleset': json.loads(args.features.read_text())['ruleset']}, 'provenance': {'generation': 1}}, 'sensorCalibration': []}
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            assert not self.headers.get('Authorization')
            url = urlsplit(self.path); q = {k:v[0] for k,v in parse_qs(url.query).items()}
            name = url.path.rsplit('/',1)[-1]
            if name == 'settings': result = {'device': {'timezone': 'UTC'}}
            elif name == 'overview': result = overview
            elif name == 'sleep-stages':
                record=next(r for r in datasets['sleep-records'] if r['id']==int(q['sleepRecordId']))
                a,b=millis(record['enteredBedAt']),millis(record['leftBedAt'])
                result={'sleepRecordId':record['id'],'stageView':overview['stageView'],'epochs':[{'start':t,'duration':300000,'stage':'light'} for t in range(a,b,300000)]}
            elif name in datasets:
                a,b = millis(q['startDate']),millis(q['endDate'])
                def included(r):
                    if r['side'] != q['side']: return False
                    if name == 'reference-stages': return millis(r['start']) <= b and millis(r['end']) > a
                    return a <= millis(r['enteredBedAt'] if name == 'sleep-records' else r['timestamp']) <= b
                result = [r for r in datasets[name] if included(r)][:int(q['limit'])]
            else: self.send_error(404);return
            raw = json.dumps(result).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(raw)
        def log_message(self,*args): pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    Thread(target=server.serve_forever,daemon=True).start()
    try:
        subprocess.run([sys.executable,str(Path(__file__).with_name('train.py')),'--pod',f'127.0.0.1:{server.server_port}','--side','left','--out',str(args.output)],check=True)
    finally:
        server.shutdown();server.server_close()
    state=json.loads((args.output/'progress.json').read_text())
    report=json.loads((args.output/'model/report.json').read_text())
    assert state['status']=='complete' and state['completedFits']==state['totalFits']
    assert report['groups']==5 and report['artifact']['parityRows']==180
    assert (args.output/'index.html').exists() and (args.output/'acquisition.json').exists()
    assert any('reference-stages' in r for r in requests)
    acquisition=json.loads((args.output/'acquisition.json').read_text())
    assert state['transfer']['requests']==len(acquisition['requests'])
    assert state['transfer']['bytes']==sum((args.output/'responses'/r['file']).stat().st_size for r in acquisition['requests'])
    assert state['dataCounts']['vitals']==900 and state['dataCounts']['movement']==900
    assert state['dataset']['rows']==180 and state['dataset']['labeled']==180
    assert len(state['heldOutHistory'])==5 and len(state['fitHistory'])==80
    events=[json.loads(line) for line in (args.output/'events.jsonl').read_text().splitlines()]
    assert len([e for e in events if 'fitResult' in e])==state['completedFits']
    assert events[-1]['status']=='complete'
    traces=json.loads((args.output/'model/timelines.json').read_text())
    assert traces['podSource']=='pod-api' and len(traces['sessions'])==5
    assert all(r['heldOut'] is not None and r['final'] is not None and r['pod']==1 for n in traces['sessions'] for r in n['rows'])
    confusion=[[0]*4 for _ in range(4)]
    for night in traces['sessions']:
        for row in night['rows']:
            if row['watch'] is not None: confusion[row['watch']][row['heldOut']]+=1
    assert confusion==report['scores']['nested']['confusion']
    print('Synthetic HTTP pod → features → training → parity → report passed.')


if __name__ == '__main__': main()
