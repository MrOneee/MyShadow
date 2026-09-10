import copy
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import Mock
from types import SimpleNamespace
from activity_runtime.services import Contents
from skills.turtle_soup.research import (source_documents,discover_with_harness,ResearchUnavailable,
    PreparationFailed,accept_material,canonical_url,prepare_game,attribution_mode)

class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.puzzle=json.loads(Path('skills/turtle_soup/puzzles.json').read_text(encoding='utf-8'))[0]
        self.puzzle['source']['url']='https://example.com/p?id=1'
        self.material={k:copy.deepcopy(self.puzzle[k]) for k in ('title','surface','solution','source')}
        self.criteria={'author':'许二木','theme':'','attribution':'associated'}
        self.body='公开转载：许二木讲述。\n'+self.material['surface']+'\n'+self.material['solution']
        self.sources=[{'name':'web_fetch','result':{'value':{'url':self.material['source']['url'],'statusCode':200},'content':[{'type':'text','text':self.body}]}}]
        self.result={'status':'ready','materials':[self.material],'reason':'','evidence':[
            {'url':self.material['source']['url'],'kind':k,'quote':q} for k,q in [('attribution','许二木讲述'),('surface',self.material['surface'][:20]),('solution',self.material['solution'][:20])]]}
        self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row;self.addCleanup(self.db.close)
        self.contents=Contents(self.db)
    def ctx(self,research=None,infer=None):
        return SimpleNamespace(report_progress=Mock(),infer=infer or Mock(return_value=self.criteria),contents=self.contents,
            skill=SimpleNamespace(instructions='rules',manifest={'id':'turtle_soup'}),research=research)
    def test_unavailable_is_explicit(self):
        def research(prompt,schema,validate,request):
            return validate({'status':'unavailable','materials':[],'evidence':[],'reason':'汤底页需要验证码'},[{'name':'web_fetch','result':{'isError':True}}])
        with self.assertRaisesRegex(ResearchUnavailable,'汤底页需要验证码'):discover_with_harness(self.ctx(research),'找许二木的汤')
    def test_cannot_claim_not_found_without_searching(self):
        with self.assertRaisesRegex(ValueError,'尚未执行检索'):
            accept_material({'status':'unavailable','materials':[],'reason':'未找到'},[],self.criteria)
    def test_unavailable_cannot_publish_material(self):
        with self.assertRaisesRegex(ValueError,'不能附带'):
            accept_material({'status':'unavailable','materials':[{}],'reason':'未找到'},[{}],self.criteria)
    def test_layout_and_tracking_differences_are_not_rejections(self):
        self.result['evidence'][0]['quote']='许 二木\n讲述'
        for e in self.result['evidence']:e['url']+='&utm_source=search#answer'
        found=accept_material(self.result,self.sources,self.criteria)
        self.assertIn(self.material['source']['url'],found['documents'])
        self.assertNotEqual(canonical_url('https://example.com/p?id=1'),canonical_url('https://example.com/p?id=2'))
    def test_forged_quote_is_rejected(self):
        self.result['evidence'][1]['quote']='模型编造的内容'
        with self.assertRaisesRegex(ValueError,'不能改写'):accept_material(self.result,self.sources,self.criteria)
    def test_http_error_page_not_treated_as_material(self):
        self.sources[0]['result']['value']['statusCode']=403
        self.assertEqual(source_documents(self.sources),{})
    def test_source_check_does_not_run_models(self):
        found=accept_material(self.result,self.sources,self.criteria)
        self.assertEqual(found['materials'],[self.material])
    def test_creator_name_alone_does_not_require_original_authorship(self):
        self.assertEqual(attribution_mode('找个许二木的海龟汤玩玩','许二木'),'associated')
        self.assertEqual(attribution_mode('找许二木讲过的汤','许二木'),'associated')
        self.assertEqual(attribution_mode('找作者许二木的原创题','许二木'),'original')
        self.assertEqual(attribution_mode('找许二木的汤，不要求原创','许二木'),'associated')
    def test_one_review_accepts_warnings_and_normalizes_weights(self):
        draft=copy.deepcopy(self.puzzle)
        for fact in draft['facts']:fact['weight']=10
        infer=Mock(side_effect=[draft,{'blockers':[],'warnings':['措辞可以更自然']}])
        p=prepare_game(self.ctx(infer=infer),accept_material(self.result,self.sources,self.criteria),self.criteria)
        self.assertEqual(sum(f['weight'] for f in p['facts']),100);self.assertEqual(infer.call_count,2)
        self.assertEqual(len(self.contents.list('turtle_soup')),1)
    def test_repair_does_not_search_or_repeat_source_review(self):
        infer=Mock(side_effect=[copy.deepcopy(self.puzzle),{'blockers':[{'kind':'rules','detail':'提示需要递进'}],'warnings':[]},copy.deepcopy(self.puzzle),{'blockers':[],'warnings':[]}])
        ctx=self.ctx(research=Mock(),infer=infer)
        prepare_game(ctx,accept_material(self.result,self.sources,self.criteria),self.criteria)
        self.assertEqual(infer.call_count,4);ctx.research.assert_not_called()
    def test_cached_material_survives_preparation_failure(self):
        def research(prompt,schema,validate,request):return validate(copy.deepcopy(self.result),self.sources)
        bad={'blockers':[{'kind':'rules','detail':'计分重复'}],'warnings':[]}
        infer=Mock(side_effect=[self.criteria,copy.deepcopy(self.puzzle),bad,copy.deepcopy(self.puzzle),bad])
        ctx=self.ctx(Mock(side_effect=research),infer)
        with self.assertRaises(PreparationFailed):discover_with_harness(ctx,'找许二木的汤')
        ctx.infer=Mock(side_effect=[self.criteria,copy.deepcopy(self.puzzle),{'blockers':[],'warnings':[]}])
        discover_with_harness(ctx,'找许二木的汤')
        self.assertEqual(ctx.research.call_count,1)
    def test_missing_core_material_is_still_blocking(self):
        infer=Mock(side_effect=[copy.deepcopy(self.puzzle),{'blockers':[{'kind':'source','detail':'原文没有汤底'}],'warnings':[]}])
        with self.assertRaises(ResearchUnavailable):prepare_game(self.ctx(infer=infer),accept_material(self.result,self.sources,self.criteria),self.criteria)
        self.assertEqual(self.contents.list('turtle_soup'),[])

    def test_hint_repair_only_replaces_hints(self):
        replacement=['可以问问人物的行动是否受限？','可以问问环境的变化是否有关？','可以问问什么改变了他的判断？']
        infer=Mock(side_effect=[copy.deepcopy(self.puzzle),{'blockers':[{'kind':'rules','field':'hints','detail':'第三条透露答案'}],'warnings':[]},
                               {'hints':replacement},{'blockers':[],'warnings':[]}])
        result=prepare_game(self.ctx(infer=infer),accept_material(self.result,self.sources,self.criteria),self.criteria)
        self.assertEqual(result['hints'],replacement);self.assertEqual(result['solution'],self.puzzle['solution'])
        self.assertEqual(set(infer.call_args_list[2].args[4]['properties']),{'hints'})

if __name__=='__main__':unittest.main()
