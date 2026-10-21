import json
from pathlib import Path
import tempfile
import unittest
import numpy as np

import timelines


class Model:
    def predict(self, x): return np.array([2, 1, 2])


class TimelineTests(unittest.TestCase):
    def data(self, source='pod-api'):
        return {'timeZone':'America/Los_Angeles','podStageSource':source,'nights':[{'id':5,'group':'2026-01-01','start':1000,'end':1900,
            'rows':[{'start':1000+i*300,'x':[i],'y':None if i!=1 else 2,'rule':1,'pod':None if i==2 else 0} for i in range(3)]}]}

    def test_alignment_and_missing_reference_are_preserved(self):
        data=self.data();doc=timelines.initial(data)
        rows=doc['sessions'][0]['rows']
        self.assertEqual([r['watch'] for r in rows],[None,2,None])
        self.assertEqual([r['pod'] for r in rows],[0,0,None])
        self.assertTrue(all(r['heldOut'] is None and r['final'] is None for r in rows))
        self.assertEqual([r['end'] for r in rows],[1300,1600,1900])

    def test_unlabeled_context_and_postprocessing_applied_to_both_modes(self):
        data=self.data();doc=timelines.initial(data)
        def smooth(values,rules):
            self.assertEqual(list(values),[2,1,2]);return [2,2,2]
        timelines.add_predictions(doc,data['nights'],Model(),'selected','heldOut',{},smooth)
        self.assertEqual([r['heldOut'] for r in doc['sessions'][0]['rows']],[2,2,2])
        self.assertTrue(all(r['final'] is None for r in doc['sessions'][0]['rows']))
        timelines.add_predictions(doc,data['nights'],Model(),'final_model','final',{},lambda p,r:p)
        self.assertEqual([r['final'] for r in doc['sessions'][0]['rows']],[2,1,2])
        self.assertEqual(doc['sessions'][0]['heldOutModel'],'selected')
        self.assertEqual(doc['sessions'][0]['finalModel'],'final_model')

    def test_offline_rules_are_identified_as_replay(self):
        doc=timelines.initial(self.data('replayed-rules'))
        self.assertEqual(doc['podSource'],'replayed-rules')
        self.assertEqual([r['pod'] for r in doc['sessions'][0]['rows']],[1,1,1])

    def test_epoch_misalignment_is_rejected(self):
        data=self.data();doc=timelines.initial(data)
        data['nights'][0]['rows'][0]['start']=999
        with self.assertRaisesRegex(ValueError,'alignment'):
            timelines.add_predictions(doc,data['nights'],Model(),'model','heldOut',{},lambda p,r:p)

    def test_atomic_roundtrip(self):
        doc=timelines.initial(self.data())
        with tempfile.TemporaryDirectory() as d:
            timelines.save(doc,d)
            self.assertEqual(json.loads((Path(d)/'timelines.json').read_text()),doc)
            self.assertFalse((Path(d)/'timelines.tmp').exists())


if __name__=='__main__': unittest.main()
