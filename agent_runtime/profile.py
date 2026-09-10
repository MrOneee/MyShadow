"""Pinned upstream DSH composition, extended with host-bound tools and budgets."""
from pathlib import Path
import json

VERSION='0.1.2rc1'
SOURCE_COMMIT='a66e4702047846cdaa10c66c9d3df3951f5ea70d'

def profile_patch(plugin,web=False,mode='native'):
    if mode not in ('native','ptc'):raise ValueError('Invalid tool mode')
    rows=[{'id':name,'disabled':True} for name in ('persistent-bash','persistent-pwsh','str-replace-editor')]
    extensions=[{'id':'wechat-bound-tools','name':Path(plugin).resolve().as_uri()},
                {'id':'wechat-repeat-reminder','name':'@deepseek-ai/dsh-repeat-tool-reminder','config':{'thresholds':[2,3]}},
                {'id':'wechat-tool-timeout','name':'@deepseek-ai/dsh-tool-call-timeout-policy'}]
    if mode=='ptc':
        extensions.insert(0,{'id':'wechat-code-runtime','name':Path(plugin).with_name('ptc_runtime.mjs').resolve().as_uri()})
    if web:
        extensions += [
            {'id':'wechat-token-meter','name':'@deepseek-ai/dsh-token-meter'},
            {'id':'wechat-compaction','name':'@deepseek-ai/dsh-compaction-basic',
             'config':{'thresholdRatio':0.65,'retainTokens':12000,'maxTokens':3000,'auto':True}},
            {'id':'wechat-web','name':'@deepseek-ai/dsh-web'},
            {'id':'wechat-web-search','name':'@deepseek-ai/dsh-web-search-deepseek',
             'config':{'maxUses':2,'maxTokens':3000}},
            {'id':'wechat-web-fetch','name':'@deepseek-ai/dsh-web-fetch-http',
             'config':{'maxResponseBytes':2000000,'maxBodyChars':500000,'timeoutMs':25000,'maxRedirects':3}},
            {'id':'wechat-web-tools','name':'@deepseek-ai/dsh-tool-web',
             'config':{'searchMaxQueries':2,'searchMaxResults':8,'searchTimeoutMs':90000,'fetchTimeoutMs':30000,'fetchMaxOutputChars':24000}}]
    rows += [{'id':'llm-deepseek','config':{'apiKeyEnv':'DEEPSEEK_API_KEY','defaultContextWindow':65536,
              'models':[{'id':model,'contextWindow':65536} for model in ('deepseek-v4-flash','deepseek-v4-pro')],
              'streamIdleTimeoutMs':60000}},
             {'id':'tools','config':{'mode':mode,'maxParallelSubCalls':2}},
             {'id':'agent-loop','config':{'agents':[],'maxParallelToolCalls':1}}, {'insert':extensions}]
    return json.dumps(rows,ensure_ascii=False,indent=2)
