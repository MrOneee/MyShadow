def score(puzzle,discovered):
    return sum(f['weight'] for f in puzzle['facts'] if f['id'] in discovered)


def solved(puzzle,discovered):
    return score(puzzle,discovered)>80


def record(puzzle,discovered,evidence,event,accepted):
    known={f['id'] for f in puzzle['facts']}
    added=[]
    for item in evidence:
        ident=item['fact_id']
        if ident in known and ident in accepted and ident not in discovered and item['quote'] in event.text:
            discovered[ident]={'sender':event.sender,'name':event.name,'message':event.key,'quote':item['quote']}
            added.append(ident)
    return added
