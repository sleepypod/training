import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from progress import update
from telemetry import summarize_features


class TelemetryTests(unittest.TestCase):
    def test_complete_event_log_and_bounded_dashboard_history(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'TRAINING_PROGRESS': str(Path(d)/'progress.json')}), contextlib.redirect_stderr(io.StringIO()):
            for i in range(130):
                update('Training', 'fit', fitResult={'fit': i, 'seconds': .1})
            state=json.loads((Path(d)/'progress.json').read_text())
            events=[json.loads(line) for line in (Path(d)/'events.jsonl').read_text().splitlines()]
            self.assertEqual(len(state['events']),120)
            self.assertEqual(len(state['fitHistory']),80)
            self.assertEqual(len(events),130)
            self.assertEqual(events[0]['fitResult']['fit'],0)
            self.assertEqual(events[-1]['fitResult']['fit'],129)

    def test_feature_summary_preserves_unlabeled_context_and_input(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'features.json'
            data={'features':['a','b'],'nights':[{'rows':[{'x':[0,2],'y':0},{'x':[2,4],'y':None},{'x':[4,6],'y':2}]}]}
            original=json.dumps(data);path.write_text(original)
            with patch('telemetry.update') as emit:
                summary=summarize_features(path,2)
            self.assertEqual(summary['labeled'],2)
            self.assertEqual(summary['unlabeled'],1)
            self.assertEqual(summary['classes'],{'wake':1,'light':0,'deep':1,'rem':0})
            self.assertEqual(summary['matrixBytes'],48)
            self.assertEqual(summary['rowsPerSecond'],1.5)
            self.assertEqual(summary['columns'][0]['mean'],2)
            self.assertEqual(path.read_text(),original)
            self.assertEqual(emit.call_args.kwargs['dataset'],summary)

    def test_nonfinite_features_fail_instead_of_charts_hiding_bad_input(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'features.json'
            path.write_text(json.dumps({'features':['a'],'nights':[{'rows':[{'x':[float('nan')],'y':0}]}]}))
            with self.assertRaisesRegex(ValueError,'non-finite'):summarize_features(path,1)

    def test_verbose_terminal_keeps_newlines(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'TRAINING_PROGRESS':str(Path(d)/'progress.json'),'TRAINING_VERBOSE':'1'}):
            stream=io.StringIO()
            with contextlib.redirect_stderr(stream), patch.object(stream,'isatty',return_value=True):
                update('Training','one');update('Training','two')
            self.assertNotIn('\r',stream.getvalue())
            self.assertEqual(len(stream.getvalue().splitlines()),2)


if __name__=='__main__':unittest.main()
