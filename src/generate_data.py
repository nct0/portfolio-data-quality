"""Generate deterministic, fake and intentionally messy e-commerce transactions."""
import csv, random, pathlib, datetime
from decimal import Decimal
ROOT=pathlib.Path(__file__).resolve().parents[1]
RAW=ROOT/'data'/'raw'; REF=ROOT/'data'/'reference'
random.seed(20261108)

def save(path,headers,rows):
    with path.open('w', newline='',encoding='utf-8-sig') as f:
        w=csv.writer(f); w.writerow(headers); w.writerows(rows)

products=[('SKU-001','Notebook',12.50),('SKU-002','Desk Lamp',35.00),('SKU-003','USB Cable',7.25),('SKU-004','Travel Mug',18.00),('SKU-005','Backpack',49.90),('SKU-006','Keyboard',63.00),('SKU-007','Mouse',22.00),('SKU-008','Phone Stand',11.00)]
save(REF/'products.csv',['sku','product_name','unit_price_usd'],products)
save(REF/'channels.csv',['channel','region'],[['Web','North'],['Marketplace','Central'],['Retail','South']])
rows=[]
for n in range(1,5001):
    sku,name,price=random.choice(products)
    qty=random.randint(1,5)
    order_date=datetime.date(2026,1,1)+datetime.timedelta(days=random.randint(0,180))
    date_style=random.choice(['iso','us','dmy'])
    date_value=order_date.isoformat() if date_style=='iso' else order_date.strftime('%m/%d/%Y' if date_style=='us' else '%d-%b-%Y')
    channel=random.choice(['Web','Marketplace','Retail'])
    status=random.choices(['Completed','Refunded','Pending'],weights=[85,8,7])[0]
    amt=round(price*qty,2)
    rows.append([f'ORD-{n:06d}',f'C-{random.randint(1,900):04d}',date_value,random.choice([sku,sku.lower(),f' {sku} ']),str(qty),f'{amt:.2f}',random.choice([channel, channel.lower(),f' {channel} ']),random.choice([status,status.lower(),f' {status} '])])
# Controlled flaws, each affects a unique source record and can be audited.
byid={r[0]:r for r in rows}
for i in range(11,21): byid[f'ORD-{i:06d}'][5]='9999.99'  # amount mismatch
for i in range(21,31): byid[f'ORD-{i:06d}'][4]='0' # invalid quantity
for i in range(31,41): byid[f'ORD-{i:06d}'][3]='SKU-INVALID' # invalid sku
for i in range(41,51): byid[f'ORD-{i:06d}'][2]='not-a-date'
for i in range(51,61): byid[f'ORD-{i:06d}'][6]='UNKNOWN'
for i in range(61,71): byid[f'ORD-{i:06d}'][7]='MISSING'
for i in range(71,81): byid[f'ORD-{i:06d}'][1]='' # missing customer
for i in range(81,91): byid[f'ORD-{i:06d}'][5]='n/a' # invalid amount
for i in range(91,101): byid[f'ORD-{i:06d}'][0]='' # missing order id
# duplicate first 15 as-is and next 5 with modified amount => conflict
rows.extend([list(rows[i]) for i in range(100,115)])
for i in range(115,120):
    duplicate=list(rows[i]); duplicate[1]='C-9999'; rows.append(duplicate)
random.shuffle(rows)
headers=['order_id','customer_id','order_date','sku','quantity','amount_usd','channel','status']
# split as three CSV exports (like a real client sending multiple reports)
for j in range(3): save(RAW/f'orders_export_{j+1}.csv',headers,rows[j::3])
print(f'Generated {len(rows)} input rows from 5000 base orders, across three exports')
