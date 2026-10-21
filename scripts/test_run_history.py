import json
import os
from pathlib import Path
import tempfile
import time
import unittest
import urllib.request
import urllib.error
from dashboard import payload, serve, write_report
from run_history import Catalog
from diagnostics import describe


class RunHistoryTests(unittest.TestCase):
    def run_fixture(self, path, status='complete'):
        path.mkdir(parents=True)
        (path / 'progress.json').write_text(json.dumps({'status':status, 'startedAt':1000, 'elapsedSeconds':20}))
        return path

    def test_import_is_idempotent_and_preserves_files(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);run=self.run_fixture(root/'data'/'old');before=(run/'progress.json').read_bytes()
            catalog=Catalog(root/'history.sqlite')
            self.assertEqual(catalog.discover([root/'data']),1)
            key=catalog.register(run)
            self.assertEqual(len(Catalog(root/'history.sqlite').list()),1)
            self.assertEqual(catalog.resolve(key),run.resolve())
            self.assertEqual((run/'progress.json').read_bytes(),before)
            self.assertIsNone(catalog.resolve('../../etc/passwd'))

    def test_discovery_ignores_symlink_and_non_run_input_folders(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);outside=self.run_fixture(root/'elsewhere'/'run');data=root/'data';data.mkdir()
            (data/'link').symlink_to(outside,target_is_directory=True)
            (data/'input.json').write_text('{}')
            catalog=Catalog(root/'history.sqlite')
            self.assertEqual(catalog.discover([data]),0)
            with self.assertRaises(ValueError):catalog.register(data)

    def test_stale_legacy_state_is_interrupted_without_rewriting_history(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);run=self.run_fixture(root/'old','running')
            os.utime(run/'progress.json',(1,1));catalog=Catalog(root/'history.sqlite');catalog.register(run)
            self.assertEqual(catalog.list()[0]['status'],'interrupted')
            self.assertEqual(json.loads((run/'progress.json').read_text())['status'],'running')

    def test_multiple_runs_routes_and_private_file_allowlist(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);a=self.run_fixture(root/'a');b=self.run_fixture(root/'b','failed')
            (a/'secret.txt').write_text('do not serve');catalog=Catalog(root/'history.sqlite')
            akey=catalog.register(a);bkey=catalog.register(b);server=serve(a,catalog)
            try:
                base=f'http://127.0.0.1:{server.server_port}'
                def get(path):return json.load(urllib.request.urlopen(base+path))
                self.assertEqual(len(get('/api/runs')),2)
                self.assertEqual(get('/api/runs/'+bkey)['state']['status'],'failed')
                for path in ['/secret.txt','/api/runs/'+akey+'/secret.txt','/api/runs/../../secret.txt','/api/runs/'+akey+'/candidate/extra']:
                    with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(base+path)
                html=urllib.request.urlopen(base+'/api/runs/'+akey+'/report').read().decode()
                self.assertIn('const embedded',html)
                self.assertNotIn('__EMBEDDED__',html)
                self.assertNotIn('__CSS__',html)
                self.assertNotIn('__JS__',html)
            finally:server.shutdown();server.server_close()

    def test_historical_timeline_inputs_join_without_fabricating_baseline(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'model').mkdir()
            (root/'features.json').write_text(json.dumps({'features':['hrZ'],'nights':[{'id':1,'group':'a','rows':[{'start':100,'x':[2.0],'y':1}]}]}))
            (root/'model/timelines.json').write_text(json.dumps({'sessions':[{'id':'1','rows':[{'start':100,'watch':1,'heldOut':2}]}]}))
            result=payload(root)['timelines']
            self.assertEqual(result['sessions'][0]['rows'][0]['x'],[2.0])
            self.assertNotIn('baseline',result['sessions'][0]['rows'][0])

    def test_diagnostics_counts_missing_labels_separately_and_records_drift(self):
        data={'features':['constant','changing'],'nights':[{'id':i,'group':str(i),'rows':[{'start':j*300,'x':[1,i*10+j],'y':None if j==0 else 1} for j in range(4)]} for i in range(2)]}
        result=describe(data)
        self.assertEqual(result['nonfinite'],0)
        self.assertEqual(result['labeled'],6)
        self.assertIsNone(result['columns'][0]['skew'])
        self.assertEqual(sum(result['columns'][1]['histogram']['counts']),8)
        self.assertEqual(len(result['columns'][1]['drift']),2)
        self.assertEqual(result['columns'][1]['drift'][0]['statistic'],1)
