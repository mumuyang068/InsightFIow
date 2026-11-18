"""Research adapter following the shipped R11 CTE and E03 repurchase function."""
from collections import defaultdict
from datetime import datetime, timezone, timedelta

TZ=timezone(timedelta(hours=8))

def calculate(orders,start,end,asof,window,net,cohort_filters,repeat_filters, *, diagnostic=False):
    unique={}
    for order in orders:
        oid=order['id']
        if oid in unique and unique[oid]!=order:
            raise ValueError('conflicting_order_id:'+oid)
        unique[oid]=order
    grouped=defaultdict(list)
    for o in unique.values():
        grouped[o['customer']].append(o)
    trace=[]
    for customer, history in sorted(grouped.items()):
        history=sorted(history,key=lambda o:(o['t'],o['id']))
        valid=[]
        rejected=[]
        for o in history:
            why=('after_observation' if o['t']>asof else
                 'ineligible_payment' if o['purpose']!='normal' or o['amount']<=0 else
                 'nonpositive_net' if net and o['amount']-o['refund']<=0 else None)
            if why:rejected.append({'id':o['id'],'reason':why})
            else:valid.append(o)
        # Eligibility is evaluated on the full available order history before cohort filtering.
        if diagnostic:
            # B03 replay: the isolated test applies the cohort channel to every eligible row.
            channel = cohort_filters.get("channel")
            valid = [order for order in valid if not channel or order.get("channel") == channel]
        first=valid[0] if valid else None
        row={'customer':customer,'first_order':first,'excluded_orders':rejected,
             'eligible_order_ids':[o['id'] for o in valid],'in_cohort':False,'mature':None,'repeat_orders':[]}
        if not first: row['exclusion']='no_qualifying_order'
        elif not start<=first['t']<end: row['exclusion']='first_order_outside_cohort'
        elif any(first.get(k)!=v for k,v in cohort_filters.items()):row['exclusion']='first_order_channel_not_selected'
        else:
            deadline=first['t']+window
            row.update(in_cohort=True,mature=deadline<=asof,deadline=deadline,exclusion=None)
            row['repeat_orders']=[o for o in valid if first['t']<o['t']<=deadline and
                                  all(o.get(k)==v for k,v in repeat_filters.items())]
        trace.append(row)
    mature=[r for r in trace if r['mature']]
    repeated=[r['customer'] for r in mature if r['repeat_orders']]
    return {'numerator':len(repeated),'denominator':len(mature),
            'rate_pct':round(100*len(repeated)/len(mature),6) if mature else None,
            'immature':sum(r['in_cohort'] and r['mature'] is False for r in trace),
            'mature_members':[r['customer'] for r in mature],'repeated_members':repeated,
            'raw_rows':len(orders),'unique_rows':len(unique),'trace':trace}
