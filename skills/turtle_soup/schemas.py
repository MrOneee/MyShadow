from activity_runtime.contracts import obj

TEXT={'type':'string','minLength':1,'maxLength':1800}
FACT=obj({'id':{'type':'string','minLength':1,'maxLength':24},'text':{'type':'string','minLength':1,'maxLength':100},
          'weight':{'type':'integer','minimum':1,'maximum':100},'core':{'type':'boolean'}})
PUZZLE=obj({'id':{'type':'string','minLength':1,'maxLength':64},'title':{'type':'string','minLength':1,'maxLength':30},
    'surface':{'type':'string','minLength':1,'maxLength':1000},'solution':{'type':'string','minLength':1,'maxLength':1200},'difficulty':{'type':'string','enum':['入门','中等','进阶']},
    'facts':{'type':'array','minItems':3,'maxItems':7,'items':FACT},
    'canon':{'type':'array','minItems':2,'maxItems':12,'items':TEXT},
    'hints':{'type':'array','minItems':3,'maxItems':3,'items':{'type':'string','minLength':1,'maxLength':160}},
    'source':obj({'title':{'type':'string','minLength':1,'maxLength':120},'url':{'type':'string','minLength':1,'maxLength':600},'note':{'type':'string','minLength':1,'maxLength':400}})})
JUDGMENT=obj({'relevant':{'type':'boolean'},
    'answers':{'type':'array','maxItems':4,'items':obj({
        'quote':{'type':'string','minLength':1,'maxLength':700},
        'verdict':{'type':'string','enum':['yes','no','irrelevant','unknown','clarify']}})},
    'evidence':{'type':'array','maxItems':7,'items':obj({'fact_id':{'type':'string','maxLength':24},'quote':{'type':'string','minLength':1,'maxLength':700}})}})
VERIFIED=obj({'analysis':{'type':'string','maxLength':1800},'accepted':{'type':'array','maxItems':7,'items':{'type':'string','maxLength':24}}})
CORRECTIONS=obj({'corrections':{'type':'array','maxItems':3,'items':obj({
    'question':{'type':'string','minLength':1,'maxLength':4000},
    'verdict':{'type':'string','enum':['yes','no','irrelevant','unknown']}})}})
REVIEW=obj({'approved':{'type':'boolean'},'logic':{'type':'integer','minimum':0,'maximum':5},
    'fairness':{'type':'integer','minimum':0,'maximum':5},'playability':{'type':'integer','minimum':0,'maximum':5},
    'issues':{'type':'array','maxItems':8,'items':TEXT}})
PLAYABILITY=obj({'analysis':{'type':'string','maxLength':1800},'surface_fact_ids':{'type':'array','maxItems':7,'items':{'type':'string','maxLength':24}},
    'unfair_fact_ids':{'type':'array','maxItems':7,'items':{'type':'string','maxLength':24}},
    'leaking_hint_indices':{'type':'array','maxItems':3,'items':{'type':'integer','minimum':0,'maximum':2}},
    'hint_order_valid':{'type':'boolean'},'contradictions':{'type':'array','maxItems':6,'items':TEXT}})
