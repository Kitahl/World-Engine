import tempfile
import unittest
from pathlib import Path

from world_engine import WorldEngine


class TestV38NpcLife(unittest.TestCase):
    def setUp(self):
        self.td=tempfile.TemporaryDirectory(); self.e=WorldEngine(Path(self.td.name)/'w.sqlite3'); self.e.ensure_campaign('c','C')
        self.e.upsert_location('c','village','Village')
        self.e.upsert_npc('c','n1','Mara',location='village')
        self.e.upsert_npc('c','n2','Bren',location='village')
    def tearDown(self): self.td.cleanup()
    def test_schema_version_11(self):
        with self.e._db() as db:
            self.assertEqual(db.execute('pragma user_version').fetchone()[0],20)
            for t in ('npc_thoughts','npc_archetype_profiles','npc_jobs','npc_job_reservations'):
                self.assertTrue(db.execute("select 1 from sqlite_master where type='table' and name=?",(t,)).fetchone())
    def test_causal_mood(self):
        self.e.npc_life_dispatch('add_thought','c',{'npc_id':'n1','thought_id':'grief','cause':'brother died','mood_delta':-25})
        self.e.npc_life_dispatch('add_thought','c',{'npc_id':'n1','thought_id':'rest','cause':'well rested','mood_delta':8})
        m=self.e.npc_life_dispatch('mood','c',{'npc_id':'n1'})
        self.assertEqual(m['mood'],-17)
        self.assertEqual({x['id'] for x in m['thoughts']},{'grief','rest'})
    def test_seed_canonical_needs(self):
        r=self.e.npc_life_dispatch('seed_needs','c',{'npc_id':'n1'})
        self.assertEqual(len(r['needs']),10)
        with self.e._db() as db:self.assertEqual(db.execute("select count(*) n from npc_needs where campaign_id='c' and npc_id='n1'").fetchone()['n'],10)
    def test_archetype_applies_actions_and_needs(self):
        self.e.npc_life_dispatch('save_archetype','c',{'archetype_id':'smith','name':'Smith','needs':{'wealth':{'baseline':40,'drift_per_day':1,'curve':'linear'}},'actions':[{'id':'work','base_utility':1,'effects':[{'type':'need','need':'wealth','delta':-10}]}]})
        r=self.e.npc_life_dispatch('apply_archetype','c',{'npc_id':'n1','archetype_id':'smith'})
        self.assertEqual(r['actions_installed'],1)
        self.assertEqual(self.e.get_npc('c','n1')['archetype_id'],'smith')
    def test_job_capacity_is_enforced(self):
        self.e.npc_life_dispatch('create_job','c',{'job_id':'repair','kind':'repair','title':'Repair gate','capacity':1})
        self.e.npc_life_dispatch('reserve_job','c',{'job_id':'repair','npc_id':'n1'})
        with self.assertRaises(ValueError):self.e.npc_life_dispatch('reserve_job','c',{'job_id':'repair','npc_id':'n2'})
    def test_job_release_reopens_slot(self):
        self.e.npc_life_dispatch('create_job','c',{'job_id':'repair','kind':'repair','title':'Repair gate','capacity':1})
        self.e.npc_life_dispatch('reserve_job','c',{'job_id':'repair','npc_id':'n1'})
        self.e.npc_life_dispatch('release_job','c',{'job_id':'repair','npc_id':'n1'})
        r=self.e.npc_life_dispatch('reserve_job','c',{'job_id':'repair','npc_id':'n2'})
        self.assertEqual(r['status'],'active')
    def test_goap_multistep(self):
        actions=[
            {'id':'work','preconditions':{'money':{'lt':1}},'effects':{'money':{'delta':1}},'cost':1},
            {'id':'buy','preconditions':{'money':{'ge':1}},'effects':{'money':{'delta':-1},'food':{'delta':1}},'cost':1},
            {'id':'eat','preconditions':{'food':{'ge':1}},'effects':{'food':{'delta':-1},'fed':{'set':True}},'cost':1},
        ]
        r=self.e.npc_life_dispatch('plan','c',{'start':{'money':0,'food':0,'fed':False},'goal':{'fed':True},'actions':actions,'max_depth':5})
        self.assertTrue(r['found']); self.assertEqual(r['plan'],['work','buy','eat'])
    def test_goap_budgeted_failure(self):
        r=self.e.npc_life_dispatch('plan','c',{'start':{'a':0},'goal':{'a':2},'actions':[{'id':'inc','effects':{'a':{'delta':1}}}],'max_depth':1})
        self.assertFalse(r['found'])
    def test_mood_consideration_in_decide_scoring(self):
        self.e.npc_life_dispatch('add_thought','c',{'npc_id':'n1','thought_id':'happy','cause':'good day','mood_delta':80})
        self.e.save_npc_action('c','n1','socialize',base_utility=0,considerations=[{'type':'mood','weight':1.0}])
        with self.e._db() as db:
            npc=db.execute("select * from npcs where campaign_id='c' and id='n1'").fetchone()
            ranked=__import__('world_engine.simulation',fromlist=['SimulationKernel']).SimulationKernel(self.e)._score_actions(db,'c',npc,None,0)
        self.assertEqual(ranked[0][1],'socialize'); self.assertGreater(ranked[0][0],.5)

if __name__=='__main__':unittest.main()
