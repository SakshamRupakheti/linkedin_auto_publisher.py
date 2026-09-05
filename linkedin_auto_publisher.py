"""World & Human RSS publisher. Python 3.11+, no third-party dependencies."""
import argparse
import csv
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sys
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
UTC = timezone.utc

def now():
    return datetime.now(UTC)

def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temp.replace(path)

def clean(text):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]*>', '', text or ''))).strip()

def canonical(url):
    p = urlsplit(url.strip())
    if p.scheme not in ('http', 'https') or not p.hostname or p.username:
        raise ValueError('Invalid article URL')
    query = [(k,v) for k,v in parse_qsl(p.query) if not k.lower().startswith('utm_')
             and k.lower() not in ('fbclid','gclid','at_medium','at_campaign')]
    return urlunsplit((p.scheme,p.netloc.lower(),p.path,urlencode(query),''))

def duplicate(article, others):
    norm = lambda s: ' '.join(re.findall(r'\w+', s.casefold()))
    return any(article['url'] == a['url'] or SequenceMatcher(None,norm(article['title']),norm(a['title'])).ratio() >= .86 for a in others)

def fetch(url, headers=None, payload=None):
    req = Request(url, headers={'User-Agent':'WorldAndHumanNews/1.0', **(headers or {})},
                  data=None if payload is None else json.dumps(payload).encode())
    with urlopen(req, timeout=25) as response:
        body = response.read(5_000_001)
        if len(body) > 5_000_000:
            raise ValueError('Response exceeds 5 MB')
        return response.status, response.headers, body

def parse_feed(body, source, clock, max_age):
    if b'<!DOCTYPE' in body.upper() or b'<!ENTITY' in body.upper():
        raise ValueError('Unsupported XML declaration')
    root = ET.fromstring(body)
    articles = []
    for item in root.findall('./channel/item'):
        try:
            title = clean(item.findtext('title'))
            url = canonical(item.findtext('link',''))
            date = parsedate_to_datetime(item.findtext('pubDate',''))
            if date.tzinfo is None or not title or len(title.split()) > 25 or len(title) > 240:
                continue
            if not clock - timedelta(hours=max_age) <= date <= clock + timedelta(minutes=5):
                continue
            articles.append(dict(title=title,url=url,published_at=date.astimezone(UTC).isoformat(),
                                 source=source['name'],category=source['category']))
        except (ValueError, TypeError, AttributeError, OverflowError):
            continue
    return articles

def collect(config, clock):
    articles, health = [], []
    for source in config['feeds']:
        try:
            _, _, body = fetch(source['url'])
            items = parse_feed(body,source,clock,config['max_age_hours'])
            articles.extend(items)
            health.append(dict(source=source['name'],category=source['category'],fresh_articles=len(items),status='ok'))
        except (HTTPError, URLError, TimeoutError, ValueError, ET.ParseError, OSError):
            health.append(dict(source=source['name'],category=source['category'],fresh_articles=0,status='unavailable'))
    return articles, health

def select(articles, state, config):
    history = [a for p in state['posts'] if p['status'] != 'not_published' for a in p['articles']]
    recent = [a for p in state['posts'][-14:] if p['status'] != 'not_published' for a in p['articles']]
    candidates = sorted(articles,key=lambda a:a['published_at'],reverse=True)
    candidates.sort(key=lambda a:sum(x['category']==a['category'] for x in recent))
    chosen = []
    for article in candidates:
        if any(a['source']==article['source'] or a['category']==article['category'] for a in chosen):
            continue
        if not duplicate(article,history+chosen):
            chosen.append(article)
        if len(chosen) == config['stories_per_post']:
            break
    return chosen

def compose(articles, clock):
    sections = [f'WORLD & HUMAN | News briefing | {clock:%d %b %Y}']
    for a in articles:
        sections.append(f"{a['category'].upper()} — {a['source']}\n“{a['title']}”\n{a['url']}")
    sections.append('Headlines as reported by the linked publishers. Follow the sources for context and updates.\n\n#WorldNews #WorldAndHuman')
    text = '\n\n'.join(sections)
    if len(text) > 2800:
        raise RuntimeError('Digest exceeds length limit')
    return text

def credentials(config):
    token = os.getenv('LINKEDIN_ACCESS_TOKEN','').strip()
    if not token:
        raise RuntimeError('Missing LINKEDIN_ACCESS_TOKEN. Add it to GitHub Actions secrets.')
    org = str(config['organization_id'])
    version = os.getenv('LINKEDIN_VERSION','').strip() or config['linkedin_version']
    if not re.fullmatch(r'[1-9]\d*',org) or not re.fullmatch(r'\d{6}',version):
        raise RuntimeError('Invalid organization ID or API version')
    return 'urn:li:organization:'+org, {'Authorization':'Bearer '+token,'Content-Type':'application/json',
        'LinkedIn-Version':version,'X-Restli-Protocol-Version':'2.0.0'}

