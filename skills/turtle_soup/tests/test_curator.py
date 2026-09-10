import copy
import json
import unittest
from pathlib import Path
from unittest.mock import Mock
from skills.turtle_soup.curator import discover_one, audit

class CuratorTests(unittest.TestCase):
    def setUp(self):
        self.puzzle=json.loads((Path(__file__).resolve().parents[1]/'puzzles.json').read_text(encoding='utf-8'))[0]
        self.ctx=Mock()
        self.ctx.trace="test"
        self.ctx.skill.manifest={'id':'turtle_soup'}
        self.ctx.search.return_value={'summary':'检索资料 '+self.puzzle['source']['url']}
        self.review={'approved':True,'logic':5,'fairness':5,'playability':5,'issues':[]}
        self.checks={'analysis':'逐项检查无缺陷','surface_fact_ids':[],'unfair_fact_ids':[],
                     'leaking_hint_indices':[],'hint_order_valid':True,'contradictions':[]}

    def test_reviewed_web_content_is_saved_and_returns_same_identity(self):
        self.ctx.infer.side_effect=[copy.deepcopy(self.puzzle),self.review,self.checks]
        result=discover_one(self.ctx,'公开关键词')
        saved=self.ctx.contents.save.call_args.args
        self.assertEqual(saved[-1],'approved')
        self.assertEqual(saved[2]['id'],result['id'])
        self.assertTrue(result['id'].startswith('web_'))

    def test_rejected_draft_is_revised_and_independently_reviewed(self):
        bad=dict(self.checks,leaking_hint_indices=[0])
        self.ctx.infer.side_effect=[copy.deepcopy(self.puzzle),self.review,bad,
                                   copy.deepcopy(self.puzzle),self.review,self.checks]
        discover_one(self.ctx,'公开关键词')
        self.assertEqual([x.args[-1] for x in self.ctx.contents.save.call_args_list],['rejected','approved'])
        self.assertEqual(self.ctx.infer.call_count,6)

    def test_high_scores_never_override_a_leaking_hint(self):
        bad=dict(self.checks,leaking_hint_indices=[0])
        self.ctx.infer.side_effect=[copy.deepcopy(self.puzzle),self.review,bad,
                                   copy.deepcopy(self.puzzle),self.review,bad]
        with self.assertRaises(RuntimeError):discover_one(self.ctx,'公开关键词')
        self.assertEqual([x.args[-1] for x in self.ctx.contents.save.call_args_list],['rejected','rejected'])

    def test_unretrieved_source_is_rejected_before_approval(self):
        self.ctx.search.return_value={'summary':'没有该来源'}
        self.ctx.infer.return_value=copy.deepcopy(self.puzzle)
        with self.assertRaises(ValueError):discover_one(self.ctx,'公开关键词')
        self.ctx.contents.save.assert_not_called()

    def test_search_failure_never_becomes_model_invented_content(self):
        self.ctx.search.return_value={'error':'search_unavailable'}
        with self.assertRaises(RuntimeError):discover_one(self.ctx,'公开关键词')
        self.ctx.infer.assert_not_called()

    def test_invalid_weights_or_duplicate_ids_cannot_enter_catalog(self):
        p=copy.deepcopy(self.puzzle);p['facts'][0]['weight']+=1
        with self.assertRaises(ValueError):audit(p)
        p=copy.deepcopy(self.puzzle);p['facts'][1]['id']=p['facts'][0]['id']
        with self.assertRaises(ValueError):audit(p)

    def test_author_without_source_evidence_is_rejected(self):
        from skills.turtle_soup.curator import check_criteria
        self.ctx.infer.return_value={'matches':True,'quote':'凭空编造的作者署名'}
        with self.assertRaises(RuntimeError):
            check_criteria(self.ctx,self.puzzle,'无署名资料',{'theme':'校园','author':'张三'})

    def test_matching_author_quote_is_grounded(self):
        from skills.turtle_soup.curator import check_criteria
        self.ctx.infer.return_value={'matches':True,'quote':'原作者：张三'}
        check_criteria(self.ctx,self.puzzle,'校园故事 原作者：张三',{'theme':'校园','author':'张三'})

    def test_queries_carry_both_requirements_and_retry_without_substitution(self):
        from skills.turtle_soup.curator import discover
        from unittest.mock import patch
        self.ctx.infer.return_value={'theme':'校园','author':'张三'}
        with patch('skills.turtle_soup.curator.discover_one',side_effect=[RuntimeError('rejected'),self.puzzle]) as one:
            self.assertEqual(discover(self.ctx,'找张三写的校园海龟汤'),self.puzzle)
        self.assertEqual(one.call_count,2)
        for call in one.call_args_list:
            self.assertIn('校园',call.args[1]);self.assertIn('张三',call.args[1])
            self.assertEqual(call.args[2],{'theme':'校园','author':'张三'})

    def test_queries_fit_search_limits_and_do_not_require_unspecified_author(self):
        from skills.turtle_soup.curator import discover
        from unittest.mock import patch
        for criteria in ({'theme':'日常生活','author':''},{'theme':'a'*50,'author':'b'*50}):
            self.ctx.infer.return_value=criteria
            with patch('skills.turtle_soup.curator.discover_one',side_effect=[RuntimeError('retry'),self.puzzle]) as one:
                discover(self.ctx,'request')
            for call in one.call_args_list:
                self.assertLessEqual(len(call.args[1]),200)
                if not criteria['author']:self.assertIn('不要求追溯最初作者',call.args[1])
