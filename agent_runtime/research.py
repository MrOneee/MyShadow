"""Small, explicit routing and output contracts for read-only material tasks."""
import re

def material_request(messages):
    text=next((m.get('content','') for m in reversed(messages) if m.get('role')=='user'),'')
    if not isinstance(text,str):return False
    text=text.partition('\n当前提问：')[2] if '\n当前提问：' in text else text
    # Leave combined requests with external effects on the ordinary chat path.
    if re.search(r'定时|提醒我|安排|取消任务|发.*表情|表情包',text):return False
    return bool(re.search(r'(?:查|搜|找|整理|总结|汇总|统计|对比|比较).{0,30}(?:资料|来源|网页|历史|记录|群聊)|(?:历史|记录|群聊).{0,30}(?:查|整理|总结|汇总|统计|对比)',text))

HISTORY_OUTPUT={'type':'object','properties':{
    'error':{'type':'string'},'scope':{'type':'string'},'timezone':{'type':'string'},
    'start':{'type':'string'},'end_exclusive':{'type':'string'},'note':{'type':'string'},
    'partial':{'type':'boolean'},'more_matches':{'type':'boolean'},
    'messages':{'type':'array','items':{'type':'object','properties':{
        'sender':{'type':'string'},'time':{'type':'string'},'type':{'type':'string'},
        'text':{'type':'string'},'truncated':{'type':'boolean'}},
        'required':['sender','time','type','text','truncated'],'additionalProperties':False}}},
    'additionalProperties':False}
