import unittest
import exporter as e
import test_exporter as fixtures

class LocaleOnboarding(unittest.TestCase):
 setUp=fixtures.FeedSafety.setUp
 rows=fixtures.FeedSafety.rows
 def test_approved_current_translation_only(self):
  p=self.products['1']
  self.ex['locale_copy_approvals']={'1':{'fr':e.localized_copy_digest(p['title'],p['body_html'])}}
  rows,skipped=e.make_rows(self.products,self.allow,self.ex,'fr','2026-11-25T05:00Z',self.facts)
  self.assertEqual(len(rows),1); self.assertEqual(skipped,[])
  for lang in ['en','de','es','nl']:
   rows,skipped=e.make_rows(self.products,self.allow,self.ex,lang,'2026-11-25T05:00Z',self.facts)
   self.assertEqual(rows,[]);self.assertEqual(skipped,[{'id':'1','reason':'locale_not_reviewed'}])
 def test_italian_fallback_or_later_copy_edit_cannot_pass(self):
  p=self.products['1'];self.ex['locale_copy_approvals']={'1':{'fr':e.localized_copy_digest('Boîte cadeau','Boîte transparente 15 × 15 cm.')}}
  for title,body in [('Scatola regalo','Scatola trasparente 15 × 15 cm.'),('Boîte cadeau','Nouveaux faits non vérifiés')]:
   p.update(title=title,body_html=body)
   rows,skipped=e.make_rows(self.products,self.allow,self.ex,'fr','2026-11-25T05:00Z',self.facts)
   self.assertEqual(rows,[]);self.assertEqual(skipped[0]['reason'],'reviewed_localized_copy_changed')
 def test_policy_hold_keeps_precedence(self):
  p=self.products['1'];self.ex['locale_copy_approvals']={'1':{'de':e.localized_copy_digest(p['title'],p['body_html'])}}
  self.ex['policy_hold_product_ids']=['1']
  rows,skipped=e.make_rows(self.products,self.allow,self.ex,'de','2026-11-25T05:00Z',self.facts)
  self.assertEqual(rows,[]);self.assertEqual(skipped[0]['reason'],'unresolved_Google_policy_hold')
 def test_empty_locale_map_holds_all_five(self):
  self.ex['locale_copy_approvals']={'1':{}}
  for lang in ['en','de','fr','es','nl']:
   self.assertEqual(e.make_rows(self.products,self.allow,self.ex,lang,'2026-11-25T05:00Z',self.facts)[0],[])
 def test_malformed_registry_never_fails_open(self):
  for invalid in [None,[],{'1':None},{'1':{'fr':'bad'}},{'1':{'it':'a'*64}}]:
   self.ex['locale_copy_approvals']=invalid
   with self.assertRaises(e.FeedError):self.rows()
 def test_legacy_without_registry_unchanged(self):
  before=self.rows();self.ex['locale_copy_approvals']={};self.assertEqual(before,self.rows())

if __name__=='__main__':unittest.main()
