import copy, datetime as dt, json, pathlib, tempfile, unittest
from unittest.mock import patch
import exporter as e

class FeedSafety(unittest.TestCase):
 def setUp(self):
  self.products={'1':{'id':1,'handle':'regalo','title':'Geschenkbox','body_html':'<p>Transparente Box 15 × 15 cm.</p>','tags':['grossisti'],'vendor':'Unknown','images':[{'src':'https://cdn.shopify.com/red.jpg','variant_ids':[11]},{'src':'https://cdn.shopify.com/blue.jpg','variant_ids':[12]}],'variants':[{'id':11,'title':'Default Title','price':'99.60','sku':'NEW','available':True}]}}
  self.allow={'verified_at':dt.datetime.now(dt.timezone.utc).isoformat(),'products':{'1':{'handle':'regalo','variants':{'11':{'shipping_group':'grossisti','sku':'OLD','barcode':'8058641655693'}}}}}
  self.facts={'1':{'id':1,'tags':['grossisti'],'variants':[{'id':11,'price':220,'barcode':'8058641917081','available':False,'featured_image':{'src':'https://cdn.shopify.com/red-fresh.jpg'}}]}}
  self.ex={'retail_product_ids':[],'paused_product_ids':[],'paused_barcodes':[]}
 def rows(self):return e.make_rows(self.products,self.allow,self.ex,'de','2026-11-14T05:00Z',self.facts)[0]
 def test_live_variant_facts_override_stale_stock_price_gtin_and_image(self):
  r,=self.rows();self.assertEqual(r['availability'],'out_of_stock');self.assertEqual(r['price'],'2.20 EUR');self.assertEqual(r['gtin'],'8058641917081');self.assertEqual(r['image_link'],'https://cdn.shopify.com/red-fresh.jpg');self.assertNotIn('blue.jpg',r['additional_image_link']);self.assertNotIn('brand',r);self.assertNotIn('mpn',r)
 def test_retail_and_paused_can_never_be_restored(self):
  self.ex['retail_product_ids']=['1'];self.assertEqual(self.rows(),[])
  self.ex['retail_product_ids']=[];self.ex['paused_product_ids']=['1'];self.assertEqual(self.rows(),[])
  self.ex['paused_product_ids']=[];self.facts['1']['tags']=['reparto-retail'];self.assertEqual(self.rows(),[])
 def test_banned_supplier_is_excluded_after_snapshot(self):
  self.products['1']['tags']=['fornitore-progress-plastic'];self.assertEqual(self.rows(),[])
 def test_existing_google_policy_hold_cannot_be_resubmitted_under_a_new_locale(self):
  self.ex['policy_hold_product_ids']=['1']
  for locale in ['de','en','fr','es','nl']:
   rows,skipped=e.make_rows(self.products,self.allow,self.ex,locale,'2026-11-14T05:00Z',self.facts)
   self.assertEqual(rows,[]);self.assertEqual(skipped,[{'id':'1','reason':'unresolved_Google_policy_hold'}])
 def test_minimum_order_copy_and_chinese_are_not_republished(self):
  self.products['1']['body_html']='Mindestbestellmenge: 12';self.assertEqual(self.rows(),[])
  self.products['1']['body_html']='中文';self.assertEqual(self.rows(),[])
 def test_exact_free_only_and_shipping_groups(self):
  r,=self.rows();self.assertEqual(r['included_destination'],'Free_listings');self.assertIn('Shopping_ads',r['excluded_destination']);self.assertNotIn('YouTube_Shopping',r['excluded_destination']);self.assertEqual(r['shipping_label'],'gsp_grossisti')
  self.allow['products']['1']['variants']['11']['shipping_group']='no_free';self.assertEqual(self.rows()[0]['shipping_label'],'gsp_no_free')
 def test_invalid_or_restricted_gtin_not_replaced_with_sku(self):
  self.facts['1']['variants'][0]['barcode']='9999999999999';r,=self.rows();self.assertEqual(r['gtin'],'');self.assertNotIn('identifier_exists',r)
  for x in ['123','0000000000000','8058641917082','0200000000000','0400000000000','2000000000000','0500000000000','9800000000000']:
   self.assertEqual(e.gtin(x),'')
 def test_pagination_requires_successful_eof_and_rejects_duplicates(self):
  def fetch(url):
   page=int(url.rsplit('=',1)[-1]);return {'products':[{'id':page*2+i}for i in range(2)]if page==1 else []}
  result,pages=e.catalog('de',fetch,page_size=2);self.assertEqual(len(result),2);self.assertEqual(pages,2)
  with self.assertRaises(e.FeedError):e.catalog('de',lambda u:{'products':[{'id':1},{'id':2}]},page_size=2,max_pages=4)
  with self.assertRaises(e.FeedError):e.catalog('de',lambda u:{'error':'temporary'},page_size=2)
 def test_network_failure_does_not_overwrite_or_extend_previous_feed(self):
  with tempfile.TemporaryDirectory()as d:
   root=pathlib.Path(d);(root/'feeds').mkdir();old='old valid content, expires at fixed old time';(root/'feeds/de.tsv').write_text(old)
   (root/'allowlist.json').write_text(json.dumps(self.allow));(root/'exclusions.json').write_text(json.dumps(self.ex));(root/'config.json').write_text(json.dumps({'allowlist_max_age_days':30,'expiration_hours':48,'enabled_locales':['de'],'enabled_shipping_groups':['grossisti'],'minimum_rows':1,'max_missing_public_fraction':0.01}))
   with self.assertRaises(e.FeedError):e.generate(root,fetch=lambda u:(_ for _ in()).throw(e.FeedError('network failure')))
   self.assertEqual((root/'feeds/de.tsv').read_text(),old)
 def test_live_deployment_expiry_exceeds_three_day_google_warning_window(self):
  config=json.loads((pathlib.Path(__file__).parents[1]/'config.json').read_text());self.assertEqual(config['expiration_hours'],96);self.assertGreater(config['expiration_hours'],72);self.assertEqual(config['allowlist_max_age_days'],3)
 def test_three_day_authority_limit_fails_before_any_public_fetch(self):
  with tempfile.TemporaryDirectory()as d:
   root=pathlib.Path(d);self.allow['verified_at']=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(days=4)).isoformat();(root/'allowlist.json').write_text(json.dumps(self.allow));(root/'exclusions.json').write_text(json.dumps(self.ex));(root/'config.json').write_text(json.dumps({'allowlist_max_age_days':3,'expiration_hours':48}))
   with self.assertRaises(e.FeedError):e.generate(root,fetch=lambda url:self.fail('Stale authority must fail before live fetch'))
 def test_expired_google_publication_allowlist_is_not_self_renewed(self):
  with tempfile.TemporaryDirectory()as d:
   root=pathlib.Path(d);self.allow['verified_at']=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(days=31)).isoformat();(root/'allowlist.json').write_text(json.dumps(self.allow));(root/'exclusions.json').write_text(json.dumps(self.ex));(root/'config.json').write_text(json.dumps({'allowlist_max_age_days':30,'expiration_hours':48}))
   with self.assertRaises(e.FeedError):e.generate(root)

if __name__=='__main__':unittest.main()
