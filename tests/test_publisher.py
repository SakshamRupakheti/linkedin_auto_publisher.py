import csv
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import linkedin_auto_publisher as p


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name)
        self.path = self.out / 'state.json'
        self.state = {'schema': 1, 'posts': []}
        self.config = p.read_json(p.ROOT / 'config.json')
        self.clock = datetime(2026, 9, 5, 10, tzinfo=timezone.utc)
        self.articles = [dict(title='A new development in ' + topic, url='https://example.org/' + topic,
                             published_at=self.clock.isoformat(), source=source, category=topic)
                         for topic, source in [('World', 'BBC'), ('Science', 'NASA'), ('Sport', 'Guardian')]]
        self.env = patch.dict(p.os.environ, {'LINKEDIN_ACCESS_TOKEN': 'test-token'}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def reserve(self):
        with patch.object(p, 'now', return_value=self.clock), patch.object(p, 'collect', return_value=(self.articles, [])):
            p.prepare(self.config, self.state, self.path, self.out, live=True)

    def test_feed_rejects_stale_future_undated_and_unsafe_links(self):
        def item(date, link='https://example.org/article'):
            return f'<item><title>Example headline</title><link>{link}</link><pubDate>{date}</pubDate></item>'
        xml = '<rss><channel>' + ''.join([
            item(format_datetime(self.clock)),
            item(format_datetime(self.clock - timedelta(days=3))),
            item(format_datetime(self.clock + timedelta(hours=3))),
            item(''), item(format_datetime(self.clock), 'javascript:alert(1)')]) + '</channel></rss>'
        self.assertEqual(len(p.parse_feed(xml.encode(), {'name':'BBC','category':'World'}, self.clock,30)),1)

    def test_duplicate_tracking_query_and_rewording(self):
        self.assertEqual(p.canonical('https://example.org/a?utm_source=feed#x'), 'https://example.org/a')
        article = dict(title='Central bank announces new interest rate decision today',url='https://one.org')
        other = dict(title='Central bank announces new interest rate decision today!',url='https://two.org')
        self.assertTrue(p.duplicate(article,[other]))

    def test_preview_never_mutates_history(self):
        with patch.object(p,'collect',return_value=(self.articles, [])):
            p.prepare(self.config,self.state,self.path,self.out)
        self.assertEqual(self.state['posts'],[])
        self.assertFalse((self.out/'attempt.json').exists())
        self.assertTrue((self.out/'draft.txt').exists())

    def test_publish_uses_organization_and_records_id(self):
        self.reserve()
        with patch.object(p,'now',return_value=self.clock), patch.object(p,'fetch',return_value=(201,{'x-restli-id':'urn:li:share:1'},b'')) as send:
            p.publish(self.config,self.state,self.path,self.out)
        self.assertEqual(send.call_args.args[2]['author'],'urn:li:organization:143606150')
        self.assertEqual(p.read_json(self.path)['posts'][0]['status'],'published')
        self.assertEqual(self.state['posts'][0]['linkedin_post_id'],'urn:li:share:1')
        with self.assertRaises(RuntimeError), patch.object(p,'fetch') as send:
            p.publish(self.config,self.state,self.path,self.out)
        send.assert_not_called()

    def test_timeout_persists_uncertainty_and_blocks_next_run(self):
        self.reserve()
        with patch.object(p,'now',return_value=self.clock), patch.object(p,'fetch',side_effect=URLError('timeout')):
            with self.assertRaisesRegex(RuntimeError,'uncertain'):
                p.publish(self.config,self.state,self.path,self.out)
        self.assertEqual(p.read_json(self.path)['posts'][0]['status'],'uncertain')
        with self.assertRaisesRegex(RuntimeError,'Unresolved'), patch.object(p,'collect') as feeds:
            p.prepare(self.config,self.state,self.path,self.out,True)
        feeds.assert_not_called()

    def test_401_records_failure_without_leaking_response(self):
        self.reserve()
        error=HTTPError('https://api.linkedin.com',401,'Unauthorized',{},io.BytesIO(b'secret-body'))
        with patch.object(p,'now',return_value=self.clock), patch.object(p,'fetch',side_effect=error):
            with self.assertRaisesRegex(RuntimeError,'401') as raised:
                p.publish(self.config,self.state,self.path,self.out)
        self.assertNotIn('secret-body',str(raised.exception))
        self.assertEqual(self.state['posts'][0]['status'],'not_published')

    def test_daily_limit_clears_old_attempt_without_fetching(self):
        self.reserve()
        post=self.state['posts'][0]
        post['status']='published'
        self.state['posts'].append(dict(post,id='second'))
        with patch.object(p,'now',return_value=self.clock), patch.object(p,'collect') as feeds:
            p.prepare(self.config,self.state,self.path,self.out,True)
        feeds.assert_not_called()
        self.assertFalse((self.out/'attempt.json').exists())

    def test_no_fresh_news_fails_without_reservation(self):
        with patch.object(p,'collect',return_value=([], [])), self.assertRaisesRegex(RuntimeError,'No fresh'):
            p.prepare(self.config,self.state,self.path,self.out,True)
        self.assertEqual(self.state['posts'],[])

    def test_history_prevents_republishing(self):
        self.state['posts']=[dict(status='published',articles=self.articles)]
        self.assertEqual(p.select(self.articles,self.state,self.config),[])

    def test_topic_rotation_and_publisher_diversity(self):
        self.state['posts']=[dict(status='published',articles=[dict(self.articles[0],title='Old world headline',url='https://old.org')])]
        chosen=p.select(self.articles,self.state,self.config)
        self.assertNotEqual(chosen[0]['category'],'World')
        self.assertEqual(len({a['source'] for a in chosen}),len(chosen))

    def test_analytics_permission_denial_is_unavailable_not_zero(self):
        error=HTTPError('https://api.linkedin.com',403,'Forbidden',{},None)
        with patch.object(p,'fetch',side_effect=error), self.assertRaises(RuntimeError):
            p.analytics(self.config,self.out)
        data=p.read_json(self.out/'analytics.json')
        self.assertEqual(data['status'],'unavailable')
        self.assertNotIn('metrics',data)
        p.report(self.state,self.out)
        self.assertIn('Unavailable metrics are not zero',(self.out/'report.md').read_text())

    def test_csv_formula_is_escaped(self):
        self.reserve()
        self.state['posts'][0]['articles'][0]['title']='=HYPERLINK("bad")'
        p.report(self.state,self.out)
        with (self.out/'posts.csv').open(encoding='utf-8-sig',newline='') as file:
            rows=list(csv.DictReader(file))
        self.assertTrue(rows[0]['title'].startswith("'="))


if __name__ == '__main__':
    unittest.main()
