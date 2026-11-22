import unittest
from urllib.parse import parse_qs, urlsplit

from exporter import FeedError, feed_catalog


def page(vendor, number=1, total=1, start_id=1):
    pages = (total + 249) // 250
    count = min(250, total - (number - 1) * 250)
    return {
        'schema': 'lido_public_feed_v1', 'language': 'en', 'currency': 'EUR',
        'page': number, 'page_size': 250, 'total': total, 'pages': pages,
        'next_page': number + 1 if number < pages else None,
        'products': [
            {'id': i, 'vendor': vendor, 'variants': [{}], 'variants_count': 1}
            for i in range(start_id, start_id + count)
        ],
    }


class VendorPartitionTests(unittest.TestCase):
    def test_multiple_vendors_and_pages_use_encoded_vendor(self):
        vendors = ['Fornitore & Caffè/+', 'Altro']

        def fetch(url):
            parts = urlsplit(url)
            self.assertEqual(parts.path, '/en/collections/vendors')
            query = parse_qs(parts.query)
            vendor, = query['q']
            number = int(query['page'][0])
            self.assertEqual(query['view'], ['lido-google-feed'])
            self.assertEqual(query['sort_by'], ['created-ascending'])
            self.assertEqual(query['currency'], ['EUR'])
            if vendor == vendors[0]:
                self.assertIn('q=Fornitore%20%26%20Caff%C3%A8%2F%2B&', parts.query)
                return page(vendor, number, 251, 1 + (number - 1) * 250)
            self.assertEqual(vendor, vendors[1])
            return page(vendor, start_id=252)

        products, pages = feed_catalog('en', fetch, public_vendor_partitions=vendors)
        self.assertEqual((len(products), pages), (252, 3))
        self.assertEqual(set(products), {str(i) for i in range(1, 253)})

    def test_legacy_path_remains_default(self):
        def fetch(url):
            self.assertIn('/en/collections/all?view=lido-google-feed&', url)
            return page('Any vendor')

        products, pages = feed_catalog('en', fetch)
        self.assertEqual((list(products), pages), (['1'], 1))

    def test_cross_partition_duplicate_fails(self):
        def fetch(url):
            return page(parse_qs(urlsplit(url).query)['q'][0])

        with self.assertRaisesRegex(FeedError, 'Duplicate'):
            feed_catalog('en', fetch, public_vendor_partitions=['One', 'Two'])

    def test_wrong_vendor_fails(self):
        with self.assertRaisesRegex(FeedError, 'Unexpected vendor'):
            feed_catalog('en', lambda _: page('Other'), public_vendor_partitions=['One'])

    def test_bad_partition_configuration_fails_before_fetch(self):
        for vendors in ([], 'One', [''], [' One'], ['One', 'One'], [1]):
            with self.subTest(vendors=vendors), self.assertRaisesRegex(FeedError, 'public_vendor_partitions'):
                feed_catalog('en', lambda _: self.fail('Unexpected fetch'), public_vendor_partitions=vendors)

    def test_partition_rejects_incomplete_or_oversized_data(self):
        cases = [
            ({'total': 25001, 'pages': 100}, 'never truncate past25000'),
            ({'pages': 2}, 'unsupported total'),
            ({'products': []}, 'Incomplete or changing feed pagination'),
            ({'next_page': 2}, 'Incomplete or changing feed pagination'),
            ({'page': 2}, 'page metadata mismatch'),
            ({'language': 'de'}, 'locale/currency/schema mismatch'),
            ({'products': [{'id': 1, 'vendor': 'One', 'variants_count': 2, 'variants': [{}]}]},
             'Variant pagination incomplete'),
        ]
        for overrides, error in cases:
            with self.subTest(overrides=overrides), self.assertRaisesRegex(FeedError, error):
                feed_catalog('en', lambda _: {**page('One'), **overrides}, public_vendor_partitions=['One'])