def prepare(config,state,path,out,live=False):
    clock = now()
    for name in ('attempt.json','draft.json','draft.txt'):
        (out/name).unlink(missing_ok=True)
    if live:
        credentials(config)
        if any(p['status'] in ('reserved','uncertain') for p in state['posts']):
            raise RuntimeError('Unresolved attempt exists. Check the Page and resolve it before continuing.')
        today = [p for p in state['posts'] if p['created_at'][:10]==clock.date().isoformat() and p['status']!='not_published']
        sent = [datetime.fromisoformat(p['created_at']) for p in state['posts'] if p['status']=='published']
        if len(today)>=config['daily_post_limit'] or (sent and clock-max(sent)<timedelta(hours=config['minimum_interval_hours'])):
            print('Posting limit or interval reached; nothing queued.')
            return
    articles,health = collect(config,clock)
    save(out/'feed-health.json',health)
    if not articles:
        raise RuntimeError('No fresh dated news available; nothing queued.')
    chosen = select(articles,state,config)
    if not chosen:
        print('No unseen news available; nothing queued.')
        return
    if len(chosen)<config['minimum_stories']:
        raise RuntimeError('Too few fresh stories from distinct publishers; nothing queued.')
    post = dict(id=hashlib.sha256((clock.isoformat()+chosen[0]['url']).encode()).hexdigest()[:20],
        created_at=clock.isoformat(),articles=chosen,status='reserved',organization_id=str(config['organization_id']),text=compose(chosen,clock))
    save(out/'draft.json',post)
    (out/'draft.txt').write_text(post['text']+'\n',encoding='utf-8')
    if live:
        state['posts'].append(post)
        save(path,state)
        save(out/'attempt.json',{'id':post['id']})
    print('Draft ready: '+str(out/'draft.txt'))

def publish(config,state,path,out):
    if not (out/'attempt.json').exists():
        print('No new publishing attempt; nothing sent.')
        return
    attempt = read_json(out/'attempt.json')['id']
    post = next(p for p in state['posts'] if p['id']==attempt)
    if post['status']!='reserved' or post['organization_id']!=str(config['organization_id']):
        raise RuntimeError('Attempt is not reserved for this organization; it will not be resent.')
    if now()-datetime.fromisoformat(post['created_at'])>timedelta(minutes=30):
        raise RuntimeError('Draft is stale; resolve it as not published before preparing fresh news.')
    author,headers = credentials(config)
    payload = dict(author=author,commentary=post['text'],visibility='PUBLIC',
        distribution=dict(feedDistribution='MAIN_FEED',targetEntities=[],thirdPartyDistributionChannels=[]),
        lifecycleState='PUBLISHED',isReshareDisabledByAuthor=False)
    # GitHub commits reserved state before calling this. Never automatically retry a POST.
    post['status']='uncertain'
    save(path,state)
    try:
        status,response_headers,_ = fetch('https://api.linkedin.com/rest/posts',headers,payload)
        if status!=201:
            raise RuntimeError('Unexpected response. Check the Page before resolving the attempt.')
        post.update(status='published',linkedin_post_id=response_headers.get('x-restli-id',''),published_at=now().isoformat())
        save(path,state)
        print('Published to organization '+str(config['organization_id']))
    except HTTPError as exc:
        if 400<=exc.code<500 and exc.code!=408:
            post['status']='not_published'
        post['http_status']=exc.code
        save(path,state)
        hints={401:'Replace expired/invalid token.',403:'Check w_organization_social, app approval and Page role.',
               426:'Update the retired LinkedIn API version.',429:'Rate limited; wait for a later run.'}
        raise RuntimeError(f'LinkedIn HTTP {exc.code}. '+hints.get(exc.code,'Inspect request settings; check Page if outcome is uncertain.')) from None
    except (URLError,TimeoutError,OSError):
        raise RuntimeError('Publish outcome uncertain. Check the Page before resolving; automatic resend is blocked.') from None

