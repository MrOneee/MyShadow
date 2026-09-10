"""Trusted contact discovery and conversation identifiers shared by host and UI."""
import re

SYSTEM_CONTACTS = {'weixin','filehelper','fmessage','medianote','notifymessage',
                   'newsapp','qqmail','qqsync','floatbottle','lbsapp','shakeapp','voipapp'}


def direct_id(value):
    return (isinstance(value,str) and bool(re.fullmatch(r'[A-Za-z0-9_-]{3,128}',value))
            and value not in SYSTEM_CONTACTS and not value.startswith('gh_'))


def conversation_id(value):
    return isinstance(value,str) and (bool(re.fullmatch(r'[0-9]+@chatroom',value)) or direct_id(value))


def direct_contacts(connection, config):
    if not config.get('private_messages',{}).get('enabled',False):return {}
    result={}
    rows=connection.execute('SELECT username,remark,nick_name FROM contact WHERE local_type=1 '
                            'AND coalesce(delete_flag,0)=0 AND coalesce(verify_flag,0)=0 '
                            'AND (flag & 3)=3 AND (flag & 8)=0')
    for row in rows:
        user=row['username'];name=(row['remark'] or row['nick_name'] or '').strip()
        if direct_id(user) and user!=config['bot_id'] and name:
            result[user]={'group_id':user,'group_name':name,'conversation_type':'direct',
                          'select_unique_result':True}
    return result
