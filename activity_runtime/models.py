"""Structured inference: no tool execution, bounded retries, redacted logs."""
import json
import time
from .contracts import validate


def shape(schema):
    """Show an instance shape so a model does not echo JSON Schema keywords."""
    if 'enum' in schema:return schema['enum'][0]
    kind=schema.get('type')
    if kind=='object':return {k:shape(v) for k,v in schema['properties'].items()}
    if kind=='array':return [shape(schema.get('items',{}))]
    return {'string':'填写实际值','boolean':False,'integer':0,'number':0,'null':None}.get(kind)


class StructuredModel:
    def __init__(self,ai,model=None):self.ai,self.model=ai,model or ai.config['model']

    def call(self,purpose,instructions,data,schema,*,trace='',max_tokens=1400,timeout=30,reasoning=False,reasoning_effort='low'):
        if getattr(self.ai,'__dict__',{}).get('harness') and not self.ai.harness.in_callback:
            outcome=self.ai.harness.run([{'role':'system','content':instructions},{'role':'user','content':json.dumps(data,ensure_ascii=False)}],
                purpose=purpose,key=trace,model=self.model,schema=schema,max_tokens=max_tokens,
                timeout=max(timeout,120),max_steps=6,reasoning=reasoning_effort if reasoning else 'off')
            return outcome.value
        messages=[{'role':'system','content':instructions+'\n返回任务的实际JSON数据，不返回JSON Schema本身。不输出type、properties、required等模式定义字段。'
                   '\n输出实例形状（示例值必须替换为实际判断，数组可以为空）：'+json.dumps(shape(schema),ensure_ascii=False)+
                   '\n以下仅为输出数据的校验规则，不是要你抄写的答案：'+json.dumps(schema,ensure_ascii=False)},
                  {'role':'user','content':json.dumps(data,ensure_ascii=False)}]
        started=time.monotonic()
        for attempt in range(2):
            try:
                result=self.ai.request('/chat/completions',{'model':self.model,
                    'messages':messages,'stream':False,'thinking':{'type':'enabled' if reasoning else 'disabled'},
                    **({'reasoning_effort':reasoning_effort} if reasoning else {'temperature':0}),
                    'response_format':{'type':'json_object'},'max_tokens':max_tokens},timeout=timeout,max_bytes=100000)
                value=json.loads(result['choices'][0]['message']['content'])
                validate(value,schema)
                print(json.dumps({'event':'activity_model','purpose':purpose,'trace':trace,'attempt':attempt+1,
                    'elapsed_ms':round((time.monotonic()-started)*1000),'tokens':result.get('usage',{}).get('total_tokens',0)}),flush=True)
                return value
            except (ValueError,TypeError,KeyError,IndexError) as exc:
                if attempt:raise RuntimeError('Structured model output invalid: '+purpose) from exc
                messages.append({'role':'user','content':'上次输出校验错误：'+str(exc)[:180]+'。只返回任务实际数据，不能复制校验规则。实例形状：'+json.dumps(shape(schema),ensure_ascii=False)})
            except (RuntimeError,OSError):
                if attempt:raise
        raise RuntimeError('Structured model failed')
