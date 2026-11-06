"""Calendar meanings of literal relative phrases, never inferred event dates."""
import re
from datetime import date,datetime,timedelta

NUMBERS=dict(zip('one two three four five six seven eight nine ten eleven twelve'.split(),range(1,13)))
NUMBERS.update({'a':1,'an':1})
CHINESE=dict(zip('一二三四五六七八九',range(1,10)))
CHINESE['两']=2
PATTERN=re.compile(r'\b(?:today|yesterday|this month|last month|this year|last year)\b|'
    r'\b(?P<n>\d{1,5}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|a|an)\s+(?P<unit>days?|weeks?)\s+ago\b|'
    r'昨天|今天|本月|这个月|上个月|今年|去年|(?<!\d)(?P<cn>\d{1,5}|[一二三四五六七八九十两]{1,3})(?P<cn_unit>天|周|星期)前',re.I)


def references(text,asserted_at):
    try:anchor=datetime.fromisoformat(asserted_at).date()
    except (TypeError,ValueError):return {}
    result={}
    for m in PATTERN.finditer(text):
        phrase=m.group();key=phrase.lower()
        if key in ('today','今天'):value=anchor.isoformat()
        elif key in ('yesterday','昨天'):
            if anchor==date.min:continue
            value=(anchor-timedelta(days=1)).isoformat()
        elif key in ('this month','本月','这个月'):value=anchor.strftime('%Y-%m')
        elif key in ('last month','上个月'):
            if anchor.replace(day=1)==date.min:continue
            value=(anchor.replace(day=1)-timedelta(days=1)).strftime('%Y-%m')
        elif key in ('this year','今年'):value=str(anchor.year)
        elif key in ('last year','去年'):
            if anchor.year==1:continue
            value=str(anchor.year-1)
        else:
            number=(m.group('n') or m.group('cn')).lower()
            if number.isdigit():count=int(number)
            elif number in NUMBERS:count=NUMBERS[number]
            elif number in CHINESE:count=CHINESE[number]
            elif '十' in number:
                left,right=number.split('十',1)
                if left and left not in CHINESE or right and right not in CHINESE:continue
                count=10*(CHINESE[left] if left else 1)+(CHINESE[right] if right else 0)
            else:continue
            unit=m.group('unit') or m.group('cn_unit')
            days=count*(1 if unit.lower() in ('day','days','天') else 7)
            if days>(anchor-date.min).days:continue
            value=(anchor-timedelta(days=days)).isoformat()
        result[phrase]=value
        if len(result)>=4:break
    return result
