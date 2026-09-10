import json
import time
from unittest.mock import patch
from test_activity_runtime import ActivityFixture


class GameTests(ActivityFixture):
    def judge(self,text,facts=(),verdict='yes',accepted=None,sender='player'):
        answers={'relevant':True,'answers':[{'quote':text,'verdict':verdict}],
                 'evidence':[{'fact_id':f,'quote':text} for f in facts]}
        self.model.call.side_effect=[answers]+([{'accepted':list(facts if accepted is None else accepted)}] if verdict in ('yes','no') else [])
        self.say(text,sender=sender)

    def test_full_multiplayer_game_reveals_after_threshold(self):
        self.say('来一局海龟汤')
        self.judge('他很矮吗',['height'],sender='甲')
        self.assertEqual(self.state()[1]['progress'],20)
        self.judge('他够不到十楼按钮，只够得到较低按钮吗',['button'],sender='乙')
        self.assertEqual(self.state()[1]['progress'],55)
        self.judge('邻居替他按十楼吗',['neighbor'],sender='丙')
        self.assertEqual(self.state()[1]['progress'],70)
        self.assertNotIn('【汤底】',self.last())
        self.judge('下雨有长伞，可以用伞够到十楼按钮',['umbrella'],sender='甲')
        self.assertEqual(self.state()[0]['phase'],'ended')
        self.assertEqual(self.state()[1]['progress'],100)
        self.assertIn('【汤底】',self.last())
        self.assertIn('乙',self.last());self.assertIn('出处',self.last())

    def test_repeated_question_does_not_call_model_or_score_twice(self):
        self.say('来一局海龟汤');self.judge('他很矮吗',['height'])
        self.model.call.reset_mock();self.say('他很矮吗',sender='另一个玩家')
        self.model.call.assert_not_called();self.assertEqual(self.state()[1]['progress'],20)

    def test_unverified_fact_is_not_scored_or_published(self):
        self.say('来一局海龟汤');self.judge('下雨有帮助吗',['umbrella'],accepted=[])
        self.assertEqual(self.state()[1]['progress'],0)
        self.assertNotIn('长伞',self.last())

    def test_unknown_fact_and_fabricated_quote_cannot_raise_progress(self):
        self.say('来一局海龟汤')
        self.model.call.side_effect=[{'relevant':True,'answers':[{'quote':'他很矮','verdict':'yes'}],
                                      'evidence':[{'fact_id':'umbrella','quote':'不存在于消息'}]},
                                     {'accepted':['unknown_fact']}]
        self.say('他很矮')
        self.assertEqual(self.state()[1]['progress'],0)
        self.assertNotIn('没有计入进度',self.last())
        self.model.call.side_effect=None
        self.model.call.return_value={'relevant':True,'answers':[{'quote':'凭空补充','verdict':'yes'}],'evidence':[]}
        self.say('这个呢')
        self.assertIn('没有计入进度',self.last())

    def test_hint_never_scores_and_cooldown(self):
        self.say('来一局海龟汤');self.say('提示',sender='player')
        self.assertEqual(self.state()[1]['hint_count'],1);self.assertEqual(self.state()[1]['progress'],0)
        self.say('提示',sender='player')
        self.assertEqual(self.state()[1]['hint_count'],1)
        self.assertIn('二十秒',self.last())

    def test_three_hints_then_no_hidden_fourth(self):
        self.say('来一局海龟汤')
        for _ in range(3):
            s,p,v=self.state();v['hint_at']=0
            self.db.execute('UPDATE activity_sessions SET private=? WHERE id=?',(json.dumps(v),s['id']));self.db.commit()
            self.say('提示')
        s,p,v=self.state();v['hint_at']=0
        self.db.execute('UPDATE activity_sessions SET private=? WHERE id=?',(json.dumps(v),s['id']));self.db.commit()
        self.say('提示')
        self.assertEqual(self.state()[1]['hint_count'],3);self.assertIn('三层提示',self.last())

    def test_owner_can_finish_without_claiming_solved(self):
        self.say('来一局海龟汤');self.say('结束游戏')
        self.assertEqual(self.state()[1]['progress'],0)
        self.assertIn('【汤底】',self.last());self.assertNotIn('这锅破了',self.last())

    def test_choosing_then_second_puzzle(self):
        self.say('找几个高质量海龟汤')
        self.assertEqual(self.state()[1]['stage'],'choosing')
        self.assertNotIn('打嗝',self.last())
        self.say('第二个',sender='player');self.assertEqual(self.state()[1]['stage'],'choosing')
        self.say('第二个');self.assertEqual(self.state()[1]['puzzle_id'],'water_thanks')

    def test_failed_search_does_not_fake_a_web_puzzle(self):
        self.host.search=type('Search',(),{'query':lambda self,q:{'error':'unavailable'}})()
        self.model.call.return_value={'theme':'','author':''}
        self.say('联网搜一个海龟汤')
        self.assertEqual(self.state()[1]['stage'],'choosing')
        self.assertIn('尚未开局',self.last())
        self.say('用题库开局');self.assertEqual(self.state()[1]['stage'],'playing')

    def test_open_question_returns_clarification_without_hidden_answer(self):
        self.say('来一局海龟汤');self.judge('到底为什么',['umbrella'],verdict='clarify',accepted=[])
        self.assertNotIn('长伞',self.last());self.assertIn('判断问题',self.last())

    def test_unrelated_addressed_message_returns_to_normal_queue(self):
        from activity_runtime.contracts import Message
        self.say('来一局海龟汤')
        self.model.call.return_value={'relevant':False,'answers':[],'evidence':[]}
        with self.db:self.host.ingest('group',Message('weather','player','玩家','北京天气',int(time.time()),True),'real',77)
        self.host.process('group')
        row=self.db.execute("SELECT * FROM replies WHERE shard='real'").fetchone()
        self.assertEqual(row['status'],'pending')
        self.assertEqual(self.state()[0]['version'],1)

    def test_progress_does_not_expose_undiscovered_fact_names(self):
        self.say('来一局海龟汤');self.say('进度')
        self.assertNotIn('按钮',self.last());self.assertNotIn('伞',self.last())

    def test_natural_pause_resume_and_hint(self):
        self.say('来一局海龟汤');self.say('先暂停一下')
        self.assertEqual(self.state()[0]['phase'],'paused')
        self.say('继续吧');self.assertEqual(self.state()[0]['phase'],'active')
        self.say('给个提示吧');self.assertEqual(self.state()[1]['hint_count'],1)

    def test_recheck_corrects_cached_wrong_answer(self):
        self.say('来一局海龟汤');self.judge('他个子很矮吗',verdict='no')
        self.model.call.side_effect=[{'corrections':[{'question':'他个子很矮吗','verdict':'yes'}]}]
        self.say('你刚才是不是判错了？')
        self.assertIn('更正',self.last())
        self.model.call.reset_mock();self.say('他个子很矮吗')
        self.model.call.assert_not_called();self.assertIn('：是',self.last())

    def test_direction_request_is_member_hint_without_model_or_score(self):
        self.say('来一局海龟汤')
        self.model.call.reset_mock()
        self.say('给个方向吧',sender='player')
        self.assertIn('提问方向',self.last())
        self.assertIn('可以',self.last())
        self.assertEqual(self.state()[1]['progress'],0)
        self.model.call.assert_not_called()


    def test_author_and_theme_reach_search_from_new_and_choosing(self):
        request='找一个作者张三写的校园主题海龟汤'
        with patch.object(__import__('sys').modules['installed_skills.turtle_soup'],'discover',side_effect=RuntimeError('no match')) as discover:
            self.say(request)
            self.assertEqual(discover.call_args.args[1],request)
            self.assertEqual(self.state()[1]['stage'],'choosing')
            self.assertIn('符合选题要求',self.last())
            self.say('搜索作者李四的医院主题',sender='player')
            self.assertEqual(discover.call_count,1)
            self.intent_model.return_value=('search',0)
            self.say('搜索作者李四的医院主题')
            self.assertEqual(discover.call_args.args[1],'搜索作者李四的医院主题')

    def test_hint_phrases_and_pause(self):
        self.say('来一局海龟汤')
        for text in ('不知道问什么','给点思路','提示一下'):
            s,p,v=self.state();v['hint_at']=0
            self.db.execute('UPDATE activity_sessions SET private=? WHERE id=?',(json.dumps(v),s['id']));self.db.commit()
            self.say(text,sender='player')
            self.assertIn('提问方向',self.last())
        self.say('暂停游戏')
        self.say('提示',sender='player')
        self.assertEqual(self.state()[1]['hint_count'],3)
        self.assertEqual(self.state()[0]['phase'],'paused')

    def test_reveal_boundary_without_all_core_facts(self):
        from installed_skills.turtle_soup.progress import solved
        puzzle={'facts':[{'id':'a','weight':80,'core':False},{'id':'b','weight':1,'core':False},
                         {'id':'c','weight':19,'core':True}]}
        self.assertFalse(solved(puzzle,{'a':{}}))
        self.assertTrue(solved(puzzle,{'a':{},'b':{}}))

    def test_reveal_at_85_with_undiscovered_core_fact(self):
        self.say('来一局海龟汤')
        s,p,v=self.state()
        # The unguessed neighbor fact is core in this saved puzzle.
        for fact in v['puzzle']['facts']:fact['core']=True
        with self.db:self.db.execute('UPDATE activity_sessions SET private=? WHERE id=?',(json.dumps(v),s['id']))
        self.judge('他很矮，够不着十楼按钮，下雨用长伞按十楼',['height','button','umbrella'])
        self.assertEqual(self.state()[1]['progress'],85)
        self.assertNotIn('neighbor',self.state()[2]['discovered'])
        self.assertEqual(self.state()[0]['phase'],'ended')
        self.assertIn('【汤底】',self.last())

    def test_semantic_commands_use_same_state_machine_and_permissions(self):
        self.say('来一局海龟汤')
        self.intent_model.return_value=('end',0)
        self.say('不想盘了，直接告诉我们真相吧',sender='player')
        self.assertEqual(self.state()[0]['phase'],'active')
        self.assertNotIn('【汤底】',self.last())
        self.intent_model.return_value=('pause',0)
        self.say('等我接个电话，一会儿回来接着玩')
        self.assertEqual(self.state()[0]['phase'],'paused')
        self.intent_model.return_value=('rules',0)
        self.say('想继续的话应该怎么跟你说？')
        self.assertEqual(self.state()[0]['phase'],'paused')
        self.intent_model.return_value=('resume',0)
        self.say('回来了，咱们接着盘')
        self.assertEqual(self.state()[0]['phase'],'active')
        self.intent_model.return_value=('hint',0)
        self.say('实在没头绪了，给大家指条路',sender='player')
        self.assertEqual(self.state()[1]['hint_count'],1)
        self.intent_model.return_value=('end',0)
        self.say('今天就到这里吧')
        self.assertEqual(self.state()[0]['phase'],'ended')

    def test_ambiguous_negated_quoted_commands_never_fast_execute(self):
        self.say('来一局海龟汤')
        for text in ('不要结束游戏','先别揭晓汤底','怎么结束游戏？','他说“结束游戏”了吗？','如果我说不玩了会怎样'):
            self.intent_model.return_value=('rules',0)
            self.intent_model.reset_mock()
            self.say(text)
            self.intent_model.assert_called_once()
            self.assertEqual(self.state()[0]['phase'],'active')
            self.assertNotIn('【汤底】',self.last())

    def test_semantic_selection_and_controls(self):
        self.say('找几个海龟汤')
        self.intent_model.return_value=('select',2)
        self.say('就那个跟喝水有关的吧')
        self.assertEqual(self.state()[1]['puzzle_id'],'water_thanks')
        for intent,text,expected in (('surface','上面题目刷没了，再贴一下',''),
                ('progress','咱们已经推到哪一步了','解读进度')):
            self.intent_model.return_value=(intent,0);self.say(text)
            self.assertIn(expected or self.state()[1]['surface'],self.last())
        self.intent_model.return_value=('change',0)
        self.say('这锅有点熟，整一道别的吧')
        self.assertNotEqual(self.state()[1]['puzzle_id'],'water_thanks')

    def test_intent_failure_does_not_reveal_or_advance(self):
        self.say('来一局海龟汤');version=self.state()[0]['version']
        self.intent_model.side_effect=RuntimeError('timeout')
        self.say('今天就到这里吧')
        self.assertEqual(self.state()[0]['version'],version)
        self.assertNotIn('【汤底】',self.last())

    def test_intent_model_receives_no_hidden_data_or_permission(self):
        from skills.turtle_soup.commands import semantic_intent
        from activity_runtime.contracts import Message
        self.say('来一局海龟汤');s,p,v=self.state()
        ctx=self.host.context(self.registry.get('turtle_soup'),s,Message('intent','player','玩家','今天就到这里',0))
        self.model.call.return_value={'intent':'end','choice':0}
        self.assertEqual(semantic_intent(ctx,{**p,'secret':'hidden'},'今天就到这里'),('end',0))
        data=self.model.call.call_args.args[2]
        self.assertNotIn('private',data)
        self.assertEqual(set(data['public']),{'stage','title'})
        self.assertNotIn(v['puzzle']['solution'],json.dumps(data,ensure_ascii=False))
        self.assertFalse(ctx.permitted('end'))
        self.model.call.return_value={'intent':'invented','choice':0}
        with self.assertRaises(ValueError):semantic_intent(ctx,p,'hello')
