import pathlib,json,unittest,xml.etree.ElementTree as ET
import xml_feed

class ShippingContract(unittest.TestCase):
 def setUp(self):
  self.row={'id':'real-variant','title':'Produkt','description':'15 cm','link':'https://lidomarketplus.it/de/products/p?variant=1','image_link':'https://cdn.shopify.com/p.jpg','availability':'out_of_stock','price':'99.60 EUR','condition':'new','gtin':'8058641655693','shipping_label':'gsp_grossisti','included_destination':'Free_listings','excluded_destination':'Shopping_ads,Display_ads,Local_inventory_ads,Free_local_listings,Youtube_affiliate,Youtube_merchandise','expiration_date':'2026-11-14T05:00Z'}
  self.shipping=json.loads((pathlib.Path(__file__).parents[1]/'shipping.json').read_text())
 def parsed(self,row):return ET.fromstring(xml_feed.build_xml([row],'de',['DE'],self.shipping)).find('./channel/item')
 def test_high_and_low_wholesale_match_live_threshold_99_paid14(self):
  for value,expected in [('2.20 EUR','14.00 EUR'),('98.99 EUR','14.00 EUR'),('99.00 EUR','0.00 EUR'),('99.60 EUR','0.00 EUR')]:
   self.row['price']=value;item=self.parsed(self.row);shipping=item.find('{'+xml_feed.G+'}shipping');self.assertEqual(shipping.findtext('{'+xml_feed.G+'}price'),expected);self.assertEqual(shipping.findtext('{'+xml_feed.G+'}min_handling_time'),'2');self.assertEqual(shipping.findtext('{'+xml_feed.G+'}max_handling_time'),'3');self.assertEqual(shipping.findtext('{'+xml_feed.G+'}min_transit_time'),'3');self.assertEqual(item.find('{'+xml_feed.G+'}free_shipping_threshold').findtext('{'+xml_feed.G+'}price_threshold'),'99.00 EUR')
 def test_general_and_3d_never_get_free_shipping_even_above99(self):
  self.row['shipping_label']='gsp_no_free';self.row['price']='150.00 EUR';item=self.parsed(self.row);self.assertIsNone(item.find('{'+xml_feed.G+'}free_shipping_threshold'));self.assertEqual(item.find('{'+xml_feed.G+'}shipping').findtext('{'+xml_feed.G+'}price'),'14.00 EUR')
 def test_free_only_exclusions_use_valid_repeated_xml_attributes(self):
  item=self.parsed(self.row);values=[x.text for x in item.findall('{'+xml_feed.G+'}excluded_destination')];self.assertIn('Shopping_ads',values);self.assertIn('Youtube_merchandise',values);self.assertEqual(item.findtext('{'+xml_feed.G+'}included_destination'),'Free_listings')
 def test_no_italian_override_and_max25_country_guard(self):
  with self.assertRaises(ValueError):xml_feed.build_xml([self.row],'de',['IT'],self.shipping)
  with self.assertRaises(ValueError):xml_feed.build_xml([self.row],'de',['DE']*26,self.shipping)

if __name__=='__main__':unittest.main()