def analytics(config,out):
    try:
        author,headers=credentials(config)
        url='https://api.linkedin.com/rest/organizationalEntityShareStatistics?'+urlencode(dict(q='organizationalEntity',organizationalEntity=author))
        _,_,body=fetch(url,headers)
        elements=json.loads(body).get('elements',[])
        metrics=elements[0].get('totalShareStatistics',{}) if elements else {}
        save(out/'analytics.json',dict(status='available' if metrics else 'no_data',collected_at=now().isoformat(),
             scope='Organization organic aggregate; API rolling 12-month availability',metrics=metrics))
    except (HTTPError,URLError,TimeoutError,OSError,ValueError,RuntimeError) as exc:
        save(out/'analytics.json',dict(status='unavailable',http_status=exc.code if isinstance(exc,HTTPError) else None,
             reason='Check token, rw_organization_admin permission, Page administrator role and API version.'))
        raise RuntimeError('Analytics unavailable; see analytics.json. Publishing history remains reportable.') from None

def report(state,out):
    posts=state['posts']
    sent=[p for p in posts if p['status']=='published']
    lines=['# World & Human — publisher report','',f'Generated: {now().isoformat()}','',f'Published digests: {len(sent)}','',
        f"Unresolved attempts: {sum(p['status'] in ('reserved','uncertain') for p in posts)}",'',
        '## Coverage','','| Topic | Published headlines |','|---|---:|']
    articles=[a for p in sent for a in p['articles']]
    for category in sorted({a['category'] for a in articles}):
        lines.append(f"| {category} | {sum(a['category']==category for a in articles)} |")
    metrics=read_json(out/'analytics.json') if (out/'analytics.json').exists() else {'status':'not_requested'}
    lines+=['','## LinkedIn organic analytics','','Status: '+metrics['status']]
    if metrics.get('metrics'):
        lines+=['',metrics['scope'],'','| Metric | Value |','|---|---:|']
        lines += [f'| {k} | {v} |' for k,v in metrics['metrics'].items()]
    else:
        lines+=['','Unavailable metrics are not zero. API analytics need separate permissions.']
    lines+=['','## Recent publishing attempts','']
    for p in reversed(posts[-20:]):
        lines.append(f"- {p['created_at']} — {p['status']} — attempt `{p['id']}`")
    lines+=['','Headlines are attributed publisher reports, not independently verified facts. Similarity filtering can miss differently worded reports of the same event.']
    (out/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    with (out/'posts.csv').open('w',encoding='utf-8-sig',newline='') as file:
        writer=csv.writer(file)
        writer.writerow(['attempt_id','created_at','status','linkedin_post_id','category','source','title','url'])
        for p in posts:
            for a in p['articles']:
                row=[p['id'],p['created_at'],p['status'],p.get('linkedin_post_id',''),a['category'],a['source'],a['title'],a['url']]
                writer.writerow(["'"+str(v) if str(v).startswith(('=','+','-','@','\t','\r')) else v for v in row])
    with (out/'metrics.csv').open('w',encoding='utf-8',newline='') as file:
        writer=csv.writer(file)
        writer.writerow(['metric','value','status'])
        for key,value in metrics.get('metrics',{}).items():
            writer.writerow([key,value,metrics['status']])
        if not metrics.get('metrics'):
            writer.writerow(['','',metrics['status']])
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a',encoding='utf-8') as file:
            file.write((out/'report.md').read_text(encoding='utf-8'))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['preview','prepare','publish','analytics','report','resolve'])
    parser.add_argument('--config',type=Path,default=ROOT/'config.json')
    parser.add_argument('--state',type=Path,default=ROOT/'state.json')
    parser.add_argument('--output',type=Path,default=ROOT/'reports')
    parser.add_argument('--attempt')
    parser.add_argument('--outcome',choices=['published','not_published'])
    parser.add_argument('--post-id',default='')
    args=parser.parse_args()
    config,state=read_json(args.config),read_json(args.state)
    if state.get('schema')!=1 or not isinstance(state.get('posts'),list):
        raise RuntimeError('Invalid state. Restore state.json; do not reset publishing history.')
    args.output.mkdir(parents=True,exist_ok=True)
    if args.command in ('preview','prepare'):
        prepare(config,state,args.state,args.output,args.command=='prepare')
    elif args.command=='publish':
        publish(config,state,args.state,args.output)
    elif args.command=='analytics':
        analytics(config,args.output)
    elif args.command=='resolve':
        if not args.attempt or not args.outcome:
            parser.error('resolve requires --attempt and --outcome after checking the Page')
        post=next((p for p in state['posts'] if p['id']==args.attempt),None)
        if not post or post['status'] not in ('reserved','uncertain'):
            raise RuntimeError('No matching unresolved attempt')
        post.update(status=args.outcome,resolved_at=now().isoformat(),linkedin_post_id=args.post_id)
        save(args.state,state)
    report(state,args.output)

if __name__=='__main__':
    try:
        main()
    except (RuntimeError,ValueError,OSError) as error:
        print('ERROR: '+str(error),file=sys.stderr)
        sys.exit(1)
