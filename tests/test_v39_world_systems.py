import tempfile
import unittest
from pathlib import Path

from world_engine import WorldEngine

class TestV39WorldSystems(unittest.TestCase):
    def setUp(self):
        self.td=tempfile.TemporaryDirectory();self.e=WorldEngine(Path(self.td.name)/'w.sqlite3');self.e.ensure_campaign('c','C')
        for lid in ('village','mine','town'):
            self.e.upsert_location('c',lid,lid.title())
        self.e.upsert_npc('c','n1','Mara',location='village')
        self.e.upsert_faction('c','f1','Village Watch');self.e.upsert_faction('c','f2','Bandits')
        self.e.upsert_quest('c','q1','Protect village')
    def tearDown(self):self.td.cleanup()
    def test_sparse_3d_map_and_z_path(self):
        self.e.world_systems_dispatch('save_map','c',{'map_id':'v','name':'Village','bounds':{'min_x':0,'max_x':2,'min_y':0,'max_y':2,'min_z':-1,'max_z':1}})
        r=self.e.world_systems_dispatch('find_path','c',{'start':{'map_id':'v','x':0,'y':0,'z':0},'goal':{'map_id':'v','x':2,'y':2,'z':1}})
        self.assertTrue(r['found']);self.assertEqual(r['path'][-1]['z'],1)
    def test_blocked_tile_reroutes(self):
        self.e.world_systems_dispatch('save_map','c',{'map_id':'v','name':'Village','bounds':{'min_x':0,'max_x':2,'min_y':0,'max_y':1,'min_z':0,'max_z':0}})
        self.e.world_systems_dispatch('save_tile','c',{'map_id':'v','x':1,'y':0,'z':0,'walkable':False,'terrain':'wall'})
        r=self.e.world_systems_dispatch('find_path','c',{'start':{'map_id':'v','x':0,'y':0,'z':0},'goal':{'map_id':'v','x':2,'y':0,'z':0}})
        self.assertTrue(r['found']);self.assertFalse(any(p['x']==1 and p['y']==0 for p in r['path']))
    def test_cross_map_portal(self):
        for mid in ('a','b'):self.e.world_systems_dispatch('save_map','c',{'map_id':mid,'name':mid,'bounds':{'min_x':0,'max_x':1,'min_y':0,'max_y':0,'min_z':0,'max_z':0}})
        self.e.world_systems_dispatch('save_portal','c',{'portal_id':'door','from_map_id':'a','from_pos':[1,0,0],'to_map_id':'b','to_pos':[0,0,0]})
        r=self.e.world_systems_dispatch('find_path','c',{'start':{'map_id':'a','x':0,'y':0,'z':0},'goal':{'map_id':'b','x':1,'y':0,'z':0}})
        self.assertTrue(r['found']);self.assertTrue(any(p['via_portal']=='door' for p in r['path']))
    def test_persistent_terrain_damage(self):
        self.e.world_systems_dispatch('save_map','c',{'map_id':'v','name':'Village','bounds':{'min_x':0,'max_x':0,'min_y':0,'max_y':0,'min_z':0,'max_z':0}})
        self.e.world_systems_dispatch('save_tile','c',{'map_id':'v','x':0,'y':0,'z':0,'terrain':'wall','walkable':False,'blocks_los':True,'terrain_hp':10})
        r=self.e.world_systems_dispatch('damage_tile','c',{'map_id':'v','x':0,'y':0,'z':0,'damage':10})
        self.assertTrue(r['destroyed'])
        snap=self.e.world_systems_dispatch('snapshot','c',{'map_id':'v'});self.assertTrue(snap['tiles'][0]['walkable'])
    def test_passive_perception_reveals_secret(self):
        self.e.world_systems_dispatch('save_discoverable','c',{'object_id':'s','kind':'secret','dc':14})
        self.assertEqual(self.e.world_systems_dispatch('passive_scan','c',{'perception':13})['count'],0)
        self.assertEqual(self.e.world_systems_dispatch('passive_scan','c',{'perception':14})['revealed'],['s'])
    def test_reward_grants_inventory(self):
        self.e.world_systems_dispatch('save_reward','c',{'reward_id':'r','xp':100,'items':[{'item_id':'gem','qty':2}]})
        self.e.world_systems_dispatch('grant_reward','c',{'reward_id':'r','actor_kind':'npc','actor_id':'n1'})
        inv=self.e.get_inventory_items('c','npc','n1');self.assertEqual(next(x for x in inv if x['item_id']=='gem')['qty'],2)
    def test_quest_graph_persists(self):
        self.e.world_systems_dispatch('save_quest_node','c',{'quest_id':'q1','node_id':'defend','status':'active','failure':{'event':'mayor_dead'}})
        self.e.world_systems_dispatch('save_quest_node','c',{'quest_id':'q1','node_id':'after','status':'inactive'})
        self.e.world_systems_dispatch('save_quest_edge','c',{'quest_id':'q1','from_node':'defend','to_node':'after','condition':{'event':'raid_repulsed'}})
        with self.e._db() as db:self.assertEqual(db.execute("select count(*) n from quest_edges where campaign_id='c' and quest_id='q1'").fetchone()['n'],1)
    def test_faction_relation(self):
        r=self.e.world_systems_dispatch('save_faction_relation','c',{'faction_a':'f1','faction_b':'f2','stance':'war','tension':90})
        self.assertEqual(r['stance'],'war')
    def test_crime_bounty(self):
        r=self.e.world_systems_dispatch('record_crime','c',{'crime_id':'c1','offender_kind':'npc','offender_id':'n1','jurisdiction':'village','offense':'theft','severity':3,'evidence':.5})
        self.assertGreater(r['bounty'],0)
    def test_rumor_propagation(self):
        self.e.world_systems_dispatch('save_rumor','c',{'rumor_id':'r1','claim':'King is dead','truth_confidence':.9})
        r=self.e.world_systems_dispatch('propagate_rumor','c',{'rumor_id':'r1','npc_id':'n1'})
        self.assertIn('n1',r['heard_by']);self.assertLess(r['truth_confidence'],.9)
    def test_population_migration(self):
        self.e.world_systems_dispatch('set_population','c',{'location_id':'village','population':200})
        self.e.world_systems_dispatch('set_population','c',{'location_id':'town','population':100})
        r=self.e.world_systems_dispatch('migrate','c',{'origin':'village','destination':'town','count':25})
        self.assertEqual(r['moved'],25)
        with self.e._db() as db:self.assertEqual(db.execute("select population from population_state where campaign_id='c' and location_id='village'").fetchone()['population'],175)
    def test_divine_and_vision(self):
        self.e.world_systems_dispatch('set_divine_state','c',{'actor_kind':'npc','actor_id':'n1','power_id':'gaia','favor':10,'corruption':2})
        r=self.e.world_systems_dispatch('add_vision','c',{'vision_id':'v1','actor_kind':'npc','actor_id':'n1','power_id':'gaia','reason':'high favor'})
        self.assertFalse(r['delivered'])
    def test_affliction_homestead_service_climate_encounter(self):
        self.assertEqual(self.e.world_systems_dispatch('set_affliction','c',{'actor_kind':'npc','actor_id':'n1','affliction_id':'wolf','kind':'lycanthropy','stage':1,'max_stage':3})['stage'],1)
        self.e.world_systems_dispatch('save_homestead','c',{'homestead_id':'h1','owner_kind':'npc','owner_id':'n1','location_id':'village'})
        self.e.world_systems_dispatch('save_service','c',{'service_id':'inn','location_id':'village','kind':'inn','name':'Bent Copper'})
        self.e.world_systems_dispatch('set_climate','c',{'scope_type':'location','scope_id':'village','climate':'temperate','season':'autumn','magic_theme':'old forest'})
        r=self.e.world_systems_dispatch('save_encounter_template','c',{'template_id':'bandit','name':'Bandit raid','difficulty':3})
        self.assertEqual(r['difficulty'],3)
    def test_execute_recipe_transaction(self):
        with self.e._write_db() as db:
            db.execute("insert into item_defs(campaign_id,id,name,base_price,effect_dice,tags_json,metadata_json,updated_at) values('c','ore','Ore',1,NULL,'[]','{}',?)",(self.e._now(),))
            db.execute("insert into item_defs(campaign_id,id,name,base_price,effect_dice,tags_json,metadata_json,updated_at) values('c','ingot','Ingot',2,NULL,'[]','{}',?)",(self.e._now(),))
            db.execute("insert into recipes(campaign_id,id,kind,inputs_json,output_item_id,output_qty,skill,dc,hours,station_tag,metadata_json,updated_at) values('c','smelt','smith',?,'ingot',1,NULL,10,1,NULL,'{}',?)",(self.e._dumps({'ore':2}),self.e._now()))
        self.e.set_inventory_item('c','npc','n1','ore',2)
        self.e.world_systems_dispatch('execute_recipe','c',{'recipe_id':'smelt','owner_kind':'npc','owner_id':'n1'})
        inv={x['item_id']:x['qty'] for x in self.e.get_inventory_items('c','npc','n1')};self.assertEqual(inv['ore'],0);self.assertEqual(inv['ingot'],1)

if __name__=='__main__':unittest.main()
