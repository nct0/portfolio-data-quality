import unittest,sys,pathlib,csv,json
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
import clean_orders
from decimal import Decimal
class TestPipeline(unittest.TestCase):
 @classmethod
 def setUpClass(cls): cls.metrics=clean_orders.run()
 def test_all_rows_accounted_for(self): self.assertTrue(self.metrics['unique_source_row_accounting'])
 def test_quality_exceptions_generated(self):
  self.assertGreaterEqual(self.metrics['rejected_rows'],90)
  self.assertGreaterEqual(self.metrics['duplicate_occurrences'],20)
 def test_no_duplicate_ids(self):
  with (ROOT/'data/processed/clean_orders.csv').open(encoding='utf-8-sig',newline='') as f: rows=list(csv.DictReader(f))
  self.assertEqual(len(rows),len(set(r['order_id'] for r in rows)))
  self.assertTrue(all(Decimal(r['amount_usd'])>0 and int(r['quantity'])>0 for r in rows))
 def test_revenue_reconciles(self):
  with (ROOT/'data/processed/clean_orders.csv').open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
  expected=sum((Decimal(r['amount_usd']) for r in rows if r['status']=='Completed'),Decimal('0.00'))
  self.assertEqual(expected,Decimal(self.metrics['completed_revenue_usd']))
 def test_repeatable_outputs(self):
  first=(ROOT/'data/processed/clean_orders.csv').read_bytes()
  clean_orders.run()
  self.assertEqual(first,(ROOT/'data/processed/clean_orders.csv').read_bytes())
if __name__=='__main__':unittest.main()
