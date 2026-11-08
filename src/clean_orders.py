"""Auditable CSV cleaning: normalize, validate, deduplicate and reconcile."""
import csv, pathlib, datetime, json
from collections import Counter,defaultdict
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
ROOT=pathlib.Path(__file__).resolve().parents[1]
RAW=ROOT/'data'/'raw'; REF=ROOT/'data'/'reference'; OUT=ROOT/'data'/'processed'
CENT=Decimal('0.01')

def read(path):
    with path.open(newline='',encoding='utf-8-sig') as f:return list(csv.DictReader(f))
def write(filename,rows,fields):
    with (OUT/filename).open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
def money(s):
    try: v=Decimal(str(s).strip().replace(',',''))
    except (InvalidOperation,ValueError): return None
    if not v.is_finite():return None
    return v.quantize(CENT,rounding=ROUND_HALF_UP)
def parse_date(s):
    for fmt in ('%Y-%m-%d','%m/%d/%Y','%d-%b-%Y'):
        try:return datetime.datetime.strptime(s.strip(),fmt).date().isoformat()
        except ValueError:pass
    return None

def run():
    OUT.mkdir(parents=True,exist_ok=True)
    products={r['sku']:r for r in read(REF/'products.csv')}
    channels={r['channel'].lower():r['channel'] for r in read(REF/'channels.csv')}
    statuses={'completed':'Completed','refunded':'Refunded','pending':'Pending'}
    source=[]
    for path in sorted(RAW.glob('orders_export_*.csv')):
        for line,row in enumerate(read(path),start=2):
            source.append((path.name,line,row))
    if not source:raise RuntimeError('No source CSV files found. Run generate_data.py first.')
    accepted=[]; rejected=[]; duplicates=[]; seen={}; issues=Counter(); totals=defaultdict(lambda:{'orders':0,'amount_usd':Decimal('0.00')})
    for filename,line,r in source:
        x={k:(v or '').strip() for k,v in r.items()}
        order=x['order_id'].upper(); sku=x['sku'].upper(); date=parse_date(x['order_date'])
        channel=channels.get(x['channel'].lower());status=statuses.get(x['status'].lower())
        problems=[]
        if not order:problems.append('MISSING_ORDER_ID')
        if not x['customer_id']:problems.append('MISSING_CUSTOMER_ID')
        if not date:problems.append('INVALID_DATE')
        if sku not in products:problems.append('UNKNOWN_SKU')
        try: qty=int(x['quantity']);assert qty>0
        except (ValueError,AssertionError):qty=0;problems.append('INVALID_QUANTITY')
        amount=money(x['amount_usd'])
        if amount is None or amount<0:problems.append('INVALID_AMOUNT')
        if not channel:problems.append('UNKNOWN_CHANNEL')
        if not status:problems.append('UNKNOWN_STATUS')
        if sku in products and qty>0 and amount is not None:
            expected=(Decimal(products[sku]['unit_price_usd'])*qty).quantize(CENT)
            if abs(amount-expected)>CENT:problems.append('AMOUNT_MISMATCH')
        normalized={'order_id':order,'customer_id':x['customer_id'].upper(),'order_date':date or '', 'sku':sku,'quantity':qty,'amount_usd':str(amount) if amount is not None else '', 'channel':channel or '', 'status':status or ''}
        if problems:
            for p in problems:issues[p]+=1
            rejected.append({'source_file':filename,'source_line':line,'order_id':order,'reasons':'|'.join(problems),**{k:r[k] for k in r}})
            continue
        fingerprint=tuple(normalized.values())
        if order in seen:
            classification='EXACT_DUPLICATE' if fingerprint==seen[order] else 'CONFLICTING_DUPLICATE'
            issues[classification]+=1
            duplicates.append({'source_file':filename,'source_line':line,'order_id':order,'classification':classification})
            # Prevent ambiguous order IDs from appearing in the clean output without review.
            if classification=='CONFLICTING_DUPLICATE':
                # Mark all accepted prior versions of this order as blocked too.
                seen[order]=None
            continue
        seen[order]=fingerprint
        accepted.append(normalized)
    conflicting_ids={d['order_id'] for d in duplicates if d['classification']=='CONFLICTING_DUPLICATE'}
    # If conflicting duplicates arise, quarantine the entire order ID. Exact duplicates are safely ignored.
    accepted=[r for r in accepted if r['order_id'] not in conflicting_ids]
    # Recalculate only from accepted, completed orders (refunds and pending remain in clean detail).
    for r in accepted:
        if r['status']!='Completed':continue
        key=(r['channel'],r['order_date'][:7])
        totals[key]['orders']+=1
        totals[key]['amount_usd']+=Decimal(r['amount_usd'])
    accepted.sort(key=lambda x:x['order_id'])
    rejected.sort(key=lambda x:(x['source_file'],x['source_line']))
    summary=[{'channel':k[0],'month':k[1],'completed_orders':v['orders'],'revenue_usd':str(v['amount_usd'].quantize(CENT))} for k,v in sorted(totals.items())]
    fields=['order_id','customer_id','order_date','sku','quantity','amount_usd','channel','status']
    write('clean_orders.csv',accepted,fields)
    write('rejected_rows.csv',rejected,['source_file','source_line','order_id','reasons',*fields])
    write('duplicates_review.csv',duplicates,['source_file','source_line','order_id','classification'])
    write('monthly_revenue.csv',summary,['channel','month','completed_orders','revenue_usd'])
    metrics={'input_rows':len(source),'accepted_rows':len(accepted),'rejected_rows':len(rejected),'duplicate_occurrences':len(duplicates),'conflicting_order_ids':len(conflicting_ids),'quarantined_valid_rows':len(conflicting_ids),'unique_source_row_accounting':len(source)==len(accepted)+len(rejected)+len(duplicates)+len(conflicting_ids),'completed_order_count':sum(r['completed_orders'] for r in summary),'completed_revenue_usd':str(sum((Decimal(r['revenue_usd']) for r in summary),Decimal('0.00'))),'issues':dict(sorted(issues.items())),'notes':['Only Completed orders count toward revenue.','Exact duplicates are ignored; all versions of conflicting order IDs are quarantined from clean data.','No currency conversion; all amounts are USD.','Revenue is gross amount for Completed orders, before taxes or fees.']}
    (OUT/'quality_report.json').write_text(json.dumps(metrics,indent=2),encoding='utf-8')
    print(json.dumps(metrics,indent=2))
    return metrics
if __name__=='__main__':run()
