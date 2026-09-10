"""Small typed boundary shared by trusted, installed skill packages."""
from dataclasses import dataclass, field
import copy
import json


def validate(value, schema, path='$'):
    kind=schema.get('type')
    types={'object':dict,'array':list,'string':str,'integer':int,'number':(int,float),'boolean':bool,'null':type(None)}
    if kind and (not isinstance(value,types[kind]) or kind in ('integer','number') and isinstance(value,bool)):
        raise ValueError(path+': wrong type')
    if 'enum' in schema and value not in schema['enum']:raise ValueError(path+': invalid choice')
    if isinstance(value,dict):
        props=schema.get('properties',{})
        if set(schema.get('required',[]))-value.keys():raise ValueError(path+': missing fields')
        if schema.get('additionalProperties') is False and value.keys()-props.keys():raise ValueError(path+': unknown fields')
        for key,item in value.items():
            if key in props:validate(item,props[key],path+'.'+key)
    if isinstance(value,list):
        if not schema.get('minItems',0)<=len(value)<=schema.get('maxItems',10000):raise ValueError(path+': array size')
        for item in value:validate(item,schema.get('items',{}),path+'[]')
    if isinstance(value,str) and not schema.get('minLength',0)<=len(value)<=schema.get('maxLength',100000):raise ValueError(path+': string size')
    if type(value) in (int,float) and not schema.get('minimum',float('-inf'))<=value<=schema.get('maximum',float('inf')):raise ValueError(path+': numeric range')
    return value


def obj(properties,required=None):
    return {'type':'object','properties':properties,'required':list(properties) if required is None else required,'additionalProperties':False}


@dataclass(frozen=True)
class Message:
    key:str
    sender:str
    name:str
    text:str
    created:int
    addressed:bool=False
    kind:str='message'
    sort_seq:int=0


@dataclass
class Turn:
    public:dict
    private:dict
    phase:str='active'
    messages:list=field(default_factory=list)
    timers:list=field(default_factory=list)
    handled:bool=True
    action:str='respond'
    audience:str='public'
    audit:dict=field(default_factory=dict)


def clone(value):
    return copy.deepcopy(value)


def bounded_json(value,limit=250000):
    text=json.dumps(value,ensure_ascii=False,allow_nan=False)
    if len(text)>limit:raise ValueError('Activity state exceeds size limit')
    return text


def progress_text(percent,lines=(),label='进度'):
    if type(percent) is not int or not 0<=percent<=100:raise ValueError('Invalid progress')
    filled=percent//10
    return label+'：'+ '■'*filled+'□'*(10-filled)+f' {percent}%' + ('\n已确认：'+'；'.join(lines) if lines else '')
