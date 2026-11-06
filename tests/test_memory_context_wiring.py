"""Exercise the actual Bot.messages_for body without Linux desktop imports."""
import ast
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace, MethodType
import unittest
from unittest.mock import Mock


class ContextBudgetExceeded(RuntimeError):pass


def bot_method():
    tree=ast.parse((Path(__file__).resolve().parents[1]/'myshadow/bot.py').read_text(encoding='utf-8'))
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Bot')
    method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='messages_for')
    ns={'json':json,'sqlite3':sqlite3,'reply_style':lambda *args:None,
        'estimate_tokens':lambda messages:len(messages[0]['content']), 'ContextBudgetExceeded':ContextBudgetExceeded}
    exec(compile(ast.Module(body=[method],type_ignores=[]),'Bot.messages_for','exec'),ns)
    return ns['messages_for']


class ContextWiringTests(unittest.TestCase):
    def fixture(self,v2=True,budget=10000):
        social=Mock()
        social.context.side_effect=lambda *args,**kwargs:'essential' if kwargs.get('compact') else 'retrieved '*100
        bot=SimpleNamespace(memory_v2=v2,social=social,require_group=lambda g:g,
            image_context_for=lambda t:'',context_for=lambda t:([],'alice'),memory_for=lambda g:'',
            trigger_anchor=lambda t:{'sender':'alice'},related_senders=lambda *args:[],
            input_budget_for=lambda t:budget,persona_for=lambda g:{},
            message_payload=lambda group,history,sender,prompt,summary,image,social,plan:[{'role':'user','content':social}])
        bot.messages_for=MethodType(bot_method(),bot)
        return bot,{'group_id':'group','id':1,'prompt':'之前讨论过的配方是什么？'}

    def test_v2_receives_current_question_before_model_runtime(self):
        bot,trigger=self.fixture();messages,_=bot.messages_for(trigger)
        bot.social.context.assert_called_once_with('group','alice',[],query=trigger['prompt'])
        self.assertIn('retrieved',messages[0]['content'])

    def test_legacy_social_memory_does_not_receive_new_keyword(self):
        bot,trigger=self.fixture(False);bot.messages_for(trigger)
        bot.social.context.assert_called_once_with('group','alice',[])

    def test_budget_falls_back_to_essential_without_query(self):
        bot,trigger=self.fixture(budget=100);messages,_=bot.messages_for(trigger)
        self.assertEqual(messages[0]['content'],'essential')
        self.assertEqual(bot.social.context.call_args.kwargs,{'compact':'essential'})


if __name__=='__main__':unittest.main()
